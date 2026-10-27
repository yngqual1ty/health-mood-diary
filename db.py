import json
import os
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=False)
APP_TIMEZONE = ZoneInfo(os.getenv("APP_TIMEZONE", "Europe/Moscow"))
DB_PATH = os.getenv("DATABASE_PATH", str(BASE_DIR / "mood.sqlite3"))
DB_PATH = str((BASE_DIR / DB_PATH).resolve()) if not os.path.isabs(DB_PATH) else DB_PATH
_docs_env = os.getenv("DOCS_DIR", "").strip()
if _docs_env:
    DOCS_DIR = str(Path(_docs_env).resolve()) if os.path.isabs(_docs_env) else str((BASE_DIR / _docs_env).resolve())
else:
    DOCS_DIR = str(Path(DB_PATH).parent / "docs")

MAX_DOC_BYTES = 20 * 1024 * 1024
EXT_TO_MIME = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MIME_TO_EXT = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}
REJECT_EXT = {
    ".exe", ".bat", ".cmd", ".com", ".cpl", ".dll", ".msi", ".scr", ".pif",
    ".js", ".jse", ".vbs", ".vbe", ".ps1", ".sh", ".apk", ".bin", ".hta",
    ".jar", ".dmg", ".app",
}
REJECT_MIME = {
    "application/x-msdownload",
    "application/x-executable",
    "application/x-dosexec",
    "application/vnd.microsoft.portable-executable",
    "application/x-sh",
    "application/javascript",
    "text/javascript",
}

@contextmanager
def conn():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def init_db():
    with conn() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY, role TEXT NOT NULL DEFAULT 'patient',
            first_name TEXT NOT NULL DEFAULT '', last_name TEXT NOT NULL DEFAULT '',
            username TEXT NOT NULL DEFAULT '', display_name TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            suicide_risk_intro_sent_at TEXT,
            starter_intro_sent_at TEXT,
            panas_intro_sent_at TEXT,
            pills_intro_sent_at TEXT,
            scales_repeat_hint_sent_at TEXT,
            mood_intro_sent_at TEXT,
            mood_followup_sent_at TEXT,
            allergies TEXT NOT NULL DEFAULT '',
            bot_chat_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS invites (
            token TEXT PRIMARY KEY, doctor_telegram_id INTEGER NOT NULL, used_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, expires_at TEXT
        );
        CREATE TABLE IF NOT EXISTS links (
            patient_telegram_id INTEGER PRIMARY KEY, doctor_telegram_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS moods (
            id INTEGER PRIMARY KEY AUTOINCREMENT, patient_telegram_id INTEGER NOT NULL,
            score INTEGER NOT NULL CHECK(score BETWEEN 1 AND 5), emotions TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT, patient_telegram_id INTEGER NOT NULL,
            questionnaire_id TEXT NOT NULL, payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'completed', result TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS treatment_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, doctor_telegram_id INTEGER NOT NULL,
            patient_telegram_id INTEGER NOT NULL, question TEXT NOT NULL,
            response TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS reminders (
            patient_telegram_id INTEGER PRIMARY KEY,
            last_sent_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS regimen (
            patient_telegram_id INTEGER PRIMARY KEY,
            items TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS pills_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_telegram_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            taken TEXT NOT NULL CHECK(taken IN ('yes', 'no', 'partial')),
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(patient_telegram_id, date)
        );
        CREATE TABLE IF NOT EXISTS pills_prompts (
            patient_telegram_id INTEGER PRIMARY KEY,
            last_asked_date TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS mood_prompts (
            patient_telegram_id INTEGER PRIMARY KEY,
            last_asked_date TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS hotline_prompts (
            patient_telegram_id INTEGER NOT NULL,
            questionnaire_id TEXT NOT NULL,
            last_sent_date TEXT NOT NULL,
            PRIMARY KEY (patient_telegram_id, questionnaire_id)
        );
        CREATE TABLE IF NOT EXISTS patient_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_telegram_id INTEGER NOT NULL,
            telegram_file_id TEXT NOT NULL DEFAULT '',
            stored_name TEXT NOT NULL DEFAULT '',
            original_name TEXT NOT NULL DEFAULT '',
            mime TEXT NOT NULL DEFAULT '',
            size INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_patient_documents_patient
            ON patient_documents(patient_telegram_id);
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            telegram_id INTEGER NOT NULL,
            expires TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_telegram
            ON sessions(telegram_id);
        CREATE TABLE IF NOT EXISTS medications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doctor_telegram_id INTEGER NOT NULL,
            patient_telegram_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            dosage TEXT NOT NULL,
            schedule_times TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT,
            instructions TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS medication_doses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            medication_id INTEGER NOT NULL,
            patient_telegram_id INTEGER NOT NULL,
            dose_date TEXT NOT NULL,
            scheduled_time TEXT NOT NULL,
            scheduled_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            notification_state TEXT NOT NULL DEFAULT 'pending',
            notification_attempts INTEGER NOT NULL DEFAULT 0,
            last_attempt_at TEXT,
            notified_at TEXT,
            taken_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(medication_id, dose_date, scheduled_time)
        );
        CREATE INDEX IF NOT EXISTS idx_medications_patient
            ON medications(patient_telegram_id, doctor_telegram_id, active);
        CREATE INDEX IF NOT EXISTS idx_medication_doses_due
            ON medication_doses(status, notification_state, scheduled_at);
        """)
        Path(DOCS_DIR).mkdir(parents=True, exist_ok=True)
        user_columns = {row["name"] for row in db.execute("PRAGMA table_info(users)").fetchall()}
        for name in ("first_name", "last_name", "username", "display_name"):
            if name not in user_columns:
                db.execute(f"ALTER TABLE users ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
        if "suicide_risk_intro_sent_at" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN suicide_risk_intro_sent_at TEXT")
        if "starter_intro_sent_at" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN starter_intro_sent_at TEXT")
        if "panas_intro_sent_at" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN panas_intro_sent_at TEXT")
        if "pills_intro_sent_at" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN pills_intro_sent_at TEXT")
        if "scales_repeat_hint_sent_at" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN scales_repeat_hint_sent_at TEXT")
        if "mood_intro_sent_at" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN mood_intro_sent_at TEXT")
        if "mood_followup_sent_at" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN mood_followup_sent_at TEXT")
        if "allergies" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN allergies TEXT NOT NULL DEFAULT ''")
        if "bot_chat_id" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN bot_chat_id INTEGER")
        invite_columns = {row["name"] for row in db.execute("PRAGMA table_info(invites)").fetchall()}
        if "expires_at" not in invite_columns:
            db.execute("ALTER TABLE invites ADD COLUMN expires_at TEXT")
        answer_columns = {row["name"] for row in db.execute("PRAGMA table_info(answers)").fetchall()}
        if "status" not in answer_columns:
            db.execute("ALTER TABLE answers ADD COLUMN status TEXT NOT NULL DEFAULT 'completed'")
        if "result" not in answer_columns:
            db.execute("ALTER TABLE answers ADD COLUMN result TEXT NOT NULL DEFAULT '{}'")


def _utc_now(now=None):
    return now or datetime.utcnow()


def _app_now(now=None):
    if now is None:
        return datetime.now(APP_TIMEZONE)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(APP_TIMEZONE)


def _utc_stamp(now=None):
    return _utc_now(now).strftime("%Y-%m-%d %H:%M:%S")


def list_patients_due_for_reminder(interval_days, now=None):
    cutoff = _utc_stamp(_utc_now(now) - timedelta(days=interval_days))
    with conn() as db:
        rows = db.execute(
            """
            SELECT l.patient_telegram_id
            FROM links l
            LEFT JOIN users u ON u.telegram_id = l.patient_telegram_id
            LEFT JOIN reminders r ON r.patient_telegram_id = l.patient_telegram_id
            WHERE COALESCE(u.role, 'patient') = 'patient'
              AND COALESCE(r.last_sent_at, l.created_at) <= ?
              AND EXISTS (
                SELECT 1 FROM answers a
                WHERE a.patient_telegram_id = l.patient_telegram_id
                  AND a.questionnaire_id = 'suicide-risk'
                  AND COALESCE(a.status, 'completed') = 'completed'
              )
            """,
            (cutoff,),
        ).fetchall()
    return [row["patient_telegram_id"] for row in rows]


def mark_reminder_sent(patient_telegram_id, sent_at=None):
    with conn() as db:
        db.execute(
            """
            INSERT INTO reminders (patient_telegram_id, last_sent_at)
            VALUES (?, ?)
            ON CONFLICT(patient_telegram_id) DO UPDATE SET last_sent_at = excluded.last_sent_at
            """,
            (patient_telegram_id, _utc_stamp(sent_at)),
        )


PILLS_TAKEN_VALUES = ("yes", "no", "partial")


def _utc_date(now=None):
    """APP_TIMEZONE calendar date (YYYY-MM-DD). Used for once-per-day pills/hotline/mood checks."""
    return _app_now(now).strftime("%Y-%m-%d")


def _parse_regimen_items(raw):
    try:
        items = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return items if isinstance(items, list) else []


def get_regimen(patient_telegram_id):
    with conn() as db:
        row = db.execute(
            "SELECT items, created_at FROM regimen WHERE patient_telegram_id = ?",
            (patient_telegram_id,),
        ).fetchone()
    if not row:
        return None
    return {"items": _parse_regimen_items(row["items"]), "created_at": row["created_at"]}


def upsert_regimen(patient_telegram_id, items, created_at=None):
    payload = json.dumps(items, ensure_ascii=False)
    stamp = created_at or _utc_stamp()
    with conn() as db:
        db.execute(
            """
            INSERT INTO regimen (patient_telegram_id, items, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(patient_telegram_id) DO UPDATE SET items = excluded.items
            """,
            (patient_telegram_id, payload, stamp),
        )
    return get_regimen(patient_telegram_id)


def get_pills_log(patient_telegram_id, day):
    with conn() as db:
        row = db.execute(
            """
            SELECT id, patient_telegram_id, date, taken, note, created_at
            FROM pills_log
            WHERE patient_telegram_id = ? AND date = ?
            """,
            (patient_telegram_id, day),
        ).fetchone()
    return dict(row) if row else None


def list_pills_log(patient_telegram_id, since_date, limit=30):
    with conn() as db:
        rows = db.execute(
            """
            SELECT date, taken, note, created_at
            FROM pills_log
            WHERE patient_telegram_id = ? AND date >= ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (patient_telegram_id, since_date, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def write_pills_log(patient_telegram_id, day, taken, note="", overwrite=False):
    if taken not in PILLS_TAKEN_VALUES:
        raise ValueError("taken must be yes, no, or partial")
    note = (note or "").strip()
    with conn() as db:
        if overwrite:
            db.execute(
                """
                INSERT INTO pills_log (patient_telegram_id, date, taken, note)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(patient_telegram_id, date) DO UPDATE SET
                    taken = excluded.taken,
                    note = excluded.note
                """,
                (patient_telegram_id, day, taken, note),
            )
            created = True
        else:
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO pills_log (patient_telegram_id, date, taken, note)
                VALUES (?, ?, ?, ?)
                """,
                (patient_telegram_id, day, taken, note),
            )
            created = cursor.rowcount == 1
    return get_pills_log(patient_telegram_id, day), created


def list_patients_due_for_pills_checkin(now=None):
    """Patients with a regimen, no APP_TIMEZONE-day log, and no prompt sent yet today."""
    today = _utc_date(now)
    with conn() as db:
        rows = db.execute(
            """
            SELECT r.patient_telegram_id, r.items
            FROM regimen r
            LEFT JOIN users u ON u.telegram_id = r.patient_telegram_id
            LEFT JOIN pills_log l
              ON l.patient_telegram_id = r.patient_telegram_id AND l.date = ?
            LEFT JOIN pills_prompts p
              ON p.patient_telegram_id = r.patient_telegram_id
            WHERE COALESCE(u.role, 'patient') = 'patient'
              AND l.id IS NULL
              AND COALESCE(p.last_asked_date, '') < ?
            """,
            (today, today),
        ).fetchall()
    due = []
    for row in rows:
        items = _parse_regimen_items(row["items"])
        if not items:
            continue
        due.append({"telegram_id": row["patient_telegram_id"], "items": items})
    return due


def has_completed_suicide_risk(telegram_id):
    """True if this patient has at least one completed ОСР (suicide-risk) answer."""
    with conn() as db:
        row = db.execute(
            """
            SELECT 1 FROM answers
            WHERE patient_telegram_id = ?
              AND questionnaire_id = 'suicide-risk'
              AND COALESCE(status, 'completed') = 'completed'
            LIMIT 1
            """,
            (telegram_id,),
        ).fetchone()
    return bool(row)


def should_send_suicide_risk_intro(telegram_id):
    """True for a patient who has never received the ОСР Mini App intro."""
    with conn() as db:
        row = db.execute(
            """
            SELECT COALESCE(role, 'patient') AS role, suicide_risk_intro_sent_at
            FROM users WHERE telegram_id = ?
            """,
            (telegram_id,),
        ).fetchone()
    if not row or row["role"] == "doctor":
        return False
    return not row["suicide_risk_intro_sent_at"]


def mark_suicide_risk_intro_sent(telegram_id, sent_at=None):
    stamp = _utc_stamp(sent_at)
    with conn() as db:
        db.execute(
            "INSERT INTO users (telegram_id) VALUES (?) ON CONFLICT(telegram_id) DO NOTHING",
            (telegram_id,),
        )
        db.execute(
            """
            UPDATE users SET suicide_risk_intro_sent_at = ?
            WHERE telegram_id = ? AND suicide_risk_intro_sent_at IS NULL
            """,
            (stamp, telegram_id),
        )


def should_send_starter_intro(telegram_id):
    """True for a patient who has never received the HADS/BDI/BAI Mini App intro."""
    with conn() as db:
        row = db.execute(
            """
            SELECT COALESCE(u.role, 'patient') AS role, u.starter_intro_sent_at
            FROM links l
            LEFT JOIN users u ON u.telegram_id = l.patient_telegram_id
            WHERE l.patient_telegram_id = ?
            """,
            (telegram_id,),
        ).fetchone()
    if not row or row["role"] == "doctor":
        return False
    return not row["starter_intro_sent_at"]


def mark_starter_intro_sent(telegram_id, sent_at=None):
    stamp = _utc_stamp(sent_at)
    with conn() as db:
        db.execute(
            "INSERT INTO users (telegram_id) VALUES (?) ON CONFLICT(telegram_id) DO NOTHING",
            (telegram_id,),
        )
        db.execute(
            """
            UPDATE users SET starter_intro_sent_at = ?
            WHERE telegram_id = ? AND starter_intro_sent_at IS NULL
            """,
            (stamp, telegram_id),
        )


def claim_scales_intro(telegram_id, sent_at=None):
    """True if this caller may send the one-time HADS/BDI/BAI message after completed ОСР."""
    stamp = _utc_stamp(sent_at)
    with conn() as db:
        db.execute(
            "INSERT INTO users (telegram_id) VALUES (?) ON CONFLICT(telegram_id) DO NOTHING",
            (telegram_id,),
        )
        cursor = db.execute(
            """
            UPDATE users SET starter_intro_sent_at = ?
            WHERE telegram_id = ?
              AND COALESCE(role, 'patient') = 'patient'
              AND starter_intro_sent_at IS NULL
              AND EXISTS (
                SELECT 1 FROM answers
                WHERE patient_telegram_id = users.telegram_id
                  AND questionnaire_id = 'suicide-risk'
                  AND COALESCE(status, 'completed') = 'completed'
              )
            """,
            (stamp, telegram_id),
        )
        return cursor.rowcount == 1


def release_scales_intro(telegram_id):
    with conn() as db:
        db.execute(
            "UPDATE users SET starter_intro_sent_at = NULL WHERE telegram_id = ?",
            (telegram_id,),
        )


def claim_panas_intro(telegram_id, sent_at=None):
    """True if this caller may send the one-time ШПАНА prompt (not a daily ask)."""
    stamp = _utc_stamp(sent_at)
    with conn() as db:
        db.execute(
            "INSERT INTO users (telegram_id) VALUES (?) ON CONFLICT(telegram_id) DO NOTHING",
            (telegram_id,),
        )
        cursor = db.execute(
            """
            UPDATE users SET panas_intro_sent_at = ?
            WHERE telegram_id = ?
              AND COALESCE(role, 'patient') = 'patient'
              AND panas_intro_sent_at IS NULL
              AND NOT EXISTS (
                SELECT 1 FROM answers
                WHERE patient_telegram_id = users.telegram_id
                  AND questionnaire_id = 'panas'
              )
            """,
            (stamp, telegram_id),
        )
        return cursor.rowcount == 1


def release_panas_intro(telegram_id):
    with conn() as db:
        db.execute(
            "UPDATE users SET panas_intro_sent_at = NULL WHERE telegram_id = ?",
            (telegram_id,),
        )


def claim_pills_intro(telegram_id, sent_at=None):
    """True if this caller may send the one-time pills-diary Mini App prompt."""
    stamp = _utc_stamp(sent_at)
    with conn() as db:
        db.execute(
            "INSERT INTO users (telegram_id) VALUES (?) ON CONFLICT(telegram_id) DO NOTHING",
            (telegram_id,),
        )
        cursor = db.execute(
            """
            UPDATE users SET pills_intro_sent_at = ?
            WHERE telegram_id = ?
              AND COALESCE(role, 'patient') = 'patient'
              AND pills_intro_sent_at IS NULL
              AND EXISTS (
                SELECT 1 FROM answers
                WHERE patient_telegram_id = users.telegram_id
                  AND questionnaire_id = 'hads'
                  AND COALESCE(status, 'completed') = 'completed'
              )
              AND NOT EXISTS (
                SELECT 1 FROM regimen
                WHERE patient_telegram_id = users.telegram_id
              )
            """,
            (stamp, telegram_id),
        )
        return cursor.rowcount == 1


def release_pills_intro(telegram_id):
    with conn() as db:
        db.execute(
            "UPDATE users SET pills_intro_sent_at = NULL WHERE telegram_id = ?",
            (telegram_id,),
        )


def claim_scales_repeat_hint(telegram_id, sent_at=None):
    """True if this caller may send the one-time HADS/BDI/BAI repeat-hint.

    Once ever per patient (`users.scales_repeat_hint_sent_at`), not per scale
    and not on every later completion. The copy explains biweekly reminders;
    sending it again would spam alongside pills/ШПАНА/hotline after HADS.
    """
    stamp = _utc_stamp(sent_at)
    with conn() as db:
        db.execute(
            "INSERT INTO users (telegram_id) VALUES (?) ON CONFLICT(telegram_id) DO NOTHING",
            (telegram_id,),
        )
        cursor = db.execute(
            """
            UPDATE users SET scales_repeat_hint_sent_at = ?
            WHERE telegram_id = ?
              AND COALESCE(role, 'patient') = 'patient'
              AND scales_repeat_hint_sent_at IS NULL
              AND EXISTS (
                SELECT 1 FROM answers
                WHERE patient_telegram_id = users.telegram_id
                  AND questionnaire_id IN ('hads', 'bdi', 'bai')
                  AND COALESCE(status, 'completed') = 'completed'
              )
            """,
            (stamp, telegram_id),
        )
        return cursor.rowcount == 1


def release_scales_repeat_hint(telegram_id):
    with conn() as db:
        db.execute(
            "UPDATE users SET scales_repeat_hint_sent_at = NULL WHERE telegram_id = ?",
            (telegram_id,),
        )


def claim_mood_intro(telegram_id, sent_at=None):
    """True if this caller may send the one-time daily-mood Mini App intro.

    Once ever per patient (`users.mood_intro_sent_at`). Same gate as the
    HADS/BDI/BAI repeat hint: completed starter scale, patient only. Not ОСР
    or ШПАНА. Call after the hint so Telegram order is hint, then mood.
    """
    stamp = _utc_stamp(sent_at)
    with conn() as db:
        db.execute(
            "INSERT INTO users (telegram_id) VALUES (?) ON CONFLICT(telegram_id) DO NOTHING",
            (telegram_id,),
        )
        cursor = db.execute(
            """
            UPDATE users SET mood_intro_sent_at = ?
            WHERE telegram_id = ?
              AND COALESCE(role, 'patient') = 'patient'
              AND mood_intro_sent_at IS NULL
              AND EXISTS (
                SELECT 1 FROM answers
                WHERE patient_telegram_id = users.telegram_id
                  AND questionnaire_id IN ('hads', 'bdi', 'bai')
                  AND COALESCE(status, 'completed') = 'completed'
              )
            """,
            (stamp, telegram_id),
        )
        return cursor.rowcount == 1


def release_mood_intro(telegram_id):
    with conn() as db:
        db.execute(
            "UPDATE users SET mood_intro_sent_at = NULL WHERE telegram_id = ?",
            (telegram_id,),
        )


def claim_mood_followup(telegram_id, sent_at=None):
    """True if this caller may send the one-time after-first-mood closer.

    Once ever per patient (`users.mood_followup_sent_at`). Requires at least
    one mood row so later empty calls cannot send it. Not a daily reminder.
    """
    stamp = _utc_stamp(sent_at)
    with conn() as db:
        db.execute(
            "INSERT INTO users (telegram_id) VALUES (?) ON CONFLICT(telegram_id) DO NOTHING",
            (telegram_id,),
        )
        cursor = db.execute(
            """
            UPDATE users SET mood_followup_sent_at = ?
            WHERE telegram_id = ?
              AND COALESCE(role, 'patient') = 'patient'
              AND mood_followup_sent_at IS NULL
              AND EXISTS (
                SELECT 1 FROM moods
                WHERE patient_telegram_id = users.telegram_id
              )
            """,
            (stamp, telegram_id),
        )
        return cursor.rowcount == 1


def release_mood_followup(telegram_id):
    with conn() as db:
        db.execute(
            "UPDATE users SET mood_followup_sent_at = NULL WHERE telegram_id = ?",
            (telegram_id,),
        )


def claim_hotline_notice(patient_telegram_id, questionnaire_id, now=None):
    """True if this caller may send the patient hotline for this scale today (APP_TIMEZONE)."""
    today = _utc_date(now)
    with conn() as db:
        cursor = db.execute(
            """
            INSERT INTO hotline_prompts (patient_telegram_id, questionnaire_id, last_sent_date)
            VALUES (?, ?, ?)
            ON CONFLICT(patient_telegram_id, questionnaire_id) DO UPDATE SET
                last_sent_date = excluded.last_sent_date
            WHERE hotline_prompts.last_sent_date < excluded.last_sent_date
            """,
            (patient_telegram_id, questionnaire_id, today),
        )
        return cursor.rowcount == 1


def release_hotline_notice(patient_telegram_id, questionnaire_id, now=None):
    """Undo a failed claim so the hotline can be retried the same APP_TIMEZONE day."""
    today = _utc_date(now)
    with conn() as db:
        db.execute(
            """
            UPDATE hotline_prompts
            SET last_sent_date = ''
            WHERE patient_telegram_id = ?
              AND questionnaire_id = ?
              AND last_sent_date = ?
            """,
            (patient_telegram_id, questionnaire_id, today),
        )


def mark_pills_prompt_sent(patient_telegram_id, asked_date=None):
    day = asked_date or _utc_date()
    with conn() as db:
        db.execute(
            """
            INSERT INTO pills_prompts (patient_telegram_id, last_asked_date)
            VALUES (?, ?)
            ON CONFLICT(patient_telegram_id) DO UPDATE SET last_asked_date = excluded.last_asked_date
            """,
            (patient_telegram_id, day),
        )


def is_doctor(telegram_id):
    with conn() as db:
        row = db.execute(
            "SELECT role FROM users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
    return bool(row and row["role"] == "doctor")


def write_mood_for_day(patient_telegram_id, day, score, label="", now=None):
    """Insert one mood for this APP_TIMEZONE date. Returns True if inserted, False if a row already exists."""
    if score not in (1, 2, 3, 4, 5):
        raise ValueError("score must be 1–5")
    label = (label or "").strip()
    created_at = f"{day} {_app_now(now).strftime('%H:%M:%S')}"
    with conn() as db:
        existing = db.execute(
            """
            SELECT id FROM moods
            WHERE patient_telegram_id = ?
              AND date(created_at) = ?
            LIMIT 1
            """,
            (patient_telegram_id, day),
        ).fetchone()
        if existing:
            return False
        db.execute(
            """
            INSERT INTO moods(patient_telegram_id, score, emotions, note, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (patient_telegram_id, score, label, label, created_at),
        )
        return True


def list_patients_due_for_mood_checkin(now=None):
    """Unused leftover: daily mood is Mini App /mood/. Table mood_prompts is not written."""
    today = _utc_date(now)
    with conn() as db:
        rows = db.execute(
            """
            SELECT l.patient_telegram_id
            FROM links l
            LEFT JOIN users u ON u.telegram_id = l.patient_telegram_id
            LEFT JOIN mood_prompts p
              ON p.patient_telegram_id = l.patient_telegram_id
            WHERE COALESCE(u.role, 'patient') = 'patient'
              AND COALESCE(p.last_asked_date, '') < ?
              AND NOT EXISTS (
                SELECT 1 FROM moods m
                WHERE m.patient_telegram_id = l.patient_telegram_id
                  AND date(m.created_at) = ?
              )
            """,
            (today, today),
        ).fetchall()
    return [row["patient_telegram_id"] for row in rows]


def mark_mood_prompt_sent(patient_telegram_id, asked_date=None):
    day = asked_date or _utc_date()
    with conn() as db:
        db.execute(
            """
            INSERT INTO mood_prompts (patient_telegram_id, last_asked_date)
            VALUES (?, ?)
            ON CONFLICT(patient_telegram_id) DO UPDATE SET last_asked_date = excluded.last_asked_date
            """,
            (patient_telegram_id, day),
        )


class DocumentError(ValueError):
    """Patient file rejected or could not be stored. `.code` is a stable reason."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def is_linked_patient(telegram_id):
    if is_doctor(telegram_id):
        return False
    with conn() as db:
        row = db.execute(
            "SELECT 1 FROM links WHERE patient_telegram_id = ?",
            (telegram_id,),
        ).fetchone()
    return bool(row)


def sanitize_filename(name: str, default: str = "document") -> str:
    name = (name or "").replace("\x00", "").replace("\\", "/").split("/")[-1]
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r"[^\w.\- ]+", "_", name, flags=re.UNICODE)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name or name in {".", ".."}:
        name = default
    stem, ext = os.path.splitext(name)
    stem = (stem or default)[:80]
    ext = ext[:10].lower()
    return f"{stem}{ext}"


def _looks_executable(data: bytes) -> bool:
    if data.startswith(b"MZ") or data.startswith(b"\x7fELF") or data.startswith(b"#!"):
        return True
    if data.startswith(b"\xca\xfe\xba\xbe") or data.startswith(b"\xce\xfa\xed\xfe"):
        return True
    return False


def normalize_document_meta(original_name: str, mime: str | None):
    """Return (safe_name, mime) if the file type is allowed, otherwise None."""
    name = (original_name or "").replace("\\", "/").split("/")[-1].replace("\x00", "")
    ext = Path(name).suffix.lower()
    mime_n = (mime or "").split(";")[0].strip().lower()
    if ext in REJECT_EXT or mime_n in REJECT_MIME:
        return None
    if ext in EXT_TO_MIME:
        out_mime = mime_n if mime_n in MIME_TO_EXT else EXT_TO_MIME[ext]
        if out_mime == "image/jpg":
            out_mime = "image/jpeg"
        return sanitize_filename(name or f"document{ext}"), out_mime
    if mime_n in MIME_TO_EXT:
        guessed_ext = MIME_TO_EXT[mime_n]
        out_mime = "image/jpeg" if mime_n == "image/jpg" else mime_n
        stem = Path(name).stem if name else "document"
        return sanitize_filename(f"{stem}{guessed_ext}"), out_mime
    return None


def _document_path(patient_telegram_id, stored_name: str) -> Path | None:
    if not stored_name or Path(stored_name).name != stored_name:
        return None
    base = Path(DOCS_DIR).resolve()
    path = (base / str(int(patient_telegram_id)) / stored_name).resolve()
    try:
        path.relative_to(base)
    except ValueError:
        return None
    return path


def save_patient_document(patient_telegram_id, telegram_file_id, original_name, mime, data: bytes):
    if not data:
        raise DocumentError("empty")
    if len(data) > MAX_DOC_BYTES:
        raise DocumentError("too_large")
    if _looks_executable(data):
        raise DocumentError("forbidden")
    meta = normalize_document_meta(original_name, mime)
    if not meta:
        raise DocumentError("forbidden")
    safe_name, out_mime = meta
    display_name = Path(original_name or "").name.strip() or safe_name
    display_name = display_name.replace("\x00", "")[:180]
    patient_dir = Path(DOCS_DIR) / str(int(patient_telegram_id))
    patient_dir.mkdir(parents=True, exist_ok=True)
    dest = None
    with conn() as db:
        cursor = db.execute(
            """
            INSERT INTO patient_documents (
                patient_telegram_id, telegram_file_id, stored_name,
                original_name, mime, size
            ) VALUES (?, ?, '', ?, ?, ?)
            """,
            (
                int(patient_telegram_id),
                telegram_file_id or "",
                display_name,
                out_mime,
                len(data),
            ),
        )
        doc_id = cursor.lastrowid
        stored_name = f"{doc_id}_{safe_name}"
        dest = patient_dir / stored_name
        try:
            dest.write_bytes(data)
            db.execute(
                "UPDATE patient_documents SET stored_name = ? WHERE id = ?",
                (stored_name, doc_id),
            )
        except Exception:
            if dest is not None:
                dest.unlink(missing_ok=True)
            raise
    return get_patient_document(patient_telegram_id, doc_id)


def list_patient_documents(patient_telegram_id):
    with conn() as db:
        rows = db.execute(
            """
            SELECT id, original_name, mime, size, created_at
            FROM patient_documents
            WHERE patient_telegram_id = ?
            ORDER BY id DESC
            """,
            (int(patient_telegram_id),),
        ).fetchall()
    return [dict(row) for row in rows]


def get_patient_document(patient_telegram_id, doc_id):
    with conn() as db:
        row = db.execute(
            """
            SELECT id, patient_telegram_id, telegram_file_id, stored_name,
                   original_name, mime, size, created_at
            FROM patient_documents
            WHERE id = ? AND patient_telegram_id = ?
            """,
            (int(doc_id), int(patient_telegram_id)),
        ).fetchone()
    return dict(row) if row else None


def patient_document_file(patient_telegram_id, doc_id):
    row = get_patient_document(patient_telegram_id, doc_id)
    if not row:
        return None
    path = _document_path(patient_telegram_id, row.get("stored_name") or "")
    if path is None or not path.is_file():
        return None
    return row, path


def delete_patient_document(patient_telegram_id, doc_id):
    row = get_patient_document(patient_telegram_id, doc_id)
    if not row:
        return False
    path = _document_path(patient_telegram_id, row.get("stored_name") or "")
    with conn() as db:
        deleted = db.execute(
            "DELETE FROM patient_documents WHERE id = ? AND patient_telegram_id = ?",
            (int(doc_id), int(patient_telegram_id)),
        ).rowcount
    if path is not None:
        path.unlink(missing_ok=True)
    return deleted == 1


def create_session(token, telegram_id, expires):
    now = _utc_stamp()
    with conn() as db:
        db.execute("DELETE FROM sessions WHERE expires <= ?", (now,))
        db.execute(
            "INSERT INTO sessions (token, telegram_id, expires) VALUES (?, ?, ?)",
            (token, int(telegram_id), expires),
        )


def session_telegram_id(token):
    if not token:
        return None
    with conn() as db:
        row = db.execute(
            "SELECT telegram_id FROM sessions WHERE token = ? AND expires > ?",
            (token, _utc_stamp()),
        ).fetchone()
    return int(row["telegram_id"]) if row else None


def delete_session(token):
    if not token:
        return
    with conn() as db:
        db.execute("DELETE FROM sessions WHERE token = ?", (token,))


def delete_sessions_for_user(telegram_id):
    with conn() as db:
        db.execute("DELETE FROM sessions WHERE telegram_id = ?", (int(telegram_id),))


_FLOW_RESET_TABLES = (
    "answers",
    "moods",
    "pills_log",
    "pills_prompts",
    "mood_prompts",
    "hotline_prompts",
    "regimen",
    "reminders",
    "treatment_suggestions",
    "medication_doses",
    "medications",
    "sessions",
    "links",
    "invites",
    "users",
)


def _unlink_patient_document_files(rows):
    for row in rows:
        path = _document_path(row["patient_telegram_id"], row["stored_name"] or "")
        if path is not None and path.is_file():
            path.unlink(missing_ok=True)
    docs_root = Path(DOCS_DIR).resolve()
    if not docs_root.is_dir():
        return
    for child in docs_root.iterdir():
        if not child.is_dir() or not child.name.isdigit():
            continue
        for leftover in child.iterdir():
            if leftover.is_file() or leftover.is_symlink():
                leftover.unlink(missing_ok=True)
        try:
            child.rmdir()
        except OSError:
            pass


def reset_dev_data():
    """Wipe all diary data so Telegram /start and after-ОСР messages fire again.

    Deletes users, links, invites, and flow tables. Caller should re-insert ADMIN_DOCTOR_ID.
    """
    cleared = []
    with conn() as db:
        doc_rows = db.execute(
            "SELECT patient_telegram_id, stored_name FROM patient_documents"
        ).fetchall()
        db.execute("DELETE FROM patient_documents")
        cleared.append("patient_documents")
        for table in _FLOW_RESET_TABLES:
            db.execute(f"DELETE FROM {table}")
            cleared.append(table)
    _unlink_patient_document_files(doc_rows)
    return cleared
