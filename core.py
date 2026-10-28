"""Fail-closed configuration and bounded read-only client for ALWATHIQ-NET BRAIN."""
from __future__ import annotations

import ipaddress
import os
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    bot_token: str
    allowed_user_ids: frozenset[int]
    brain_url: str = ""
    brain_token: str = ""
    request_timeout: float = 5.0
    cooldown_seconds: float = 2.0

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token or ":" not in token:
            raise ConfigurationError("BOT_TOKEN must be configured")
        raw_ids = os.getenv("ALLOWED_TELEGRAM_USER_IDS", "").strip()
        try:
            ids = frozenset(int(part.strip()) for part in raw_ids.split(","))
        except ValueError as exc:
            raise ConfigurationError("ALLOWED_TELEGRAM_USER_IDS must contain numeric IDs") from exc
        if not ids or any(user_id <= 0 for user_id in ids):
            raise ConfigurationError("At least one positive allowed Telegram user ID is required")
        url = os.getenv("ALWATHIQ_BRAIN_URL", "").strip().rstrip("/")
        if url:
            validate_url(url, allow_internal_http=os.getenv("ALLOW_INTERNAL_HTTP", "").lower() == "true")
        try:
            timeout = float(os.getenv("BRAIN_TIMEOUT_SECONDS", "5"))
            cooldown = float(os.getenv("COMMAND_COOLDOWN_SECONDS", "2"))
        except ValueError as exc:
            raise ConfigurationError("Timeout and cooldown must be numeric") from exc
        if not 0.5 <= timeout <= 15 or not 0 <= cooldown <= 60:
            raise ConfigurationError("Timeout or cooldown outside approved bounds")
        if url and not os.getenv("ALWATHIQ_BRAIN_TOKEN", "").strip():
            raise ConfigurationError("A BRAIN API bearer token is required when an upstream is set")
        return cls(token, ids, url, os.getenv("ALWATHIQ_BRAIN_TOKEN", "").strip(), timeout, cooldown)


def validate_url(url: str, *, allow_internal_http: bool = False) -> None:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ConfigurationError("Invalid upstream URL") from exc
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigurationError("Upstream URL cannot contain credentials, queries, or fragments")
    if not host or parsed.path not in ("", "/") or port == 0:
        raise ConfigurationError("Upstream must be an origin without a path")
    if parsed.scheme == "https":
        return
    if parsed.scheme != "http":
        raise ConfigurationError("Upstream must use HTTP or HTTPS")
    if host == "localhost":
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    if not allow_internal_http:
        raise ConfigurationError("Non-loopback HTTP requires explicit ALLOW_INTERNAL_HTTP=true")
    # The option is for explicitly configured private Docker/host networking only.
    if host.endswith(".local") or host.replace("-", "").isalnum():
        return
    try:
        if ipaddress.ip_address(host).is_private:
            return
    except ValueError:
        pass
    raise ConfigurationError("Plain HTTP is permitted only for internal destinations")


class RateGate:
    """Per-user, in-process throttle; no persistence or distributed guarantees."""

    def __init__(self, cooldown_seconds: float) -> None:
        self.cooldown_seconds = cooldown_seconds
        self._last: dict[int, float] = {}

    def permit(self, user_id: int, *, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        previous = self._last.get(user_id)
        if previous is not None and current - previous < self.cooldown_seconds:
            return False
        self._last[user_id] = current
        # Bound memory for long-running processes.
        if len(self._last) > 4096:
            self._last = {key: value for key, value in self._last.items()
                          if current - value < max(60, self.cooldown_seconds)}
        return True


class BrainError(Exception):
    """Upstream failure safe for display without leaking credentials."""


class BrainClient:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.brain_url or "http://127.0.0.1",
            timeout=httpx.Timeout(settings.request_timeout),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def read(self, route: str) -> dict:
        if not self.settings.brain_url:
            raise BrainError("نواة التشخيص غير مرتبطة حالياً.")
        if route not in ("/readyz", "/api/v1/routeros/health", "/api/v1/incidents"):
            raise BrainError("المسار غير مسموح")
        return await self._request("GET", route)

    async def diagnostic(self, tool: str) -> dict:
        if not self.settings.brain_url:
            raise BrainError("نواة التشخيص غير مرتبطة حالياً.")
        if tool not in ("routeros.resources", "routeros.pppoe_active"):
            raise BrainError("أداة التشخيص غير مسموحة")
        return await self._request("POST", "/api/v1/diagnostics/runs",
                                   json={"calls": [{"tool_name": tool, "arguments": {}}]})

    async def _request(self, method: str, route: str, *, json: dict | None = None) -> dict:
        try:
            result = await self._client.request(
                method, route,
                headers={"Authorization": f"Bearer {self.settings.brain_token}"},
                json=json,
            )
            if result.status_code != 200 and result.status_code != 201:
                raise BrainError("تعذر جلب البيانات من النواة (HTTP %d)." % result.status_code)
            if len(result.content) > 65536:
                raise BrainError("تجاوزت الاستجابة الحد المسموح.")
            data = result.json()
            if not isinstance(data, dict):
                raise BrainError("استجابة غير متوقعة من النواة.")
            return data
        except (httpx.RequestError, ValueError) as exc:
            raise BrainError("الاتصال بنواة التشخيص غير متاح.") from exc


def summarize_diagnostic(payload: dict, tool: str) -> str:
    results = payload.get("results", [])
    if not isinstance(results, list) or not results:
        return "لا توجد بيانات تشخيصية."
    result = results[0]
    if not isinstance(result, dict) or result.get("tool_name") != tool or not result.get("ok"):
        return "فشل استعلام التشخيص."
    data = result.get("data", {})
    if not isinstance(data, dict):
        return "استجابة غير متوقعة."
    status = str(data.get("status", "unknown"))[:30]
    if status != "ok":
        return "حالة مصدر البيانات: " + status
    payload_data = data.get("data", {})
    if tool == "routeros.pppoe_active":
        count = payload_data.get("session_count") if isinstance(payload_data, dict) else None
        return "جلسات PPPoE الفعالة: " + (str(count) if isinstance(count, int) and count >= 0 else "غير معروفة")
    # Deliberately avoid sending raw interface data, identifying information or secrets to Telegram.
    resources = payload_data[0] if isinstance(payload_data, list) and payload_data and isinstance(payload_data[0], dict) else {}
    return ("موارد الراوتر: " + ", ".join(
        f"{name}: {str(resources[name])[:40]}"
        for name in ("cpu-load", "free-memory", "total-memory", "uptime") if name in resources
    )) if resources else "الاتصال ناجح؛ لا تتوفر مؤشرات موارد مختصرة."
