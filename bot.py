"""ALWATHIQ-NET Telegram operations bot: private, authorized, read-only."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from core import BrainClient, BrainError, ConfigurationError, RateGate, Settings, summarize_diagnostic

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
LOG = logging.getLogger("alwathiq.telegram")

HELP = (
    "ALWATHIQ-NET | المراقبة فقط\n"
    "/status — جاهزية نواة التشخيص\n"
    "/router — موارد جهاز الاختبار المرتبط بالنواة\n"
    "/pppoe — عدد جلسات PPPoE من المصدر المعتمد\n"
    "/incidents — ملخص الحوادث\n"
    "/help — قائمة الأوامر\n"
    "لا توجد أوامر تعديل أو إعادة تشغيل أو قطع اشتراكات."
)


def is_authorized(update: Update, settings: Settings) -> bool:
    user = update.effective_user
    chat = update.effective_chat
    return bool(user and chat and chat.type == "private"
                and user.id in settings.allowed_user_ids
                and chat.id == user.id)


async def guarded(update: Update, context: ContextTypes.DEFAULT_TYPE, command: str) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not is_authorized(update, settings) or update.message is None:
        return
    gate: RateGate = context.application.bot_data["rate_gate"]
    if not gate.permit(update.effective_user.id):
        await update.message.reply_text("يرجى الانتظار قبل إرسال أمر جديد.")
        return
    if command in ("start", "help"):
        await update.message.reply_text(HELP)
        return
    client: BrainClient = context.application.bot_data["client"]
    try:
        if command == "status":
            data = await client.read("/readyz")
            checks = data.get("checks", {})
            if not isinstance(checks, dict):
                checks = {}
            response = ("جاهزية النواة: " + str(data.get("status", "unknown"))[:30]
                        + "\nالتخزين: " + str(checks.get("storage", "غير محدد"))[:30]
                        + "\nالتكاملات: " + str(checks.get("integrations", "غير محدد"))[:30])
        elif command == "router":
            response = summarize_diagnostic(await client.diagnostic("routeros.resources"),
                                            "routeros.resources")
        elif command == "pppoe":
            response = summarize_diagnostic(await client.diagnostic("routeros.pppoe_active"),
                                            "routeros.pppoe_active")
        elif command == "incidents":
            data = await client.read("/api/v1/incidents")
            items = data.get("items", [])
            if not isinstance(items, list):
                items = []
            opened = sum(isinstance(item, dict) and item.get("status") != "resolved"
                         for item in items)
            response = (f"الحوادث في الصفحة الحالية: {len(items)}\n"
                        f"غير المحسومة في الصفحة: {opened}\n"
                        "للتفاصيل استخدم لوحة إدارة المنصة.")
        else:
            response = "الأمر غير متاح."
    except BrainError as exc:
        response = str(exc)
    except Exception:
        # Never disclose exception detail, HTTP credentials, upstream URL or token.
        LOG.warning("Read-only operation failed: %s", command)
        response = "تعذر تنفيذ القراءة الآمنة."
    await update.message.reply_text(response[:3500])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await guarded(update, context, "start")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await guarded(update, context, "help")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await guarded(update, context, "status")


async def router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await guarded(update, context, "router")


async def pppoe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await guarded(update, context, "pppoe")


async def incidents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await guarded(update, context, "incidents")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOG.error("Telegram update handler failed (details redacted): %s",
              type(context.error).__name__)


async def post_shutdown(application: Application) -> None:
    client: BrainClient = application.bot_data["client"]
    await client.aclose()


def make_application(settings: Settings) -> Application:
    app = Application.builder().token(settings.bot_token).concurrent_updates(False).post_shutdown(post_shutdown).build()
    app.bot_data["settings"] = settings
    app.bot_data["rate_gate"] = RateGate(settings.cooldown_seconds)
    app.bot_data["client"] = BrainClient(settings)
    for name, handler in (
        ("start", start), ("help", help_command), ("status", status),
        ("router", router), ("pppoe", pppoe), ("incidents", incidents)
    ):
        app.add_handler(CommandHandler(name, handler))
    app.add_error_handler(error_handler)
    return app


def main() -> None:
    try:
        settings = Settings.from_env()
    except ConfigurationError as exc:
        raise SystemExit("Invalid bot configuration: " + str(exc)) from None
    LOG.info("Starting ALWATHIQ-NET bot with private-chat allowlist enabled")
    make_application(settings).run_polling(
        drop_pending_updates=True,
        allowed_updates=[Update.MESSAGE],
        poll_interval=1.0,
        timeout=10,
    )


if __name__ == "__main__":
    main()
