import asyncio
import logging
import os
from datetime import datetime
from io import BytesIO
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
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

try:
    from aiogram.enums import ContentType
except ImportError:  # aiogram 2 fallback; this project uses aiogram 3
    from aiogram.types import ContentType
from db import (
    APP_TIMEZONE,
    conn,
    init_db,
    list_patients_due_for_reminder,
    mark_reminder_sent,
    list_patients_due_for_pills_checkin,
    mark_pills_prompt_sent,
    write_pills_log,
    mark_suicide_risk_intro_sent,
    mark_suicide_risk_skipped,
    mark_allergies_skipped,
    suicide_risk_unblocked,
    _utc_date,
    DocumentError,
    MAX_DOC_BYTES,
    is_doctor,
    is_linked_patient,
    normalize_document_meta,
    save_patient_document,
)
from app import (
    SKIP_SCALES_CALLBACK,
    SKIP_ALLERGIES_CALLBACK,
    SEND_CONCLUSION_BUTTON,
    SEND_CONCLUSION_CALLBACK,
    consume_invite,
    maybe_send_osr_patient_followup,
    maybe_send_scales_repeat_hint,
    maybe_send_bot_menu,
)
from medication_notifications import (
    confirm_medication_dose,
    medication_reminder_loop,
)

log = logging.getLogger(__name__)
dp = Dispatcher()

# Linked patients who completed or skipped ОСР receive one HADS reminder every
# REMINDER_INTERVAL_DAYS (default 14). The first send is due that many days
# after the doctor link; later sends use last_sent_at. Completing a scale
# does not reset the clock. HADS is not sent before the first completed or skipped ОСР.
REMINDER_TEXT = (
    "Плановая проверка самочувствия\n\n"
    "Это регулярное напоминание раз в две недели. "
    "Оно не является диагнозом и не требует срочных действий.\n\n"
    "Пожалуйста, заполните шкалу HADS (тревога и депрессия). "
    "Так удобнее отслеживать изменения вместе с врачом.\n\n"
    "Откройте анкету кнопкой ниже — это займёт несколько минут."
)

PATIENT_START_TEXT = (
    "Привет! Я помогу в наблюдении у психиатра: найду проблему, ускорю лечение, напомню о таблеточках."
)
DOCTOR_START_TEXT = "Ниже по кнопке ссылка на ваш дашборд."
SUICIDE_RISK_INTRO_TEXT = (
    "Заполни входную анкету.\n\n"
    "Так проще начать наблюдение вместе с врачом.\n\n"
    "Открой анкету кнопкой ниже — займёт пару минут."
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


SKIP_BUTTON_TEXT = "Пропустить"
SKIP_CALLBACK_OSR = "skip:osr"
SKIP_CALLBACK_SCALES = SKIP_SCALES_CALLBACK
SKIP_CALLBACK_ALLERGIES = SKIP_ALLERGIES_CALLBACK


def scale_menu(path, label):
    if not APP_URL.startswith("https://"):
        raise RuntimeError("APP_URL must be a valid HTTPS URL in .env")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, web_app=WebAppInfo(url=app_path(path)))],
    ])


def suicide_risk_intro_menu():
    if not APP_URL.startswith("https://"):
        raise RuntimeError("APP_URL must be a valid HTTPS URL in .env")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Входная анкета",
            web_app=WebAppInfo(url=app_path("suicide-risk")),
        )],
        [InlineKeyboardButton(text=SKIP_BUTTON_TEXT, callback_data=SKIP_CALLBACK_OSR)],
    ])


def reminder_menu():
    return scale_menu("hads", "Заполнить HADS")


def doctor_menu():
    return scale_menu("doctor", "Открыть дашборд")


def persistent_bot_menu():
    """Inline Mini App buttons (same URLs as app.py bot_menu_markup). HTTPS APP_URL required."""
    if not APP_URL.startswith("https://"):
        raise RuntimeError("APP_URL must be a valid HTTPS URL in .env")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оценить настроение", web_app=WebAppInfo(url=app_path("mood")))],
        [
            InlineKeyboardButton(text="Дневник сна", web_app=WebAppInfo(url=app_path("sleep"))),
            InlineKeyboardButton(text="Дневник таблеток", web_app=WebAppInfo(url=app_path("pills-diary"))),
        ],
        [
            InlineKeyboardButton(text="Указать аллергии", web_app=WebAppInfo(url=app_path("allergies"))),
            InlineKeyboardButton(text=SEND_CONCLUSION_BUTTON, callback_data=SEND_CONCLUSION_CALLBACK),
        ],
    ])


# Daily pills check-in uses APP_TIMEZONE calendar dates. After PILLS_CHECKIN_HOUR
# (default 20:00) the bot asks once if a regimen exists and there is no log
# or prompt for that local day. Independent of the biweekly scale reminders.
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
# Patient /start (including invite, including already linked): always greeting + ОСР
# (Mini App + «Пропустить»). The 3-scales Telegram message waits until completed
# or skipped ОСР and is not sent on /start. Mini App HADS/BAI/BDI/ШПАНА are not
# blocked on ОСР. Doctors /start get a Mini App dashboard button, not this patient pack.
# ШПАНА stays one-time after the first HADS/BAI/BDI completion in app.py.
# Pills-diary Mini App is one-time after a completed HADS in app.py (skipped if configured).
# After allergies save or Telegram «Пропустить», patients get an inline
# keyboard (mood / sleep / pills / allergies / send conclusion). Once per patient.


async def send_suicide_risk_intro(message: Message, telegram_id: int) -> bool:
    try:
        await message.answer(
            SUICIDE_RISK_INTRO_TEXT,
            reply_markup=suicide_risk_intro_menu(),
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
    if is_doctor(user.id):
        await message.answer(DOCTOR_START_TEXT, reply_markup=doctor_menu())
        return
    payload = (message.text or "").partition(" ")[2].strip()
    if payload.startswith("invite_"):
        consume_invite(user.id, payload[len("invite_"):])
    await message.answer(PATIENT_START_TEXT, reply_markup=menu())
    if not await send_suicide_risk_intro(message, user.id):
        return
    mark_suicide_risk_intro_sent(user.id)


@dp.callback_query(F.data == SKIP_CALLBACK_OSR)
async def skip_osr_callback(callback: CallbackQuery):
    """Skip ОСР: record skip, send thanks + 3-scales, no doctor alert or hotline.

    Every tap after /start must work for the same user: after a previous skip,
    after they already completed ОСР, and after another /start. Mini App
    questionnaires stay ungated. Doctors are unchanged.
    """
    user = callback.from_user
    if user is None:
        await callback.answer("Не удалось определить пользователя.", show_alert=True)
        return
    await callback.answer("Пропущено")
    if is_doctor(user.id):
        return
    mark_suicide_risk_skipped(user.id)
    maybe_send_osr_patient_followup(user.id, force=True)


@dp.callback_query(F.data == SKIP_CALLBACK_SCALES)
async def skip_scales_callback(callback: CallbackQuery):
    """Skip HADS/BDI/BAI for now: repeat-hint + mood intro once. Pills wait for HADS."""
    user = callback.from_user
    if user is None:
        await callback.answer("Не удалось определить пользователя.", show_alert=True)
        return
    await callback.answer("Пропущено")
    if is_doctor(user.id) or not suicide_risk_unblocked(user.id):
        return
    maybe_send_scales_repeat_hint(user.id, allow_skip=True)


@dp.callback_query(F.data == SKIP_CALLBACK_ALLERGIES)
async def skip_allergies_callback(callback: CallbackQuery):
    """Skip allergies: no save required. Send thanks + inline keyboard once."""
    user = callback.from_user
    if user is None:
        await callback.answer("Не удалось определить пользователя.", show_alert=True)
        return
    await callback.answer("Пропущено")
    if is_doctor(user.id):
        return
    mark_allergies_skipped(user.id)
    maybe_send_bot_menu(user.id)


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
    hour = min(
        23,
        _env_int(
            "PILLS_CHECKIN_HOUR",
            _env_int("PILLS_CHECKIN_HOUR_UTC", 20, minimum=0),
            minimum=0,
        ),
    )
    now = datetime.now(APP_TIMEZONE)
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
    if callback.from_user is None:
        await callback.answer("Не удалось определить пользователя.", show_alert=True)
        return
    try:
        dose_id = int((callback.data or "").partition(":")[2])
    except ValueError:
        await callback.answer("Не удалось распознать приём.", show_alert=True)
        return

    result = confirm_medication_dose(callback.from_user.id, dose_id)
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
                log.exception("Could not update confirmed medication message")
    elif result == "already_taken":
        await callback.answer("Этот приём уже отмечен.")
    else:
        await callback.answer(
            "Это назначение уже не активно.", show_alert=True
        )


DOC_SAVED_TEXT = "Документ сохранён, врач увидит его в карте."
DOC_NEED_INVITE_TEXT = (
    "Чтобы отправлять документы, нужна ссылка-приглашение от врача."
)
DOC_UPLOAD_HINT_TEXT = (
    "Отправьте заключение файлом в этот чат — PDF, JPEG, PNG, WebP, DOC или DOCX.\n"
    "Документ сохранится в вашей карте, и врач его увидит."
)
DOC_ERROR_TEXT = {
    "too_large": "Файл слишком большой. Можно отправить документ до 20 МБ.",
    "forbidden": (
        "Этот тип файла не принимается. "
        "Можно отправить PDF, JPEG, PNG, WebP, DOC или DOCX."
    ),
    "empty": "Не удалось прочитать файл. Попробуйте ещё раз.",
}


def _file_from_message(message: Message):
    if message.document:
        doc = message.document
        return (
            doc.file_id,
            doc.file_name or "document",
            doc.mime_type,
            doc.file_size or 0,
        )
    if message.photo:
        photo = message.photo[-1]
        return photo.file_id, "photo.jpg", "image/jpeg", photo.file_size or 0
    return None


async def _download_telegram_file(bot: Bot, file_id: str, declared_size: int) -> bytes:
    if declared_size > MAX_DOC_BYTES:
        raise DocumentError("too_large")
    tg_file = await bot.get_file(file_id)
    if tg_file.file_size and tg_file.file_size > MAX_DOC_BYTES:
        raise DocumentError("too_large")
    buffer = BytesIO()
    await bot.download(tg_file, destination=buffer)
    data = buffer.getvalue()
    if not data:
        raise DocumentError("empty")
    if len(data) > MAX_DOC_BYTES:
        raise DocumentError("too_large")
    return data


async def _answer_send_conclusion(message: Message, telegram_id: int) -> None:
    if not is_linked_patient(telegram_id):
        await message.answer(DOC_NEED_INVITE_TEXT)
        return
    try:
        reply_markup = persistent_bot_menu()
    except RuntimeError:
        reply_markup = None
    await message.answer(DOC_UPLOAD_HINT_TEXT, reply_markup=reply_markup)


@dp.message(F.text == SEND_CONCLUSION_BUTTON)
async def send_conclusion_hint(message: Message):
    user = message.from_user
    if user is None or message.chat.type != "private":
        return
    if is_doctor(user.id):
        return
    await _answer_send_conclusion(message, user.id)


@dp.callback_query(F.data == SEND_CONCLUSION_CALLBACK)
async def send_conclusion_callback(callback: CallbackQuery):
    user = callback.from_user
    if user is None:
        await callback.answer("Не удалось определить пользователя.", show_alert=True)
        return
    if is_doctor(user.id):
        await callback.answer()
        return
    await callback.answer()
    message = callback.message
    if message is None:
        return
    await _answer_send_conclusion(message, user.id)


@dp.message(F.content_type.in_({ContentType.DOCUMENT, ContentType.PHOTO}))
async def receive_patient_document(message: Message):
    user = message.from_user
    if user is None or message.chat.type != "private":
        return
    if is_doctor(user.id):
        return
    if not is_linked_patient(user.id):
        await message.answer(DOC_NEED_INVITE_TEXT)
        return
    extracted = _file_from_message(message)
    if extracted is None:
        return
    file_id, original_name, mime, declared_size = extracted
    if not normalize_document_meta(original_name, mime):
        await message.answer(DOC_ERROR_TEXT["forbidden"])
        return
    try:
        data = await _download_telegram_file(message.bot, file_id, declared_size)
        save_patient_document(user.id, file_id, original_name, mime, data)
    except DocumentError as exc:
        await message.answer(DOC_ERROR_TEXT.get(exc.code, DOC_ERROR_TEXT["empty"]))
        return
    except (TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError) as exc:
        log.warning("Не удалось скачать документ %s: %s", user.id, exc)
        await message.answer("Не удалось сохранить документ. Попробуйте ещё раз.")
        return
    except Exception:
        log.exception("Ошибка сохранения документа %s", user.id)
        await message.answer("Не удалось сохранить документ. Попробуйте ещё раз.")
        return
    await message.answer(DOC_SAVED_TEXT)


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Set BOT_TOKEN in .env")
    if not APP_URL.startswith("https://"):
        raise RuntimeError("Set APP_URL to the current HTTPS tunnel URL in .env")
    logging.basicConfig(level=logging.INFO)
    init_db()
    # TELEGRAM_PROXY is a Bot API host (TelegramAPIServer.from_base), not an HTTP proxy.
    api_base = TELEGRAM_PROXY or "https://api.telegram.org"
    session = AiohttpSession(api=TelegramAPIServer.from_base(api_base))
    reminder_task = None
    pills_task = None
    medication_task = None
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
            reminder_task = asyncio.create_task(reminder_loop(bot))
            pills_task = asyncio.create_task(pills_loop(bot))
            medication_task = asyncio.create_task(medication_reminder_loop(bot))
            await dp.start_polling(bot)
    finally:
        for task in (reminder_task, pills_task, medication_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())
