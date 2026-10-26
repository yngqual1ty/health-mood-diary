import json
import logging
import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote
from uuid import uuid4

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=False)

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from db import conn, init_db
from auth import validate_init_data, AuthError
from medication_notifications import APP_TIMEZONE

app = FastAPI(title="Mood Diary MVP")
WEB_DIR = Path(__file__).parent / "web"
INVITE_TTL_HOURS = 24
ADMIN_DOCTOR_ID = int(os.getenv("ADMIN_DOCTOR_ID", "0"))
QUESTIONNAIRES_PATH = BASE_DIR / "questionnaires.json"
DOCUMENTS_DIR = Path(
    os.getenv("PATIENT_DOCUMENTS_DIR", str(BASE_DIR / "patient_documents"))
)
if not DOCUMENTS_DIR.is_absolute():
    DOCUMENTS_DIR = BASE_DIR / DOCUMENTS_DIR
MAX_DOCUMENT_SIZE = 10 * 1024 * 1024
DOCUMENT_FORMATS = {
    ".pdf": ("application/pdf", lambda content: content.startswith(b"%PDF-")),
    ".jpg": ("image/jpeg", lambda content: content.startswith(b"\xff\xd8\xff")),
    ".jpeg": ("image/jpeg", lambda content: content.startswith(b"\xff\xd8\xff")),
    ".png": (
        "image/png",
        lambda content: content.startswith(b"\x89PNG\r\n\x1a\n"),
    ),
}
logger = logging.getLogger(__name__)


def questionnaire_definitions():
    try:
        data = json.loads(QUESTIONNAIRES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="Не удалось загрузить определения опросников.") from exc
    if not isinstance(data, list):
        raise HTTPException(status_code=500, detail="Некорректный формат определений опросников.")
    return {item["id"]: item for item in data if isinstance(item, dict) and item.get("id")}


def interpret_score(score, bands):
    for band in bands:
        if score <= band.get("max", -1):
            return band.get("label", "")
    return ""


def score_questionnaire(definition, answers):
    values = {str(key): int(value) for key, value in answers.items() if str(value).lstrip("-").isdigit()}
    scoring = definition.get("scoring", {})
    if scoring.get("type") == "domains":
        domains = {}
        for domain, config in scoring.get("domains", {}).items():
            total = sum(values[item["id"]] for item in definition.get("items", [])
                        if item.get("domain") == domain and item.get("id") in values)
            domains[domain] = {"score": total, "interpretation": interpret_score(total, config.get("bands", []))}
        return {"type": "domains", "domains": domains}
    if scoring.get("type") == "sum":
        total = sum(values[item["id"]] for item in definition.get("items", []) if item.get("id") in values)
        return {"type": "sum", "score": total, "interpretation": interpret_score(total, scoring.get("bands", []))}
    return {}


@app.get("/")
def index():
    return FileResponse(
        WEB_DIR / "index.html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    init_db()
    if ADMIN_DOCTOR_ID > 0:
        with conn() as db:
            db.execute(
                """
                INSERT INTO users (telegram_id, role)
                VALUES (?, 'doctor')
                ON CONFLICT(telegram_id) DO UPDATE SET role = 'doctor'
                """,
                (ADMIN_DOCTOR_ID,),
            )


def current_user(authorization: str | None):
    raw = (
        authorization[4:]
        if authorization and authorization.lower().startswith("tma ")
        else ""
    )
    try:
        data = validate_init_data(raw)
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e

    uid = data["telegram_id"]
    telegram_user = data.get("user", {})
    default_name = " ".join(
        part.strip()
        for part in (
            telegram_user.get("first_name", ""),
            telegram_user.get("last_name", ""),
        )
        if part and part.strip()
    )
    with conn() as db:
        db.execute(
            """
            INSERT INTO users (telegram_id, first_name, last_name, username, display_name)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                username = excluded.username,
                display_name = CASE
                    WHEN users.display_name = '' THEN excluded.display_name
                    ELSE users.display_name
                END
            """,
            (
                uid,
                telegram_user.get("first_name", ""),
                telegram_user.get("last_name", ""),
                telegram_user.get("username", ""),
                default_name,
            ),
        )
    return uid


class MoodIn(BaseModel):
    score: int = Field(ge=1, le=5)
    emotions: list[str] = Field(default_factory=list)
    note: str = Field(default="", max_length=2000)


class AnswersIn(BaseModel):
    questionnaire_id: str = Field(min_length=1, max_length=100)
    answers: dict
    status: str = Field(default="completed", pattern="^completed$")


class PatientNameIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)


class DoctorCreateIn(BaseModel):
    telegram_id: int = Field(gt=0)


class MedicationIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    dosage: str = Field(min_length=1, max_length=120)
    times: list[str] = Field(min_length=1, max_length=8)
    start_date: date
    end_date: date | None = None
    instructions: str = Field(default="", max_length=1000)


def normalized_medication_times(times: list[str]) -> list[str]:
    normalized = sorted(set(value.strip() for value in times))
    if len(normalized) != len(times):
        raise HTTPException(
            status_code=422,
            detail="Время приёма должно быть указано без повторов.",
        )
    if any(
        len(value) != 5
        or value[2] != ":"
        or not value[:2].isdigit()
        or not value[3:].isdigit()
        or int(value[:2]) > 23
        or int(value[3:]) > 59
        for value in normalized
    ):
        raise HTTPException(
            status_code=422,
            detail="Укажите время приёма в формате ЧЧ:ММ.",
        )
    return normalized

def require_doctor(uid: int):
    with conn() as db:
        user = db.execute(
            "SELECT role FROM users WHERE telegram_id = ?", (uid,)
        ).fetchone()
    if not user or user["role"] != "doctor":
        raise HTTPException(status_code=403, detail="Доступ только для врача.")


def require_admin_doctor(uid: int):
    if uid != ADMIN_DOCTOR_ID:
        raise HTTPException(status_code=403, detail="Недостаточно прав.")


def require_patient(uid: int):
    with conn() as db:
        user = db.execute(
            "SELECT role FROM users WHERE telegram_id = ?", (uid,)
        ).fetchone()
    if not user or user["role"] != "patient":
        raise HTTPException(status_code=403, detail="Дневник доступен только пациенту.")


@app.get("/api/me")
def me(authorization: str | None = Header(default=None)):
    uid = current_user(authorization)
    with conn() as db:
        user = db.execute(
                """SELECT telegram_id, role, first_name, last_name, username, display_name
               FROM users WHERE telegram_id = ?""",
            (uid,),
        ).fetchone()
        link = db.execute(
            "SELECT doctor_telegram_id FROM links WHERE patient_telegram_id = ?",
            (uid,),
        ).fetchone()
    return {
        "telegram_id": uid,
        "role": "doctor" if uid == ADMIN_DOCTOR_ID else user["role"],
        "is_admin": uid == ADMIN_DOCTOR_ID,
        "first_name": user["first_name"],
        "last_name": user["last_name"],
        "username": user["username"],
        "display_name": user["display_name"],
        "linked": bool(link),
        "doctor_telegram_id": link["doctor_telegram_id"] if link else None,
    }


@app.post("/api/moods")
def create_mood(body: MoodIn, authorization: str | None = Header(default=None)):
    uid = current_user(authorization)
    require_patient(uid)
    emotions = [e.strip() for e in body.emotions if e.strip()]
    note = body.note.strip()
    with conn() as db:
        db.execute(
            """INSERT INTO moods(patient_telegram_id, score, emotions, note)
               VALUES (?, ?, ?, ?)""",
            (uid, body.score, ", ".join(emotions), note),
        )
    return {"ok": True}


@app.get("/api/moods")
def list_moods(authorization: str | None = Header(default=None)):
    uid = current_user(authorization)
    require_patient(uid)
    with conn() as db:
        rows = db.execute(
            """SELECT score, emotions, note, created_at FROM moods
               WHERE patient_telegram_id = ? ORDER BY id DESC LIMIT 30""",
            (uid,),
        ).fetchall()
    return {"items": [dict(row) for row in rows]}


@app.get("/api/questionnaires")
def list_questionnaires(authorization: str | None = Header(default=None)):
    uid = current_user(authorization)
    require_patient(uid)
    definitions = questionnaire_definitions()
    with conn() as db:
        placeholders = ", ".join("?" for _ in definitions)
        rows = db.execute(
            "SELECT questionnaire_id, status, result, created_at FROM answers "
            f"WHERE patient_telegram_id = ? AND questionnaire_id IN ({placeholders}) ORDER BY id DESC",
            (uid, *definitions.keys()),
        ).fetchall()
    latest = {}
    completed = set()
    for row in rows:
        if row["status"] == "completed":
            completed.add(row["questionnaire_id"])
        if row["questionnaire_id"] not in latest:
            item = dict(row)
            item["result"] = json.loads(item["result"] or "{}")
            latest[row["questionnaire_id"]] = item
    return {
        "items": [
            {**definition, "latest": latest.get(questionnaire_id)}
            for questionnaire_id, definition in definitions.items()
            if questionnaire_id not in completed
        ]
    }


@app.get("/api/doctor/patients")
def doctor_patients(authorization: str | None = Header(default=None)):
    uid = current_user(authorization)
    require_doctor(uid)
    with conn() as db:
        rows = db.execute(
            """
            SELECT
                u.telegram_id, u.first_name, u.last_name, u.username, u.display_name,
                (SELECT m.score FROM moods m
                 WHERE m.patient_telegram_id = u.telegram_id
                 ORDER BY m.id DESC LIMIT 1) AS last_mood,
                (SELECT m.created_at FROM moods m
                 WHERE m.patient_telegram_id = u.telegram_id
                 ORDER BY m.id DESC LIMIT 1) AS last_mood_at,
                (SELECT COUNT(*) FROM answers a
                 WHERE a.patient_telegram_id = u.telegram_id) AS answers_count
            FROM links l
            JOIN users u ON u.telegram_id = l.patient_telegram_id
            WHERE l.doctor_telegram_id = ?
            ORDER BY u.first_name, u.last_name, u.telegram_id
            """,
            (uid,),
        ).fetchall()
    return {"items": [dict(row) for row in rows]}


@app.post("/api/admin/doctors")
def create_doctor(
    body: DoctorCreateIn,
    authorization: str | None = Header(default=None),
):
    uid = current_user(authorization)
    require_admin_doctor(uid)
    with conn() as db:
        db.execute(
            """
            INSERT INTO users (telegram_id, role)
            VALUES (?, 'doctor')
            ON CONFLICT(telegram_id) DO UPDATE SET role = 'doctor'
            """,
            (body.telegram_id,),
        )
    return {"ok": True, "telegram_id": body.telegram_id, "role": "doctor"}


@app.get("/api/admin/doctors")
def list_doctors(authorization: str | None = Header(default=None)):
    uid = current_user(authorization)
    require_admin_doctor(uid)
    with conn() as db:
        rows = db.execute(
            """
            SELECT telegram_id, first_name, last_name, username, display_name
            FROM users
            WHERE role = 'doctor'
            ORDER BY display_name, first_name, telegram_id
            """
        ).fetchall()
    return {
        "items": [
            {**dict(row), "is_admin": row["telegram_id"] == ADMIN_DOCTOR_ID}
            for row in rows
        ]
    }


@app.delete("/api/admin/doctors/{doctor_id}")
def delete_doctor(
    doctor_id: int,
    authorization: str | None = Header(default=None),
):
    uid = current_user(authorization)
    require_admin_doctor(uid)
    if doctor_id == ADMIN_DOCTOR_ID:
        raise HTTPException(status_code=400, detail="Главного врача удалить нельзя.")
    with conn() as db:
        changed = db.execute(
            "UPDATE users SET role = 'patient' WHERE telegram_id = ? AND role = 'doctor'",
            (doctor_id,),
        ).rowcount
        if changed != 1:
            raise HTTPException(status_code=404, detail="Врач не найден.")
        db.execute("DELETE FROM links WHERE doctor_telegram_id = ?", (doctor_id,))
    return {"ok": True}


@app.post("/api/doctor/patients/{patient_id}")
@app.patch("/api/doctor/patients/{patient_id}")
def rename_patient(
    patient_id: int,
    body: PatientNameIn,
    authorization: str | None = Header(default=None),
):
    uid = current_user(authorization)
    require_doctor(uid)
    display_name = body.display_name.strip()
    if not display_name:
        raise HTTPException(status_code=422, detail="Имя не может быть пустым.")
    with conn() as db:
        ensure_patient_link(db, uid, patient_id)
        db.execute(
            "UPDATE users SET display_name = ? WHERE telegram_id = ?",
            (display_name, patient_id),
        )
    return {"ok": True, "display_name": display_name}


def ensure_patient_link(db, doctor_id: int, patient_id: int):
    link = db.execute(
        """SELECT 1 FROM links
           WHERE doctor_telegram_id = ? AND patient_telegram_id = ?""",
        (doctor_id, patient_id),
    ).fetchone()
    if not link:
        raise HTTPException(
            status_code=404,
            detail="Пациент не найден среди ваших пациентов.",
        )


@app.delete("/api/doctor/patients/{patient_id}")
def unlink_patient(
    patient_id: int, authorization: str | None = Header(default=None)
):
    uid = current_user(authorization)
    require_doctor(uid)
    with conn() as db:
        ensure_patient_link(db, uid, patient_id)
        db.execute(
            "DELETE FROM links WHERE doctor_telegram_id = ? AND patient_telegram_id = ?",
            (uid, patient_id),
        )
    return {"ok": True, "data_preserved": True}


@app.get("/api/doctor/patients/{patient_id}/moods")
def doctor_patient_moods(
    patient_id: int, authorization: str | None = Header(default=None)
):
    uid = current_user(authorization)
    require_doctor(uid)
    with conn() as db:
        ensure_patient_link(db, uid, patient_id)
        rows = db.execute(
            """SELECT score, emotions, note, created_at FROM moods
               WHERE patient_telegram_id = ? ORDER BY id DESC LIMIT 100""",
            (patient_id,),
        ).fetchall()
    return {"items": [dict(row) for row in rows]}


@app.get("/api/doctor/patients/{patient_id}/answers")
def doctor_patient_answers(
    patient_id: int, authorization: str | None = Header(default=None)
):
    uid = current_user(authorization)
    require_doctor(uid)
    definitions = questionnaire_definitions()
    with conn() as db:
        ensure_patient_link(db, uid, patient_id)
        rows = db.execute(
            """SELECT questionnaire_id, payload, status, result, created_at FROM answers
               WHERE patient_telegram_id = ? ORDER BY id DESC LIMIT 100""",
            (patient_id,),
        ).fetchall()

    result = []
    questionnaire_metadata = {}
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(item["payload"])
        except (TypeError, json.JSONDecodeError):
            item["payload"] = {}
        try:
            item["result"] = json.loads(item["result"] or "{}")
        except (TypeError, json.JSONDecodeError):
            item["result"] = {}
        definition = definitions.get(item["questionnaire_id"])
        if definition:
            questionnaire_metadata[item["questionnaire_id"]] = {
                "title": definition.get("title", item["questionnaire_id"]),
                "items": definition.get("items", []),
                "scoring": definition.get("scoring", {}),
            }
        result.append(item)
    return {"items": result, "questionnaires": questionnaire_metadata}


@app.get("/api/doctor/patients/{patient_id}/medications")
def doctor_patient_medications(
    patient_id: int,
    for_date: date | None = Query(default=None),
    week_start: date | None = Query(default=None),
    authorization: str | None = Header(default=None),
):
    uid = current_user(authorization)
    require_doctor(uid)
    selected_date = week_start or for_date or datetime.now(APP_TIMEZONE).date()
    week_start_date = selected_date - timedelta(days=selected_date.weekday())
    week_dates = [
        (week_start_date + timedelta(days=offset)).isoformat()
        for offset in range(7)
    ]
    with conn() as db:
        ensure_patient_link(db, uid, patient_id)
        medications = db.execute(
            """
            SELECT id, name, dosage, schedule_times, start_date, end_date,
                   instructions, active
            FROM medications
            WHERE doctor_telegram_id = ? AND patient_telegram_id = ?
            ORDER BY active DESC, id DESC
            """,
            (uid, patient_id),
        ).fetchall()
        doses = db.execute(
            """
            SELECT medication_id, scheduled_time, status, notification_state,
                   notified_at, taken_at, dose_date
            FROM medication_doses
            WHERE patient_telegram_id = ? AND dose_date BETWEEN ? AND ?
            """,
            (patient_id, week_dates[0], week_dates[-1]),
        ).fetchall()

    dose_by_time = {
        (row["medication_id"], row["dose_date"], row["scheduled_time"]): dict(row)
        for row in doses
    }
    items = []
    for medication in medications:
        item = dict(medication)
        times = json.loads(item.pop("schedule_times"))
        item["times"] = times
        item["days"] = []
        for date_text in week_dates:
            in_course_dates = (
                item["start_date"] <= date_text
                and (not item["end_date"] or item["end_date"] >= date_text)
            )
            daily_doses = []
            for scheduled_time in times:
                dose = dose_by_time.get((item["id"], date_text, scheduled_time))
                if dose:
                    daily_doses.append({"time": scheduled_time, **dose})
                elif in_course_dates:
                    daily_doses.append(
                        {
                            "time": scheduled_time,
                            "status": "scheduled" if item["active"] else "cancelled",
                            "notification_state": None,
                            "notified_at": None,
                            "taken_at": None,
                        }
                    )
            item["days"].append({"date": date_text, "doses": daily_doses})
        items.append(item)
    return {
        "date": selected_date.isoformat(),
        "today": datetime.now(APP_TIMEZONE).date().isoformat(),
        "week_start": week_dates[0],
        "days": week_dates,
        "timezone": str(APP_TIMEZONE),
        "items": items,
    }


@app.post("/api/doctor/patients/{patient_id}/medications")
def create_patient_medication(
    patient_id: int,
    body: MedicationIn,
    authorization: str | None = Header(default=None),
):
    uid = current_user(authorization)
    require_doctor(uid)
    if body.end_date and body.end_date < body.start_date:
        raise HTTPException(
            status_code=422,
            detail="Дата окончания курса не может быть раньше даты начала.",
        )
    times = normalized_medication_times(body.times)
    name = body.name.strip()
    dosage = body.dosage.strip()
    if not name or not dosage:
        raise HTTPException(
            status_code=422,
            detail="Укажите название лекарства и дозировку.",
        )
    with conn() as db:
        ensure_patient_link(db, uid, patient_id)
        cursor = db.execute(
            """
            INSERT INTO medications (
                doctor_telegram_id, patient_telegram_id, name, dosage,
                schedule_times, start_date, end_date, instructions
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uid,
                patient_id,
                name,
                dosage,
                json.dumps(times),
                body.start_date.isoformat(),
                body.end_date.isoformat() if body.end_date else None,
                body.instructions.strip(),
            ),
        )
        medication_id = cursor.lastrowid
    return {"ok": True, "id": medication_id}


@app.delete("/api/doctor/patients/{patient_id}/medications/{medication_id}")
def stop_patient_medication(
    patient_id: int,
    medication_id: int,
    authorization: str | None = Header(default=None),
):
    uid = current_user(authorization)
    require_doctor(uid)
    with conn() as db:
        ensure_patient_link(db, uid, patient_id)
        changed = db.execute(
            """
            UPDATE medications SET active = 0
            WHERE id = ? AND doctor_telegram_id = ? AND patient_telegram_id = ?
              AND active = 1
            """,
            (medication_id, uid, patient_id),
        ).rowcount
        if changed != 1:
            raise HTTPException(status_code=404, detail="Назначение не найдено.")
        db.execute(
            """
            UPDATE medication_doses SET status = 'cancelled'
            WHERE medication_id = ? AND status = 'pending'
            """,
            (medication_id,),
        )
    return {"ok": True}


def linked_doctor_for_patient(db, patient_id: int) -> int:
    link = db.execute(
        "SELECT doctor_telegram_id FROM links WHERE patient_telegram_id = ?",
        (patient_id,),
    ).fetchone()
    if not link:
        raise HTTPException(status_code=404, detail="Пациент не привязан к врачу.")
    return link["doctor_telegram_id"]


def document_rows(db, doctor_id: int, patient_id: int, viewer_id: int):
    return db.execute(
        """
        SELECT id, original_filename, content_type, size_bytes, created_at,
               CASE WHEN uploaded_by_telegram_id = ? THEN 1 ELSE 0 END AS can_delete,
               CASE WHEN uploaded_by_telegram_id = patient_telegram_id
                    THEN 'patient' ELSE 'doctor' END AS uploader_role
        FROM patient_documents
        WHERE doctor_telegram_id = ? AND patient_telegram_id = ?
        ORDER BY id DESC LIMIT 100
        """,
        (viewer_id, doctor_id, patient_id),
    ).fetchall()


async def store_patient_document(
    patient_id: int,
    doctor_id: int,
    uploader_id: int,
    request: Request,
    filename: str,
):
    filename = unquote(filename)
    extension = Path(filename.replace("\\", "/")).suffix.lower()
    file_format = DOCUMENT_FORMATS.get(extension)
    if not file_format:
        raise HTTPException(
            status_code=415, detail="Разрешены только PDF, JPG и PNG."
        )
    content_type, signature_check = file_format
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_DOCUMENT_SIZE:
            raise HTTPException(status_code=413, detail="Размер файла не должен превышать 10 МБ.")
    if not signature_check(content):
        raise HTTPException(status_code=415, detail="Содержимое файла не соответствует формату.")

    original_filename = Path(filename.replace("\\", "/")).name
    original_filename = "".join(
        character for character in original_filename
        if ord(character) >= 32 and ord(character) != 127
    ).strip()
    if not original_filename or len(original_filename) > 255:
        raise HTTPException(status_code=422, detail="Некорректное имя файла.")

    stored_filename = f"{uuid4().hex}{extension}"
    target = DOCUMENTS_DIR / stored_filename
    try:
        DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        with conn() as db:
            db.execute(
                """
                INSERT INTO patient_documents (
                    patient_telegram_id, doctor_telegram_id, uploaded_by_telegram_id,
                    original_filename, stored_filename, content_type, size_bytes
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    patient_id,
                    doctor_id,
                    uploader_id,
                    original_filename,
                    stored_filename,
                    content_type,
                    len(content),
                ),
            )
    except (OSError, sqlite3.Error) as exc:
        target.unlink(missing_ok=True)
        logger.exception("Could not store patient document for patient_id=%s", patient_id)
        raise HTTPException(status_code=500, detail="Не удалось сохранить документ.") from exc
    return {"ok": True}


def document_download_response(document, document_id: int):
    path = (DOCUMENTS_DIR / document["stored_filename"]).resolve()
    if path.parent != DOCUMENTS_DIR.resolve() or not path.is_file():
        logger.error("Stored patient document is missing or invalid: document_id=%s", document_id)
        raise HTTPException(status_code=404, detail="Файл документа не найден.")
    return FileResponse(
        path,
        media_type=document["content_type"],
        filename=document["original_filename"],
        content_disposition_type="attachment",
        headers={"X-Content-Type-Options": "nosniff"},
    )


def delete_document_file(document, document_id: int):
    path = (DOCUMENTS_DIR / document["stored_filename"]).resolve()
    if path.parent != DOCUMENTS_DIR.resolve():
        logger.error("Invalid stored patient document path: document_id=%s", document_id)
        raise HTTPException(status_code=500, detail="Некорректный путь к документу.")
    try:
        path.unlink()
    except OSError as exc:
        logger.exception("Could not delete patient document id=%s", document_id)
        raise HTTPException(status_code=500, detail="Не удалось удалить файл документа.") from exc


@app.get("/api/doctor/patients/{patient_id}/documents")
def list_patient_documents(
    patient_id: int, authorization: str | None = Header(default=None)
):
    uid = current_user(authorization)
    require_doctor(uid)
    with conn() as db:
        ensure_patient_link(db, uid, patient_id)
        rows = document_rows(db, uid, patient_id, uid)
    return {"items": [dict(row) for row in rows]}


@app.post("/api/doctor/patients/{patient_id}/documents")
async def upload_patient_document(
    patient_id: int,
    request: Request,
    filename: str = Header(default="", alias="X-File-Name"),
    authorization: str | None = Header(default=None),
):
    uid = current_user(authorization)
    require_doctor(uid)
    with conn() as db:
        ensure_patient_link(db, uid, patient_id)
    return await store_patient_document(patient_id, uid, uid, request, filename)


@app.get("/api/doctor/patients/{patient_id}/documents/{document_id}")
def download_patient_document(
    patient_id: int,
    document_id: int,
    authorization: str | None = Header(default=None),
):
    uid = current_user(authorization)
    require_doctor(uid)
    with conn() as db:
        ensure_patient_link(db, uid, patient_id)
        document = db.execute(
            """
            SELECT original_filename, stored_filename, content_type
            FROM patient_documents
            WHERE id = ? AND doctor_telegram_id = ? AND patient_telegram_id = ?
            """,
            (document_id, uid, patient_id),
        ).fetchone()
    if not document:
        raise HTTPException(status_code=404, detail="Документ не найден.")
    return document_download_response(document, document_id)


@app.delete("/api/doctor/patients/{patient_id}/documents/{document_id}")
def delete_patient_document(
    patient_id: int,
    document_id: int,
    authorization: str | None = Header(default=None),
):
    uid = current_user(authorization)
    require_doctor(uid)
    with conn() as db:
        ensure_patient_link(db, uid, patient_id)
        document = db.execute(
            """
            SELECT stored_filename, uploaded_by_telegram_id FROM patient_documents
            WHERE id = ? AND doctor_telegram_id = ? AND patient_telegram_id = ?
            """,
            (document_id, uid, patient_id),
        ).fetchone()
    if not document:
        raise HTTPException(status_code=404, detail="Документ не найден.")
    if document["uploaded_by_telegram_id"] != uid:
        raise HTTPException(status_code=403, detail="Удалить можно только свой документ.")
    delete_document_file(document, document_id)
    with conn() as db:
        db.execute("DELETE FROM patient_documents WHERE id = ?", (document_id,))
    return {"ok": True}


@app.get("/api/patient/documents")
def list_own_patient_documents(
    authorization: str | None = Header(default=None),
):
    uid = current_user(authorization)
    require_patient(uid)
    with conn() as db:
        doctor_id = linked_doctor_for_patient(db, uid)
        rows = document_rows(db, doctor_id, uid, uid)
    return {"items": [dict(row) for row in rows]}


@app.post("/api/patient/documents")
async def upload_own_patient_document(
    request: Request,
    filename: str = Header(default="", alias="X-File-Name"),
    authorization: str | None = Header(default=None),
):
    uid = current_user(authorization)
    require_patient(uid)
    with conn() as db:
        doctor_id = linked_doctor_for_patient(db, uid)
    return await store_patient_document(uid, doctor_id, uid, request, filename)


@app.get("/api/patient/documents/{document_id}")
def download_own_patient_document(
    document_id: int,
    authorization: str | None = Header(default=None),
):
    uid = current_user(authorization)
    require_patient(uid)
    with conn() as db:
        doctor_id = linked_doctor_for_patient(db, uid)
        document = db.execute(
            """
            SELECT original_filename, stored_filename, content_type
            FROM patient_documents
            WHERE id = ? AND doctor_telegram_id = ? AND patient_telegram_id = ?
            """,
            (document_id, doctor_id, uid),
        ).fetchone()
    if not document:
        raise HTTPException(status_code=404, detail="Документ не найден.")
    return document_download_response(document, document_id)


@app.delete("/api/patient/documents/{document_id}")
def delete_own_patient_document(
    document_id: int,
    authorization: str | None = Header(default=None),
):
    uid = current_user(authorization)
    require_patient(uid)
    with conn() as db:
        doctor_id = linked_doctor_for_patient(db, uid)
        document = db.execute(
            """
            SELECT stored_filename, uploaded_by_telegram_id FROM patient_documents
            WHERE id = ? AND doctor_telegram_id = ? AND patient_telegram_id = ?
            """,
            (document_id, doctor_id, uid),
        ).fetchone()
    if not document:
        raise HTTPException(status_code=404, detail="Документ не найден.")
    if document["uploaded_by_telegram_id"] != uid:
        raise HTTPException(status_code=403, detail="Удалить можно только свой документ.")
    delete_document_file(document, document_id)
    with conn() as db:
        db.execute("DELETE FROM patient_documents WHERE id = ?", (document_id,))
    return {"ok": True}


@app.post("/api/answers")
def save_answers(body: AnswersIn, authorization: str | None = Header(default=None)):
    uid = current_user(authorization)
    require_patient(uid)
    definition = questionnaire_definitions().get(body.questionnaire_id)
    result = {}
    if body.questionnaire_id == "daily-checkin-v1":
        allowed_answers = {
            "sleep": {"Хорошо", "Средне", "Плохо"},
            "stress": {"Низким", "Умеренным", "Высоким"},
        }
        if not body.answers.get("sleep") or not body.answers.get("stress"):
            raise HTTPException(status_code=422, detail="Ответьте на все обязательные вопросы.")
        if any(
            not isinstance(body.answers[key], str) or body.answers[key] not in values
            for key, values in allowed_answers.items()
        ):
            raise HTTPException(status_code=422, detail="Ответ содержит недопустимое значение.")
        if set(body.answers) - {*allowed_answers, "note"}:
            raise HTTPException(status_code=422, detail="Ответ содержит неизвестный вопрос.")
        if not isinstance(body.answers.get("note", ""), str):
            raise HTTPException(status_code=422, detail="Комментарий должен быть текстом.")
    elif not definition or not definition.get("available"):
        raise HTTPException(status_code=404, detail="Опросник не найден или недоступен.")
    else:
        items = definition.get("items", [])
        if not items:
            raise HTTPException(status_code=400, detail="В опроснике пока нет вопросов.")
        item_ids = {item.get("id") for item in items}
        unknown = set(body.answers) - item_ids
        if unknown:
            raise HTTPException(status_code=422, detail="Ответ содержит неизвестный вопрос.")
        if item_ids and set(body.answers) != item_ids:
            raise HTTPException(status_code=422, detail="Ответьте на все вопросы опросника.")
        for item in items:
            value = body.answers[item["id"]]
            allowed_values = {option.get("value") for option in item.get("options", [])}
            if isinstance(value, bool) or not isinstance(value, int) or value not in allowed_values:
                raise HTTPException(status_code=422, detail="Ответ содержит недопустимое значение.")
        result = score_questionnaire(definition, body.answers)
    with conn() as db:
        db.execute(
            """INSERT INTO answers(patient_telegram_id, questionnaire_id, payload, status, result)
               VALUES (?, ?, ?, ?, ?)""",
            (uid, body.questionnaire_id, json.dumps(body.answers, ensure_ascii=False),
             body.status, json.dumps(result, ensure_ascii=False)),
        )
    return {"ok": True, "status": body.status, "result": result}


@app.post("/api/doctor/invites")
def create_doctor_invite(authorization: str | None = Header(default=None)):
    uid = current_user(authorization)
    require_doctor(uid)
    token = secrets.token_urlsafe(24).replace("-", "_")
    expires = (datetime.utcnow() + timedelta(hours=INVITE_TTL_HOURS)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    with conn() as db:
        db.execute(
            "INSERT INTO invites(token, doctor_telegram_id, expires_at) VALUES (?, ?, ?)",
            (token, uid, expires),
        )
    return {
        "token": token,
        "start_payload": f"invite_{token}",
        "expires_at": expires,
        "expires_in_hours": INVITE_TTL_HOURS,
        "bot_username": os.getenv("BOT_USERNAME", "").lstrip("@"),
    }


def consume_invite(patient_id: int, token: str) -> bool:
    # Immediate transaction prevents two concurrent redemptions of the same token.
    with conn() as db:
        db.execute("BEGIN IMMEDIATE")
        invite = db.execute(
            """
            SELECT token, doctor_telegram_id, used_by,
                   COALESCE(expires_at, datetime(created_at, '+24 hours')) AS effective_expiry
            FROM invites WHERE token = ?
            """,
            (token,),
        ).fetchone()

        if not invite or invite["used_by"] is not None:
            return False

        expiry = invite["effective_expiry"]
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        if not expiry or expiry <= now:
            return False

        existing_link = db.execute(
            "SELECT doctor_telegram_id FROM links WHERE patient_telegram_id = ?",
            (patient_id,),
        ).fetchone()
        if existing_link:
            return False

        changed = db.execute(
            "UPDATE invites SET used_by = ? WHERE token = ? AND used_by IS NULL",
            (patient_id, token),
        ).rowcount
        if changed != 1:
            return False

        db.execute(
            """INSERT INTO links(patient_telegram_id, doctor_telegram_id)
               VALUES (?, ?)""",
            (patient_id, invite["doctor_telegram_id"]),
        )
        return True
