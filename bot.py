from telegram.ext import Application, CommandHandler
import os

TOKEN = os.getenv("8385788660:AAFvcYKGT7ECzlIPqxAmO0XVad1UnCGYMMg")

async def start(update, context):
    await update.message.reply_text("البوت شغال ✅")

app = Application.builder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.run_polling()
