"""Independent, constrained RouterOS 7 REST adapter for ALWATHIQ-NET.

Inspired by the *feature list*, not copied from third-party bot sources.
No SSH, shell, generic path execution, or plaintext HTTP.
"""
from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

import httpx


class RouterError(Exception):
    """Safe error text; never includes secrets or an upstream response body."""


class RouterConfigError(ValueError):
    pass


NAME = re.compile(r"^[A-Za-z0-9@_.-]{1,64}$")
ROUTER_ID = re.compile(r"^\*[0-9A-Fa-f]+$")
READ_PATHS = frozenset({
    "/rest/system/resource", "/rest/interface", "/rest/interface/l2tp-client",
    "/rest/ppp/secret", "/rest/ppp/active", "/rest/ppp/profile",
    "/rest/ip/hotspot/user", "/rest/ip/hotspot/active",
})
POST_PATHS = frozenset({"/rest/ping", "/rest/system/backup/save", "/rest/system/reboot"})
# Projection prevents RouterOS from returning PPP/Hotspot passwords to the bot.
READ_FIELDS = {
    "/rest/system/resource": "uptime,cpu-load,free-memory,total-memory,version",
    "/rest/interface": ".id,name,running,disabled,type,rx-byte,tx-byte",
    "/rest/interface/l2tp-client": ".id,name,running,disabled,connect-to",
    "/rest/ppp/secret": ".id,name,profile,disabled,service",
    "/rest/ppp/active": ".id,name,service,uptime,address",
    "/rest/ppp/profile": "name,rate-limit",
    "/rest/ip/hotspot/user": ".id,name,profile,disabled",
    "/rest/ip/hotspot/active": ".id,user,uptime,address",
}
TABLES = {
    "ppp": "/rest/ppp/secret",
    "ppp_active": "/rest/ppp/active",
    "hotspot": "/rest/ip/hotspot/user",
    "hotspot_active": "/rest/ip/hotspot/active",
}


@dataclass(frozen=True)
class RouterConfig:
    origin: str = ""
    username: str = ""
    password: str = ""
    owner_ids: frozenset[int] = frozenset()
    writes_enabled: bool = False
    critical_enabled: bool = False
    ca_file: str = ""
    request_timeout: float = 5.0

    @classmethod
    def from_env(cls, allowed_user_ids: frozenset[int]) -> "RouterConfig":
        origin = os.getenv("ROUTEROS_HTTPS_URL", "").strip().rstrip("/")
        username = os.getenv("ROUTEROS_USERNAME", "").strip()
        password = os.getenv("ROUTEROS_PASSWORD", "")
        ca_file = os.getenv("ROUTEROS_CA_FILE", "").strip()
        writes = os.getenv("ROUTEROS_WRITES_ENABLED", "false").lower() == "true"
        critical = os.getenv("ROUTEROS_CRITICAL_ENABLED", "false").lower() == "true"
        raw_owners = os.getenv("ROUTEROS_OWNER_TELEGRAM_IDS", "").strip()
        try:
            owners = frozenset(int(value.strip()) for value in raw_owners.split(",")) if raw_owners else frozenset()
            timeout = float(os.getenv("ROUTEROS_TIMEOUT_SECONDS", "5"))
        except ValueError as exc:
            raise RouterConfigError("Invalid RouterOS owner IDs or timeout") from exc
        if any(v <= 0 for v in owners) or not owners.issubset(allowed_user_ids):
            raise RouterConfigError("RouterOS owners must be within the Telegram allowlist")
        if not (0.5 <= timeout <= 15):
            raise RouterConfigError("RouterOS timeout outside limits")
        if bool(origin) != bool(username and password):
            raise RouterConfigError("HTTPS origin and both credentials must be configured together")
        if origin:
            try:
                u = urlsplit(origin)
                host = u.hostname
                port = u.port
            except ValueError as exc:
                raise RouterConfigError("Invalid RouterOS URL") from exc
            if (u.scheme != "https" or not host or u.username or u.password
                    or u.path or u.query or u.fragment or port == 0):
                raise RouterConfigError("RouterOS requires a bare HTTPS origin")
        if (writes or critical) and (not origin or not owners):
            raise RouterConfigError("RouterOS writes require configured HTTPS and owner IDs")
        if critical and not writes:
            raise RouterConfigError("Critical operations require writes to be enabled")
        return cls(origin, username, password, owners, writes, critical, ca_file, timeout)


def safe_name(value: str) -> str:
    if not NAME.fullmatch(value):
        raise RouterError("اسم المستخدم غير صالح (1–64 من أحرف وأرقام و@._-).")
    return value


def safe_id(value: object) -> str:
    if not isinstance(value, str) or not ROUTER_ID.fullmatch(value):
        raise RouterError("معرّف سجل MikroTik غير صالح.")
    return quote(value, safe="")


def clean(value: object, size: int = 48) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value))[:size]


class RouterOSClient:
    def __init__(self, config: RouterConfig, *, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        self._client = httpx.AsyncClient(
            base_url=config.origin or "https://127.0.0.1",
            auth=httpx.BasicAuth(config.username, config.password),
            verify=config.ca_file or True,
            timeout=httpx.Timeout(config.request_timeout),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            limits=httpx.Limits(max_connections=3, max_keepalive_connections=2),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, path: str, *, data: dict | None = None):
        if not self.config.origin:
            raise RouterError("اتصال MikroTik المباشر غير مهيأ.")
        if method == "GET":
            allowed = path in READ_PATHS
        elif method == "POST":
            allowed = path in POST_PATHS
        elif method in {"PATCH", "DELETE"}:
            allowed = any(path.startswith(table + "/") and
                          ROUTER_ID.fullmatch(path[len(table)+1:].replace("%2A", "*"))
                          for table in TABLES.values())
        else:
            allowed = False
        if not allowed:
            raise RouterError("عملية RouterOS غير مسموح بها.")
        if method in {"PATCH", "DELETE", "POST"} and path != "/rest/ping" and not self.config.writes_enabled:
            raise RouterError("التعديلات معطّلة في الإعدادات.")
        if path in {"/rest/system/reboot", "/rest/system/backup/save"} and not self.config.critical_enabled:
            raise RouterError("الأوامر الحرجة معطّلة في الإعدادات.")
        try:
            response = await self._client.request(
                method, path,
                params={".proplist": READ_FIELDS[path]} if method == "GET" else None,
                json=data if method != "GET" else None,
            )
            if response.status_code not in {200, 201, 202, 204}:
                raise RouterError(f"رفض الراوتر الطلب (HTTP {response.status_code}).")
            if len(response.content) > 262144:
                raise RouterError("حجم استجابة الراوتر تجاوز الحد المسموح.")
            if not response.content:
                return []
            payload = response.json()
            if not isinstance(payload, (list, dict)):
                raise RouterError("نوع استجابة RouterOS غير متوقع.")
            return payload
        except (httpx.RequestError, ValueError) as exc:
            raise RouterError("تعذّر الاتصال الآمن بجهاز MikroTik.") from exc

    async def rows(self, path: str) -> list[dict]:
        payload = await self.request("GET", path)
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise RouterError("استجابة القائمة من الراوتر غير صالحة.")
        return payload

    async def find(self, table: str, name: str) -> dict:
        name = safe_name(name)
        records = await self.rows(TABLES[table])
        found = [r for r in records if r.get("name") == name]
        if len(found) != 1:
            raise RouterError("المستخدم غير موجود أو سجلاته غير فريدة.")
        return found[0]

    async def change(self, table: str, name: str, operation: str) -> None:
        if table not in {"ppp", "hotspot", "ppp_active", "hotspot_active"}:
            raise RouterError("نوع العملية غير مدعوم.")
        record = await self.find(table, name)
        path = TABLES[table] + "/" + safe_id(record.get(".id"))
        if operation in {"disable", "enable"} and table in {"ppp", "hotspot"}:
            await self.request("PATCH", path, data={"disabled": "yes" if operation == "disable" else "no"})
        elif operation == "delete":
            await self.request("DELETE", path)
        else:
            raise RouterError("العملية غير مسموحة لهذا الجدول.")

    async def ping(self, address: str):
        try:
            ipaddress.ip_address(address)
        except ValueError as exc:
            raise RouterError("أدخل عنوان IP صالحاً فقط.") from exc
        return await self.request("POST", "/rest/ping", data={"address": address, "count": "3"})

    async def backup(self, name: str) -> None:
        if not re.fullmatch(r"alwathiq-[0-9]{8}-[0-9]{6}", name):
            raise RouterError("اسم نسخة النسخ الاحتياطي غير صالح.")
        await self.request("POST", "/rest/system/backup/save", data={"name": name})

    async def reboot(self) -> None:
        await self.request("POST", "/rest/system/reboot", data={})


def format_rows(rows: list[dict], fields: tuple[str, ...], title: str, *, max_rows: int = 15) -> str:
    lines = [f"{title} — العدد: {len(rows)}"]
    for row in rows[:max_rows]:
        parts = [f"{key}: {clean(row[key])}" for key in fields if key in row]
        lines.append("• " + " | ".join(parts))
    if len(rows) > max_rows:
        lines.append(f"عرض أول {max_rows} فقط.")
    return "\n".join(lines)[:3200]


async def router_read(client: RouterOSClient, command: str, args: list[str]) -> str:
    views = {
        "resource": ("/rest/system/resource", ("uptime", "cpu-load", "free-memory", "total-memory", "version"), "موارد الراوتر"),
        "interfaces": ("/rest/interface", ("name", "running", "disabled", "type"), "الواجهات"),
        "traffic": ("/rest/interface", ("name", "rx-byte", "tx-byte"), "حركة الواجهات (عدادات تراكمية)"),
        "vpn": ("/rest/interface/l2tp-client", ("name", "running", "disabled", "connect-to"), "عملاء L2TP"),
        "listpppoe": ("/rest/ppp/secret", ("name", "profile", "disabled", "service"), "حسابات PPP"),
        "activepppoe": ("/rest/ppp/active", ("name", "service", "uptime", "address"), "جلسات PPP النشطة"),
        "profiles": ("/rest/ppp/profile", ("name", "rate-limit"), "باقات PPP"),
        "listhotspot": ("/rest/ip/hotspot/user", ("name", "profile", "disabled"), "مستخدمو Hotspot"),
        "activehotspot": ("/rest/ip/hotspot/active", ("user", "uptime", "address"), "جلسات Hotspot"),
    }
    if command == "ping":
        if len(args) != 1:
            return "الاستخدام: /ping 8.8.8.8"
        results = await client.ping(args[0])
        if not isinstance(results, list):
            return "استجابة ping غير متوقعة."
        return format_rows(results, ("host", "time", "size", "status", "packet-loss"),
                           "اختبار الاتصال", max_rows=3)
    if command not in views:
        return "أمر القراءة غير متاح."
    if args:
        return f"الاستخدام: /{command}"
    path, fields, title = views[command]
    rows = await client.rows(path)
    return format_rows(rows, fields, title)
