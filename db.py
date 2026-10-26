import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=False)
DB_PATH = os.getenv("DATABASE_PATH", str(BASE_DIR / "mood.sqlite3"))
DB_PATH = str((BASE_DIR / DB_PATH).resolve()) if not os.path.isabs(DB_PATH) else DB_PATH

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
            panas_intro_sent_at TEXT
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
        """)
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
    """UTC calendar date (YYYY-MM-DD). Pills check-ins use UTC, not patient-local time."""
    return _utc_now(now).strftime("%Y-%m-%d")


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
    """Patients with a regimen, no UTC-day log, and no prompt sent yet today."""
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
    """True for a linked patient who has never received the ОСР Mini App intro."""
    with conn() as db:
        row = db.execute(
            """
            SELECT COALESCE(u.role, 'patient') AS role, u.suicide_risk_intro_sent_at
            FROM links l
            LEFT JOIN users u ON u.telegram_id = l.patient_telegram_id
            WHERE l.patient_telegram_id = ?
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


def claim_scales_intro(telegram_id, sent_at=None):
    """True if this caller may send the one-time HADS/BAI/BDI intro (after ОСР)."""
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


def claim_hotline_notice(patient_telegram_id, questionnaire_id, now=None):
    """True if this caller may send the patient hotline for this scale today (UTC)."""
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
    """Undo a failed claim so the hotline can be retried the same UTC day."""
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
    """Insert one mood for this UTC date. Returns True if inserted, False if a row already exists."""
    if score not in (1, 2, 3, 4, 5):
        raise ValueError("score must be 1–5")
    label = (label or "").strip()
    created_at = f"{day} {_utc_now(now).strftime('%H:%M:%S')}"
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
