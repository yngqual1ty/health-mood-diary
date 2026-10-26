import asyncio
import logging
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
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError
from aiogram.filters import CommandStart
from aiogram import F
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from db import conn, init_db
from app import consume_invite
from medication_notifications import (
    confirm_medication_dose,
    medication_reminder_loop,
)

dp = Dispatcher()
logger = logging.getLogger(__name__)

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
    display_name = " ".join(
        part for part in (user.first_name, user.last_name) if part
    )
    with conn() as db:
        db.execute(
            """
            INSERT INTO users (
                telegram_id, first_name, last_name, username,
                display_name, bot_chat_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                username = excluded.username,
                bot_chat_id = excluded.bot_chat_id
            """,
            (
                user.id,
                user.first_name or "",
                user.last_name or "",
                user.username or "",
                display_name,
                message.chat.id,
            ),
        )
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


@dp.callback_query(F.data.startswith("med:"))
async def confirm_medication(callback: CallbackQuery):
    patient_id = callback.from_user.id
    try:
        dose_id = int((callback.data or "").partition(":")[2])
    except ValueError:
        await callback.answer("Не удалось распознать приём.", show_alert=True)
        return

    result = confirm_medication_dose(patient_id, dose_id)
    if result == "taken":
        await callback.answer("Приём отмечен.")
        message = callback.message
        if message:
            try:
                await message.edit_text(
                    f"{message.text or 'Напоминание о лекарстве'}\n\n✅ Приём подтверждён.",
                    reply_markup=None,
                )
            except TelegramAPIError:
                logger.exception("Could not update confirmed medication message")
    elif result == "already_taken":
        await callback.answer("Этот приём уже отмечен.")
    else:
        await callback.answer(
            "Это назначение уже не активно.", show_alert=True
        )


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Set BOT_TOKEN in .env")
    if not APP_URL.startswith("https://"):
        raise RuntimeError("Set APP_URL to the current HTTPS tunnel URL in .env")
    init_db()
    session = (
        AiohttpSession(api=TelegramAPIServer.from_base(TELEGRAM_PROXY))
        if TELEGRAM_PROXY
        else AiohttpSession()
    )
    try:
        async with Bot(BOT_TOKEN, session=session) as bot:
            # Polling cannot run while Telegram still has a webhook configured.
            try:
                await bot.delete_webhook(drop_pending_updates=True)
            except TelegramNetworkError as exc:
                raise RuntimeError(
                    "Не удалось подключиться к Telegram Bot API. "
                    "Проверьте интернет или TELEGRAM_PROXY в .env."
                ) from exc
            reminder_task = asyncio.create_task(medication_reminder_loop(bot))
            try:
                await dp.start_polling(bot)
            finally:
                reminder_task.cancel()
                await asyncio.gather(reminder_task, return_exceptions=True)
    finally:
        await session.close()

if __name__ == "__main__":
    asyncio.run(main())
