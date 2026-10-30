"""Offline tests for ALWATHIQ-NET RouterOS adaptation; no live network."""
import asyncio
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from core import RateGate, Settings
from routeros_bot import Pending, confirm, private_allowed, router_command
from routeros_ops import (
    RouterConfig, RouterConfigError, RouterError, RouterOSClient,
    format_rows, router_read, safe_id, safe_name,
)


class RouterConfigTests(unittest.TestCase):
    def test_unconfigured_is_read_only(self):
        with patch.dict(os.environ, {}, clear=True):
            c = RouterConfig.from_env(frozenset({11}))
            self.assertFalse(c.origin)
            self.assertFalse(c.writes_enabled)

    def test_http_and_url_credentials_rejected(self):
        for url in ("http://10.10.10.1", "https://a:b@10.10.10.1",
                    "https://router.test/rest", "https://router.test?x=1"):
            with self.subTest(url=url), patch.dict(os.environ, {
                "ROUTEROS_HTTPS_URL": url,
                "ROUTEROS_USERNAME": "bot",
                "ROUTEROS_PASSWORD": "secret",
            }, clear=True):
                with self.assertRaises(RouterConfigError):
                    RouterConfig.from_env(frozenset({11}))

    def test_owner_outside_allowlist_rejected(self):
        with patch.dict(os.environ, {
            "ROUTEROS_HTTPS_URL": "https://router.test",
            "ROUTEROS_USERNAME": "bot", "ROUTEROS_PASSWORD": "secret",
            "ROUTEROS_OWNER_TELEGRAM_IDS": "99",
            "ROUTEROS_WRITES_ENABLED": "true",
        }, clear=True):
            with self.assertRaises(RouterConfigError):
                RouterConfig.from_env(frozenset({11}))

    def test_critical_operations_require_owner_and_write_flag(self):
        with patch.dict(os.environ, {
            "ROUTEROS_HTTPS_URL": "https://router.test",
            "ROUTEROS_USERNAME": "bot", "ROUTEROS_PASSWORD": "secret",
            "ROUTEROS_OWNER_TELEGRAM_IDS": "11",
            "ROUTEROS_CRITICAL_ENABLED": "true",
        }, clear=True):
            with self.assertRaises(RouterConfigError):
                RouterConfig.from_env(frozenset({11}))

    def test_account_identifiers_sanitized(self):
        self.assertEqual(safe_name("amir@hilla1"), "amir@hilla1")
        with self.assertRaises(RouterError):
            safe_name("user; /system/reboot")
        with self.assertRaises(RouterError):
            safe_id("*1/../../")
        self.assertEqual(safe_id("*1"), "%2A1")

    def test_output_control_characters_removed(self):
        value = format_rows([{"name": "test\nforged", "disabled": "false"}],
                            ("name", "disabled"), "users")
        self.assertNotIn("test\nforged", value)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_resource_is_bounded_and_authenticated(self):
        seen = []
        def fake(request):
            seen.append(request)
            return httpx.Response(200, json=[{"cpu-load": "4", "password": "SECRET"}])
        c = RouterOSClient(RouterConfig("https://router.test", "bot", "secret"),
                           transport=httpx.MockTransport(fake))
        try:
            msg = await router_read(c, "resource", [])
            self.assertIn("cpu-load: 4", msg)
            self.assertNotIn("SECRET", msg)
            self.assertEqual(len(seen), 1)
            self.assertTrue(seen[0].headers["authorization"].startswith("Basic "))
            self.assertEqual(seen[0].url.path, "/rest/system/resource")
        finally:
            await c.aclose()

    async def test_disabled_write_does_not_mutate(self):
        seen = []
        def fake(request):
            seen.append(request)
            return httpx.Response(200, json=[{".id": "*1", "name": "amir1"}])
        c = RouterOSClient(RouterConfig("https://router.test", "bot", "secret"),
                           transport=httpx.MockTransport(fake))
        try:
            with self.assertRaises(RouterError):
                await c.change("ppp", "amir1", "disable")
            self.assertEqual([x.method for x in seen], ["GET"])
        finally:
            await c.aclose()

    async def test_target_lookup_uses_record_id_not_username_path(self):
        seen = []
        def fake(request):
            seen.append(request)
            if request.method == "GET":
                return httpx.Response(200, json=[{".id": "*A", "name": "amir@hilla"}])
            return httpx.Response(200, json={".id": "*A", "disabled": "yes"})
        c = RouterOSClient(
            RouterConfig("https://router.test", "bot", "secret",
                         frozenset({11}), True),
            transport=httpx.MockTransport(fake))
        try:
            await c.change("ppp", "amir@hilla", "disable")
            self.assertEqual([x.method for x in seen], ["GET", "PATCH"])
            self.assertEqual(seen[1].url.path, "/rest/ppp/secret/*A")
            self.assertIn(b'"disabled":"yes"', seen[1].content)
        finally:
            await c.aclose()

    async def test_arbitrary_api_path_disallowed(self):
        c = RouterOSClient(RouterConfig("https://router.test", "bot", "secret"),
                           transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[])))
        try:
            with self.assertRaises(RouterError):
                await c.request("POST", "/rest/execute", data={"script": "/system/reboot"})
            with self.assertRaises(RouterError):
                await c.ping("bad-host;evil")
        finally:
            await c.aclose()

    async def test_ping_is_read_only_even_when_writes_disabled(self):
        seen = []
        def fake(req):
            seen.append(req)
            return httpx.Response(200, json=[{"host": "8.8.8.8", "time": "30ms"}])
        c = RouterOSClient(RouterConfig("https://router.test", "bot", "secret"),
                           transport=httpx.MockTransport(fake))
        try:
            result = await router_read(c, "ping", ["8.8.8.8"])
            self.assertIn("8.8.8.8", result)
            self.assertEqual(seen[0].url.path, "/rest/ping")
        finally:
            await c.aclose()


class BotGuardTests(unittest.IsolatedAsyncioTestCase):
    def make_context(self, *, writes=True, critical=False, args=None):
        seen = []
        def fake(req):
            seen.append(req)
            if req.method == "GET":
                return httpx.Response(200, json=[{".id": "*1", "name": "amir1"}])
            return httpx.Response(200, json=[])
        config = RouterConfig("https://router.test", "bot", "secret",
                              frozenset({11}), writes, critical)
        client = RouterOSClient(config, transport=httpx.MockTransport(fake))
        data = {"settings": Settings("111:test", frozenset({11})),
                "rate_gate": RateGate(0), "router_client": client,
                "router_config": config, "router_pending": {}}
        context = SimpleNamespace(application=SimpleNamespace(bot_data=data), args=args or [])
        return context, client, seen

    @staticmethod
    def make_update(user_id=11, chat_type="private"):
        message = SimpleNamespace(reply_text=AsyncMock())
        return SimpleNamespace(
            effective_user=SimpleNamespace(id=user_id),
            effective_chat=SimpleNamespace(id=user_id, type=chat_type),
            message=message,
        )

    async def test_private_owner_only(self):
        ctx, client, seen = self.make_context(args=["amir1"])
        try:
            outsider = self.make_update(12)
            await router_command(outsider, ctx, "disablepppoe")
            self.assertEqual(seen, [])
            self.assertFalse(private_allowed(outsider, ctx.application.bot_data["settings"]))
            group = self.make_update(11, "supergroup")
            await router_command(group, ctx, "disablepppoe")
            self.assertEqual(seen, [])
        finally:
            await client.aclose()

    async def test_write_requires_explicit_confirmation(self):
        ctx, client, seen = self.make_context(args=["amir1"])
        owner = self.make_update()
        try:
            await router_command(owner, ctx, "disablepppoe")
            self.assertEqual(seen, [])
            pending = ctx.application.bot_data["router_pending"][11]
            ctx.args = [pending.code]
            await confirm(owner, ctx)
            self.assertEqual([x.method for x in seen], ["GET", "PATCH"])
            await confirm(owner, ctx)
            self.assertEqual([x.method for x in seen], ["GET", "PATCH"])
        finally:
            await client.aclose()

    async def test_expired_confirmation_fails_closed(self):
        ctx, client, seen = self.make_context(args=["expired"])
        owner = self.make_update()
        ctx.application.bot_data["router_pending"][11] = Pending(
            "reboot", "", "code", time.monotonic() - 1)
        ctx.args = ["code"]
        try:
            await confirm(owner, ctx)
            self.assertFalse(seen)
        finally:
            await client.aclose()

    async def test_no_reboot_without_critical_flag(self):
        ctx, client, seen = self.make_context(args=[])
        try:
            await router_command(self.make_update(), ctx, "reboot")
            self.assertFalse(ctx.application.bot_data["router_pending"])
            self.assertFalse(seen)
        finally:
            await client.aclose()


if __name__ == "__main__":
    unittest.main()
