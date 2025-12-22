import os
from telegram.ext import Application

TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

app = Application.builder().token(TOKEN).build()

app.run_polling()
