import os
import time
import logging
from telegram.ext import Application, CommandHandler
from telegram.constants import ChatType

# ================== الإعدادات ==================
ALLOWED_USERS = {764404739}     # Telegram user_id المسموح
RATE_LIMIT_SECONDS = 2          # منع السبام
LOG_FILE = "security.log"
# ===============================================

# ================== إعداد التسجيل ==================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
# ==================================================

# ================== قراءة التوكن ==================
TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")
# ==================================================

# ================== منع السبام ==================
USER_LAST_CALL = {}

def rate_limit(user_id):
    now = time.time()
    last = USER_LAST_CALL.get(user_id, 0)
    if now - last < RATE_LIMIT_SECONDS:
        return False
    USER_LAST_CALL[user_id] = now
    return True
# =================================================

# ================== صلاحيات المستخدم ==================
def is_allowed(update):
    return update.effective_user.id in ALLOWED_USERS
# ======================================================

# ================== تسجيل محاولات الاختراق ==================
def log_attack(update, reason):
    user = update.effective_user
    chat = update.effective_chat

    logging.warning(
        f"BLOCKED | "
        f"user_id={user.id} | "
        f"username={user.username} | "
        f"chat_type={chat.type} | "
        f"reason={reason}"
    )
# ===========================================================

# ================== أوامر البوت ==================
async def start(update, context):
    # منع العمل داخل الجروبات
    if update.effective_chat.type != ChatType.PRIVATE:
        log_attack(update, "COMMAND_IN_GROUP")
        return

    user_id = update.effective_user.id

    # مستخدم غير مصرح
    if not is_allowed(update):
        log_attack(update, "UNAUTHORIZED_USER")
        await update.message.reply_text("⛔ غير مصرح لك")
        return

    # منع السبام
    if not rate_limit(user_id):
        log_attack(update, "RATE_LIMIT")
        return

    await update.message.reply_text("Bot is running securely ✅")
# ==================================================

# ================== معالجة الأخطاء ==================
async def error_handler(update, context):
    logging.error("Unhandled exception:", exc_info=context.error)
# ====================================================

# ================== تشغيل التطبيق ==================
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_error_handler(error_handler)

    app.run_polling()

if __name__ == "__main__":
    main()
# ====================================================
