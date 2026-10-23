import os
import sqlite3
from contextlib import contextmanager
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
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
        """)
        user_columns = {row["name"] for row in db.execute("PRAGMA table_info(users)").fetchall()}
        for name in ("first_name", "last_name", "username", "display_name"):
            if name not in user_columns:
                db.execute(f"ALTER TABLE users ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
        invite_columns = {row["name"] for row in db.execute("PRAGMA table_info(invites)").fetchall()}
        if "expires_at" not in invite_columns:
            db.execute("ALTER TABLE invites ADD COLUMN expires_at TEXT")
        answer_columns = {row["name"] for row in db.execute("PRAGMA table_info(answers)").fetchall()}
        if "status" not in answer_columns:
            db.execute("ALTER TABLE answers ADD COLUMN status TEXT NOT NULL DEFAULT 'completed'")
        if "result" not in answer_columns:
            db.execute("ALTER TABLE answers ADD COLUMN result TEXT NOT NULL DEFAULT '{}'")
