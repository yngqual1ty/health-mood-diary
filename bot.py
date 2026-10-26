import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
APP_URL = os.getenv("APP_URL", "").strip()
TELEGRAM_PROXY = os.getenv("TELEGRAM_PROXY", "").strip()
print("APP_URL из конфигурации:", repr(APP_URL))

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
<<<<<<< HEAD
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError
from aiogram.filters import CommandStart
from aiogram import F
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from db import conn, init_db
=======
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from db import (
    init_db,
    list_patients_due_for_reminder,
    mark_reminder_sent,
    list_patients_due_for_pills_checkin,
    mark_pills_prompt_sent,
    write_pills_log,
    should_send_suicide_risk_intro,
    mark_suicide_risk_intro_sent,
    _utc_date,
)
>>>>>>> 2d85a3a549c2f97cf0b657dfa193a69c2cf8dd4f
from app import consume_invite
from medication_notifications import (
    confirm_medication_dose,
    medication_reminder_loop,
)

log = logging.getLogger(__name__)
dp = Dispatcher()
logger = logging.getLogger(__name__)

# Linked patients who already completed ОСР receive one HADS reminder every
# REMINDER_INTERVAL_DAYS (default 14). The first send is due that many days
# after the doctor link; later sends use last_sent_at. Completing a scale
# does not reset the clock. HADS is not sent before the first completed ОСР.
REMINDER_TEXT = (
    "Плановая проверка самочувствия\n\n"
    "Это регулярное напоминание раз в две недели. "
    "Оно не является диагнозом и не требует срочных действий.\n\n"
    "Пожалуйста, заполните шкалу HADS (тревога и депрессия). "
    "Так удобнее отслеживать изменения вместе с врачом.\n\n"
    "Откройте анкету кнопкой ниже — это займёт несколько минут."
)

SUICIDE_RISK_INTRO_TEXT = (
    "Пожалуйста, заполните оценку суицидального риска.\n\n"
    "Это скрининг для врача, а не диагноз. "
    "Так проще начать наблюдение вместе с врачом.\n\n"
    "Откройте анкету кнопкой ниже — это займёт несколько минут."
)


def _env_int(name, default, minimum=None):
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    if minimum is not None:
        return max(minimum, value)
    return value


def app_path(path):
    return f"{APP_URL.rstrip('/')}/{path.strip('/')}/"


def menu():
    if not APP_URL.startswith("https://"):
        raise RuntimeError("APP_URL must be a valid HTTPS URL in .env")
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть дневник", web_app=WebAppInfo(url=APP_URL))]])


def scale_menu(path, label):
    if not APP_URL.startswith("https://"):
        raise RuntimeError("APP_URL must be a valid HTTPS URL in .env")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, web_app=WebAppInfo(url=app_path(path)))],
    ])


def reminder_menu():
    return scale_menu("hads", "Заполнить HADS")


# Daily pills check-in uses UTC calendar dates. After PILLS_CHECKIN_HOUR_UTC
# (default 20:00) the bot asks once if a regimen exists and there is no log
# or prompt for that UTC day. Independent of the biweekly scale reminders.
PILLS_TAKEN_LABELS = {"yes": "да", "no": "нет", "partial": "частично"}


def _format_regimen_lines(items):
    lines = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip() or "Лекарство"
        extra = [str(item.get("time") or "").strip(), str(item.get("note") or "").strip()]
        extra = [part for part in extra if part]
        lines.append(f"• {name} — {', '.join(extra)}" if extra else f"• {name}")
    return "\n".join(lines)


def pills_checkin_text(items):
    names = _format_regimen_lines(items) or "• схема не указана"
    return (
        "Напоминание о лекарствах\n\n"
        "Сегодня в дневнике такая схема:\n"
        f"{names}\n\n"
        "Получилось принять лекарства по схеме?\n\n"
        "Это ежедневная отметка для дневника, а не медицинская рекомендация."
    )


def pills_menu(day: str):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Да", callback_data=f"pills:yes:{day}"),
        InlineKeyboardButton(text="Нет", callback_data=f"pills:no:{day}"),
        InlineKeyboardButton(text="Частично", callback_data=f"pills:partial:{day}"),
    ]])


# Daily mood is the Mini App page /mood/, not a Telegram inline keyboard and not PANAS.
# First linked /start sends only ОСР. HADS/BAI/BDI go out after a completed ОСР in app.py.
# ШПАНА stays one-time after the first HADS/BAI/BDI completion in app.py.


async def send_suicide_risk_intro(message: Message, telegram_id: int) -> bool:
    try:
        await message.answer(
            SUICIDE_RISK_INTRO_TEXT,
            reply_markup=scale_menu("suicide-risk", "Оценка суицидального риска"),
        )
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        log.warning("Не удалось отправить ОСР %s: %s", telegram_id, exc)
        return False
    except TelegramNetworkError:
        log.exception("Сеть недоступна при отправке ОСР %s", telegram_id)
        return False
    return True


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
    if should_send_suicide_risk_intro(user.id) and await send_suicide_risk_intro(message, user.id):
        mark_suicide_risk_intro_sent(user.id)


async def _send_reminder(bot: Bot, telegram_id: int) -> bool:
    for attempt in range(2):
        try:
            await bot.send_message(telegram_id, REMINDER_TEXT, reply_markup=reminder_menu())
            return True
        except TelegramRetryAfter as exc:
            if attempt == 0:
                await asyncio.sleep(exc.retry_after)
                continue
            log.warning("Лимит Telegram при отправке напоминания %s", telegram_id)
            return False
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            log.warning("Не удалось отправить напоминание %s: %s", telegram_id, exc)
            return True
        except TelegramNetworkError:
            log.exception("Сеть недоступна при отправке напоминания %s", telegram_id)
            return False
    return False


async def send_due_reminders(bot: Bot):
    interval_days = _env_int("REMINDER_INTERVAL_DAYS", 14, minimum=1)
    due_ids = list_patients_due_for_reminder(interval_days)
    if due_ids:
        log.info("Напоминания: к отправке %s пациент(ов)", len(due_ids))
    for telegram_id in due_ids:
        if await _send_reminder(bot, telegram_id):
            mark_reminder_sent(telegram_id)
        await asyncio.sleep(0.05)


async def reminder_loop(bot: Bot):
    check_seconds = _env_int("REMINDER_CHECK_SECONDS", 3600, minimum=60)
    while True:
        try:
            await send_due_reminders(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ошибка цикла напоминаний")
        await asyncio.sleep(check_seconds)


async def _send_pills_checkin(bot: Bot, telegram_id: int, items, day: str) -> bool:
    for attempt in range(2):
        try:
            await bot.send_message(
                telegram_id,
                pills_checkin_text(items),
                reply_markup=pills_menu(day),
            )
            return True
        except TelegramRetryAfter as exc:
            if attempt == 0:
                await asyncio.sleep(exc.retry_after)
                continue
            log.warning("Лимит Telegram при отправке отметки лекарств %s", telegram_id)
            return False
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            log.warning("Не удалось отправить отметку лекарств %s: %s", telegram_id, exc)
            return True
        except TelegramNetworkError:
            log.exception("Сеть недоступна при отправке отметки лекарств %s", telegram_id)
            return False
    return False


async def send_due_pills_checkins(bot: Bot):
    hour = min(23, _env_int("PILLS_CHECKIN_HOUR_UTC", 20, minimum=0))
    now = datetime.utcnow()
    if now.hour < hour:
        return
    day = _utc_date(now)
    due = list_patients_due_for_pills_checkin(now)
    if due:
        log.info("Лекарства: к отправке %s пациент(ов)", len(due))
    for patient in due:
        telegram_id = patient["telegram_id"]
        if await _send_pills_checkin(bot, telegram_id, patient["items"], day):
            mark_pills_prompt_sent(telegram_id, day)
        await asyncio.sleep(0.05)


async def pills_loop(bot: Bot):
    check_seconds = _env_int("PILLS_CHECK_SECONDS", 3600, minimum=60)
    while True:
        try:
            await send_due_pills_checkins(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ошибка цикла отметки лекарств")
        await asyncio.sleep(check_seconds)


@dp.callback_query(F.data.startswith("pills:"))
async def pills_callback(callback: CallbackQuery):
    user = callback.from_user
    raw = callback.data or ""
    parts = raw.split(":")
    if user is None or len(parts) != 3 or parts[1] not in PILLS_TAKEN_LABELS:
        await callback.answer("Не удалось прочитать ответ.", show_alert=True)
        return
    taken, day = parts[1], parts[2]
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        await callback.answer("Некорректная дата.", show_alert=True)
        return
    if day > _utc_date():
        await callback.answer("Нельзя отметить будущий день.", show_alert=True)
        return
    _row, created = write_pills_log(user.id, day, taken, overwrite=False)
    if not created:
        await callback.answer("За этот день уже есть отметка.")
        return
    label = PILLS_TAKEN_LABELS[taken]
    text = (
        f"Отметили: {label} ({day}). Спасибо.\n\n"
        "Это запись в дневник, а не медицинская рекомендация."
    )
    if callback.message:
        try:
            await callback.message.edit_text(text)
        except (TelegramBadRequest, TelegramForbiddenError):
            pass
    await callback.answer("Записали")



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
    logging.basicConfig(level=logging.INFO)
    init_db()
<<<<<<< HEAD
    session = (
        AiohttpSession(api=TelegramAPIServer.from_base(TELEGRAM_PROXY))
        if TELEGRAM_PROXY
        else AiohttpSession()
    )
=======
    # TELEGRAM_PROXY is a Bot API host (TelegramAPIServer.from_base), not an HTTP proxy.
    api_base = TELEGRAM_PROXY or "https://api.telegram.org"
    session = AiohttpSession(api=TelegramAPIServer.from_base(api_base))
    reminder_task = None
    pills_task = None
>>>>>>> 2d85a3a549c2f97cf0b657dfa193a69c2cf8dd4f
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
<<<<<<< HEAD
            reminder_task = asyncio.create_task(medication_reminder_loop(bot))
            try:
                await dp.start_polling(bot)
            finally:
                reminder_task.cancel()
                await asyncio.gather(reminder_task, return_exceptions=True)
=======
            reminder_task = asyncio.create_task(reminder_loop(bot))
            pills_task = asyncio.create_task(pills_loop(bot))
            await dp.start_polling(bot)
>>>>>>> 2d85a3a549c2f97cf0b657dfa193a69c2cf8dd4f
    finally:
        for task in (reminder_task, pills_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())
