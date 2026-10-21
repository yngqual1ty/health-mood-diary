import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
APP_URL = os.getenv("APP_URL", "").strip()
TELEGRAM_PROXY = os.getenv("TELEGRAM_PROXY", "").strip()
print("APP_URL из конфигурации:", repr(APP_URL))

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from db import init_db
from app import consume_invite

dp = Dispatcher()

def menu():
    if not APP_URL.startswith("https://"):
        raise RuntimeError("APP_URL must be a valid HTTPS URL in .env")
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть дневник", web_app=WebAppInfo(url=APP_URL))]])

@dp.message(CommandStart())
async def start(message: Message):
    user = message.from_user
    if user is None:
        await message.answer("Не удалось определить пользователя Telegram.")
        return
    payload = (message.text or "").partition(" ")[2].strip()
    if payload.startswith("invite_"):
        token = payload[len("invite_"):]
        if consume_invite(user.id, token):
            text = "Готово! Вы привязаны к врачу. Можно открыть дневник."
        else:
            text = "Приглашение недействительно, истекло или уже использовано. Если вы уже привязаны к врачу, попросите его уточнить связь."
    else:
        text = "Привет! Здесь можно вести дневник настроения и заполнять анкеты."
    await message.answer(text, reply_markup=menu())

async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Set BOT_TOKEN in .env")
    if not APP_URL.startswith("https://"):
        raise RuntimeError("Set APP_URL to the current HTTPS tunnel URL in .env")
    init_db()
    session = AiohttpSession(proxy=TELEGRAM_PROXY or None)
    try:
        async with Bot(BOT_TOKEN, session=session) as bot:
            # Polling cannot run while Telegram still has a webhook configured.
            try:
                await bot.delete_webhook(drop_pending_updates=True)
            except TelegramNetworkError as exc:
                raise RuntimeError(
                    "Не удалось подключиться к api.telegram.org. "
                    "Проверьте интернет/VPN или задайте TELEGRAM_PROXY в .env."
                ) from exc
            await dp.start_polling(bot)
    finally:
        await session.close()

if __name__ == "__main__":
    asyncio.run(main())
