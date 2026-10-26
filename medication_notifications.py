import asyncio
import json
import logging
import os
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db import conn

logger = logging.getLogger(__name__)
APP_TIMEZONE = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Krasnoyarsk"))
REMINDER_GRACE = timedelta(minutes=30)
RETRY_DELAY = timedelta(minutes=5)


def local_now() -> datetime:
    return datetime.now(APP_TIMEZONE)


def ensure_doses_for_today(now: datetime | None = None) -> None:
    current = now.astimezone(APP_TIMEZONE) if now and now.tzinfo else now
    current = current or local_now()
    today = current.date()
    with conn() as db:
        db.execute("BEGIN IMMEDIATE")
        medications = db.execute(
            """
            SELECT m.id, m.patient_telegram_id, m.schedule_times
            FROM medications m
            JOIN links l
              ON l.patient_telegram_id = m.patient_telegram_id
             AND l.doctor_telegram_id = m.doctor_telegram_id
            WHERE m.active = 1 AND m.start_date <= ?
              AND (m.end_date IS NULL OR m.end_date >= ?)
            """,
            (today.isoformat(), today.isoformat()),
        ).fetchall()
        for medication in medications:
            for scheduled_time in json.loads(medication["schedule_times"]):
                scheduled_at = datetime.combine(
                    today, datetime.strptime(scheduled_time, "%H:%M").time()
                )
                db.execute(
                    """
                    INSERT OR IGNORE INTO medication_doses (
                        medication_id, patient_telegram_id, dose_date,
                        scheduled_time, scheduled_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        medication["id"],
                        medication["patient_telegram_id"],
                        today.isoformat(),
                        scheduled_time,
                        scheduled_at.strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                )


async def send_due_medication_reminders(bot: Bot, now: datetime | None = None) -> None:
    current = now.astimezone(APP_TIMEZONE) if now and now.tzinfo else now
    current = current or local_now()
    now_text = current.strftime("%Y-%m-%d %H:%M:%S")
    grace_text = (current.replace(tzinfo=None) - REMINDER_GRACE).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    retry_text = (current.replace(tzinfo=None) - RETRY_DELAY).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    with conn() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """
            UPDATE medication_doses
            SET status = 'missed'
            WHERE status = 'pending' AND scheduled_at < ?
            """,
            (grace_text,),
        )
        db.execute(
            """
            UPDATE medication_doses
            SET notification_state = 'unavailable'
            WHERE status = 'pending' AND notification_state = 'pending'
              AND scheduled_at <= ? AND scheduled_at >= ?
              AND patient_telegram_id IN (
                  SELECT telegram_id FROM users WHERE bot_chat_id IS NULL
              )
            """,
            (now_text, grace_text),
        )
        db.execute(
            """
            UPDATE medication_doses
            SET notification_state = 'pending'
            WHERE notification_state = 'sending' AND last_attempt_at <= ?
            """,
            (retry_text,),
        )
        doses = db.execute(
            """
            SELECT d.id, d.patient_telegram_id, d.scheduled_time,
                   m.dosage, m.name, m.instructions, u.bot_chat_id
            FROM medication_doses d
            JOIN medications m ON m.id = d.medication_id
            JOIN links l
              ON l.patient_telegram_id = m.patient_telegram_id
             AND l.doctor_telegram_id = m.doctor_telegram_id
            JOIN users u ON u.telegram_id = d.patient_telegram_id
            WHERE d.status = 'pending'
              AND d.notification_state = 'pending'
              AND d.scheduled_at <= ?
              AND d.scheduled_at >= ?
              AND (d.last_attempt_at IS NULL OR d.last_attempt_at <= ?)
              AND m.active = 1
              AND u.bot_chat_id IS NOT NULL
            ORDER BY d.scheduled_at
            LIMIT 50
            """,
            (now_text, grace_text, retry_text),
        ).fetchall()
        for dose in doses:
            db.execute(
                """
                UPDATE medication_doses
                SET notification_state = 'sending',
                    notification_attempts = notification_attempts + 1,
                    last_attempt_at = ?
                WHERE id = ? AND notification_state = 'pending'
                """,
                (now_text, dose["id"]),
            )

    for dose in doses:
        text = (
            f"Время принять лекарство: {dose['name']}\n"
            f"Дозировка: {dose['dosage']}\n"
            f"Время по расписанию: {dose['scheduled_time']}"
        )
        if dose["instructions"]:
            text += f"\nИнструкция врача: {dose['instructions']}"
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Принял(а)",
                        callback_data=f"med:{dose['id']}",
                    )
                ]
            ]
        )
        try:
            await bot.send_message(
                chat_id=dose["bot_chat_id"], text=text, reply_markup=keyboard
            )
        except TelegramAPIError:
            logger.exception(
                "Failed sending medication reminder dose_id=%s patient_id=%s",
                dose["id"],
                dose["patient_telegram_id"],
            )
            with conn() as db:
                db.execute(
                    """
                    UPDATE medication_doses
                    SET notification_state = CASE
                        WHEN notification_attempts >= 3 THEN 'failed'
                        ELSE 'pending'
                    END
                    WHERE id = ? AND notification_state = 'sending'
                    """,
                    (dose["id"],),
                )
            continue

        with conn() as db:
            db.execute(
                """
                UPDATE medication_doses
                SET notification_state = 'sent', notified_at = ?
                WHERE id = ? AND notification_state = 'sending'
                """,
                (now_text, dose["id"]),
            )


async def medication_reminder_loop(bot: Bot) -> None:
    while True:
        try:
            ensure_doses_for_today()
            await send_due_medication_reminders(bot)
        except (sqlite3.Error, ValueError, TypeError):
            logger.exception("Medication reminder cycle failed")
        await asyncio.sleep(30)


def confirm_medication_dose(patient_id: int, dose_id: int) -> str:
    with conn() as db:
        db.execute("BEGIN IMMEDIATE")
        dose = db.execute(
            """
            SELECT d.status, m.active
            FROM medication_doses d
            JOIN medications m ON m.id = d.medication_id
            JOIN links l
              ON l.patient_telegram_id = m.patient_telegram_id
             AND l.doctor_telegram_id = m.doctor_telegram_id
            WHERE d.id = ? AND d.patient_telegram_id = ?
            """,
            (dose_id, patient_id),
        ).fetchone()
        if not dose or not dose["active"]:
            return "unavailable"
        if dose["status"] == "taken":
            return "already_taken"
        if dose["status"] != "pending":
            return "unavailable"
        changed = db.execute(
            """
            UPDATE medication_doses
            SET status = 'taken', taken_at = ?
            WHERE id = ? AND patient_telegram_id = ? AND status = 'pending'
            """,
            (
                local_now().strftime("%Y-%m-%d %H:%M:%S"),
                dose_id,
                patient_id,
            ),
        ).rowcount
        return "taken" if changed == 1 else "already_taken"
