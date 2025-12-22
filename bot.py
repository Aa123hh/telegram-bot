import os
from telegram.ext import Application, CommandHandler

# قراءة التوكن من GitHub Secrets
TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

# إنشاء التطبيق
app = Application.builder().token(TOKEN).build()

# أمر /start
async def start(update, context):
    await update.message.reply_text("Bot is running successfully ✅")

app.add_handler(CommandHandler("start", start))

# تشغيل البوت
if __name__ == "__main__":
    app.run_polling()
