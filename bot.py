import os
import time
import logging
from telegram.ext import Application, CommandHandler
from telegram.constants import ChatType

# ========= الإعدادات =========
ALLOWED_USERS = {764404739}   # ضع Telegram user_id مالك فقط
RATE_LIMIT_SECONDS = 2        # منع السبام
# =============================

# ========= تسجيل الأخطاء =========
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
# ================================

# ========= قراءة التوكن =========
TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")
# ================================

# ========= منع السبام =========
USER_LAST_CALL = {}

def rate_limit(user_id):
    now = time.time()
    last = USER_LAST_CALL.get(user_id, 0)
    if now - last < RATE_LIMIT_SECONDS:
        return False
    USER_LAST_CALL[user_id] = now
    return True
# ================================

# ========= صلاحيات المستخدم =========
def is_allowed(update):
    return update.effective_user.id in ALLOWED_USERS
# ================================

# ========= أوامر البوت =========
async def start(update, context):
    # السماح فقط بالمحادثة الخاصة
    if update.effective_chat.type != ChatType.PRIVATE:
        return

    user_id = update.effective_user.id

    # التحقق من الصلاحية
    if not is_allowed(update):
        await update.message.reply_text("⛔ غير مصرح لك")
        return

    # منع السبام
    if not rate_limit(user_id):
        return

    await update.message.reply_text("Bot is running securely ✅")

# ========= معالجة الأخطاء =========
async def error_handler(update, context):
    logging.error("Unhandled exception:", exc_info=context.error)
# ================================

# ========= تشغيل التطبيق =========
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_error_handler(error_handler)

    app.run_polling()

if __name__ == "__main__":
    main()
