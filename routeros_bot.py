"""Telegram command bridge for an opt-in RouterOS REST adapter.

Private allowlisted reads and separately opt-in, owner-only two-step writes.
"""
from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from routeros_ops import RouterConfig, RouterError, RouterOSClient, router_read, safe_name

LOG = logging.getLogger("alwathiq.routeros")

READ_COMMANDS = (
    "resource", "interfaces", "traffic", "vpn", "ping", "listpppoe",
    "activepppoe", "profiles", "listhotspot", "activehotspot",
)
WRITE_COMMANDS = {
    "disablepppoe": ("ppp", "disable"),
    "enablepppoe": ("ppp", "enable"),
    "delpppoe": ("ppp", "delete"),
    "kickpppoe": ("ppp_active", "delete"),
    "disablehotspot": ("hotspot", "disable"),
    "enablehotspot": ("hotspot", "enable"),
    "delhotspot": ("hotspot", "delete"),
}
CRITICAL_COMMANDS = frozenset({"reboot", "backup"})
ALL_WRITES = frozenset(WRITE_COMMANDS) | CRITICAL_COMMANDS
CONFIRM_TTL_SECONDS = 90


@dataclass(frozen=True)
class Pending:
    command: str
    name: str
    code: str
    expires: float


def private_allowed(update: Update, settings) -> bool:
    user, chat = update.effective_user, update.effective_chat
    return bool(user and chat and chat.type == "private"
                and user.id in settings.allowed_user_ids and chat.id == user.id)


async def router_command(update: Update, context: ContextTypes.DEFAULT_TYPE, name: str) -> None:
    settings = context.application.bot_data["settings"]
    if not private_allowed(update, settings) or update.message is None:
        return
    gate = context.application.bot_data["rate_gate"]
    if not gate.permit(update.effective_user.id):
        await update.message.reply_text("انتظر قليلاً قبل إرسال أمر آخر.")
        return
    config: RouterConfig = context.application.bot_data["router_config"]
    client: RouterOSClient = context.application.bot_data["router_client"]
    args = list(context.args or [])
    if name in READ_COMMANDS:
        try:
            result = await router_read(client, name, args)
        except RouterError as exc:
            result = str(exc)
        except Exception:
            LOG.warning("RouterOS read failed: %s", name)
            result = "تعذرت قراءة بيانات MikroTik."
        await update.message.reply_text(result[:3400])
        return

    if name in {"addpppoe", "addhotspot", "vpnconfig"}:
        await update.message.reply_text(
            "إنشاء الحسابات وتوزيع كلمات المرور أو إعدادات VPN يتم من "
            "واجهة المشتركين المعتمدة؛ لا نطلب كلمات المرور ولا نرسلها عبر تلغرام."
        )
        return

    if name not in ALL_WRITES:
        return
    if update.effective_user.id not in config.owner_ids or not config.writes_enabled:
        await update.message.reply_text("التعديلات غير مسموحة لهذا الحساب أو معطّلة افتراضياً.")
        return
    if name in CRITICAL_COMMANDS and not config.critical_enabled:
        await update.message.reply_text("العمليات الحرجة غير مفعلة.")
        return
    if name in CRITICAL_COMMANDS:
        if args:
            await update.message.reply_text(f"الاستخدام: /{name}")
            return
        target = ""
    else:
        if len(args) != 1:
            await update.message.reply_text(f"الاستخدام: /{name} username")
            return
        try:
            target = safe_name(args[0])
        except RouterError as exc:
            await update.message.reply_text(str(exc))
            return

    # Store only one pending operation per owner, with a short TTL.
    code = secrets.token_hex(4)
    pending: dict[int, Pending] = context.application.bot_data["router_pending"]
    pending[update.effective_user.id] = Pending(name, target, code,
                                                  time.monotonic() + CONFIRM_TTL_SECONDS)
    await update.message.reply_text(
        f"طلب يتطلب تأكيداً: {name} {target}\n"
        f"اكتب /confirm {code} خلال 90 ثانية. لن يُنفذ شيء قبل التأكيد."
    )


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    if not private_allowed(update, settings) or update.message is None:
        return
    user_id = update.effective_user.id
    config: RouterConfig = context.application.bot_data["router_config"]
    pending: dict[int, Pending] = context.application.bot_data["router_pending"]
    op = pending.get(user_id)
    supplied = list(context.args or [])
    if (not config.writes_enabled or user_id not in config.owner_ids
            or not op or len(supplied) != 1
            or not secrets.compare_digest(supplied[0], op.code)
            or time.monotonic() >= op.expires):
        pending.pop(user_id, None)
        await update.message.reply_text("التأكيد غير صالح أو انتهت مدته.")
        return
    pending.pop(user_id, None)  # Single use, even on error.
    if op.command in CRITICAL_COMMANDS and not config.critical_enabled:
        await update.message.reply_text("العملية الحرجة معطّلة.")
        return
    client: RouterOSClient = context.application.bot_data["router_client"]
    try:
        if op.command in WRITE_COMMANDS:
            table, action = WRITE_COMMANDS[op.command]
            await client.change(table, op.name, action)
        elif op.command == "backup":
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            await client.backup(f"alwathiq-{stamp}")
        elif op.command == "reboot":
            await client.reboot()
        else:
            raise RouterError("عملية غير مدعومة.")
        LOG.warning("RouterOS owner action accepted: %s", op.command)
        await update.message.reply_text(
            f"أرسل الراوتر نجاح العملية: {op.command}. "
            "تحقق من الحالة الحية؛ فصل الحساب لا ينهي الجلسة القائمة تلقائياً."
        )
    except RouterError as exc:
        LOG.warning("RouterOS owner action failed: %s", op.command)
        await update.message.reply_text(f"لم تُؤكد العملية: {str(exc)}")
    except Exception:
        LOG.warning("RouterOS owner action unexpected failure: %s", op.command)
        await update.message.reply_text("تعذّر تأكيد نتيجة العملية؛ تحقق من حالة الراوتر قبل تكرارها.")


def make_handler(name: str):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await router_command(update, context, name)
    return handler


def install_router_handlers(app: Application, settings) -> RouterOSClient:
    config = RouterConfig.from_env(settings.allowed_user_ids)
    client = RouterOSClient(config)
    app.bot_data["router_config"] = config
    app.bot_data["router_client"] = client
    app.bot_data["router_pending"] = {}
    for name in (*READ_COMMANDS, *sorted(ALL_WRITES), "addpppoe", "addhotspot", "vpnconfig"):
        app.add_handler(CommandHandler(name, make_handler(name)))
    app.add_handler(CommandHandler("confirm", confirm))
    return client
