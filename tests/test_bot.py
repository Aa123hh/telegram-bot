"""Offline security and contract tests. No Telegram network and no router access."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from bot import is_authorized
from core import (BrainClient, BrainError, ConfigurationError, RateGate,
                  Settings, summarize_diagnostic, validate_url)


class ConfigurationTests(unittest.TestCase):
    def test_missing_user_allowlist_fails_closed(self):
        with patch.dict(os.environ, {"BOT_TOKEN": "123:test", "ALLOWED_TELEGRAM_USER_IDS": ""}):
            with self.assertRaises(ConfigurationError):
                Settings.from_env()

    def test_allowlist_and_loopback(self):
        with patch.dict(os.environ, {
            "BOT_TOKEN": "123:test", "ALLOWED_TELEGRAM_USER_IDS": "123,456",
            "ALWATHIQ_BRAIN_URL": "http://127.0.0.1:8000",
            "ALWATHIQ_BRAIN_TOKEN": "service-token",
            "ALLOW_INTERNAL_HTTP": "false",
        }):
            settings = Settings.from_env()
            self.assertEqual(settings.allowed_user_ids, frozenset({123, 456}))
            self.assertEqual(settings.brain_url, "http://127.0.0.1:8000")

    def test_public_http_disallowed(self):
        with self.assertRaises(ConfigurationError):
            validate_url("http://example.com:8000")
        with self.assertRaises(ConfigurationError):
            validate_url("http://example.com:8000", allow_internal_http=True)

    def test_credentials_in_upstream_url_disallowed(self):
        with self.assertRaises(ConfigurationError):
            validate_url("https://alice:secret@example.com")

    def test_missing_upstream_key_disallowed(self):
        with patch.dict(os.environ, {
            "BOT_TOKEN": "123:test", "ALLOWED_TELEGRAM_USER_IDS": "123",
            "ALWATHIQ_BRAIN_URL": "https://example.com",
            "ALWATHIQ_BRAIN_TOKEN": "",
        }):
            with self.assertRaises(ConfigurationError):
                Settings.from_env()

    def test_permission_strict_private_chat(self):
        settings = Settings(bot_token="123:test", allowed_user_ids=frozenset({123}))
        user = SimpleNamespace(id=123)
        self.assertTrue(is_authorized(SimpleNamespace(effective_user=user, effective_chat=SimpleNamespace(id=123, type="private")), settings))
        self.assertFalse(is_authorized(SimpleNamespace(effective_user=user, effective_chat=SimpleNamespace(id=-123, type="supergroup")), settings))
        self.assertFalse(is_authorized(SimpleNamespace(effective_user=SimpleNamespace(id=999), effective_chat=SimpleNamespace(id=999, type="private")), settings))

    def test_throttle(self):
        rate = RateGate(3)
        self.assertTrue(rate.permit(123, now=10))
        self.assertFalse(rate.permit(123, now=12))
        self.assertTrue(rate.permit(123, now=13))


class FormatTests(unittest.TestCase):
    def test_pppoe_count_never_includes_usernames(self):
        payload = {"results": [{"tool_name": "routeros.pppoe_active", "ok": True,
            "data": {"status": "ok", "data": {"session_count": 2,
                "sessions": [{"name": "subscriber-secret"}]}}}]}
        response = summarize_diagnostic(payload, "routeros.pppoe_active")
        self.assertIn("2", response)
        self.assertNotIn("subscriber-secret", response)

    def test_unconfigured_not_misreported_healthy(self):
        payload = {"results": [{"tool_name": "routeros.pppoe_active", "ok": True,
            "data": {"status": "not_configured"}}]}
        self.assertIn("not_configured", summarize_diagnostic(payload, "routeros.pppoe_active"))


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_authorized_bearer_and_allowlisted_path(self):
        seen = []
        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"status": "ready"})
        config = Settings("123:test", frozenset({123}), "https://brain.example",
                          "key-abc")
        client = BrainClient(config, transport=httpx.MockTransport(handle))
        try:
            value = await client.read("/readyz")
            self.assertEqual(value["status"], "ready")
            self.assertEqual(seen[0].headers["authorization"], "Bearer key-abc")
            with self.assertRaises(BrainError):
                await client.read("/api/v1/users")
            self.assertEqual(len(seen), 1)
        finally:
            await client.aclose()

    async def test_diagnostic_tool_allowlist(self):
        seen = []
        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(201, json={"results": []})
        config = Settings("123:test", frozenset({123}), "https://brain.example",
                          "key-abc")
        client = BrainClient(config, transport=httpx.MockTransport(handle))
        try:
            await client.diagnostic("routeros.pppoe_active")
            self.assertEqual(seen[0].method, "POST")
            self.assertIn(b"routeros.pppoe_active", seen[0].content)
            with self.assertRaises(BrainError):
                await client.diagnostic("routeros.reboot")
            self.assertEqual(len(seen), 1)
        finally:
            await client.aclose()

    async def test_http_error_no_body_leak(self):
        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="secret backend traceback")
        config = Settings("123:test", frozenset({123}), "https://brain.example",
                          "key-abc")
        client = BrainClient(config, transport=httpx.MockTransport(handle))
        try:
            with self.assertRaises(BrainError) as failure:
                await client.read("/readyz")
            self.assertNotIn("secret backend traceback", str(failure.exception))
        finally:
            await client.aclose()


if __name__ == "__main__":
    unittest.main()
