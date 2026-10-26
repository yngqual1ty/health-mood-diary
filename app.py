import json
import logging
import os
import secrets
import urllib.error
import urllib.request
from contextvars import ContextVar
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=False)

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from db import (
    conn,
    init_db,
    get_regimen,
    upsert_regimen,
    list_pills_log,
    write_pills_log,
    PILLS_TAKEN_VALUES,
    _utc_date,
    claim_panas_intro,
    release_panas_intro,
    claim_pills_intro,
    release_pills_intro,
    claim_scales_intro,
    release_scales_intro,
    claim_hotline_notice,
    release_hotline_notice,
    has_completed_suicide_risk,
    delete_patient_document,
    list_patient_documents,
    patient_document_file,
    create_session,
    session_telegram_id,
    delete_session,
    delete_sessions_for_user,
)
from auth import validate_init_data, validate_login_widget, AuthError
from medication_notifications import APP_TIMEZONE
import clinical_rag

log = logging.getLogger(__name__)
STARTER_QUESTIONNAIRES = frozenset({"hads", "bai", "bdi"})
GATED_AFTER_OSR = frozenset({"hads", "bai", "bdi", "panas"})
ALERT_QUESTIONNAIRES = frozenset({"suicide-risk", "hads", "bdi"})
SCALES_INTRO = (
    (
        "hads",
        "Заполнить HADS",
        "Пожалуйста, заполните три стартовые шкалы. Это не диагноз — "
        "так проще отслеживать самочувствие вместе с врачом.\n\n"
        "HADS — тревога и депрессия.",
    ),
    (
        "bai",
        "Заполнить BAI",
        "BAI — тревога. Заполните, когда будет удобно.",
    ),
    (
        "bdi",
        "Заполнить BDI",
        "BDI — депрессия. Вместе с остальными шкалами её увидит ваш врач.",
    ),
)
# ОСР chart score is C-SSRS max 0–5 and cannot reach 18. Doctor alert uses
# the sum of all nine item values (max 23). See questionnaires.json scoring.rules.
OSR_SUM_THRESHOLD = 18
HADS_D_THRESHOLD = 11
BDI_THRESHOLD = 30
PANAS_INTRO_TEXT = (
    "Следующая короткая шкала — ШПАНА\n\n"
    "Она помогает отметить, насколько в последнее время были выражены "
    "положительные и отрицательные переживания. Это не диагноз и не требует "
    "срочных действий.\n\n"
    "Заполните, когда будет удобно — обычно это занимает несколько минут."
)
PILLS_INTRO_TEXT = (
    "Дневник таблеток\n\n"
    "Если вы принимаете лекарства, укажите схему один раз. "
    "Ежедневные отметки придут сообщением от бота.\n\n"
    "Это не медицинская рекомендация. Заполните, когда будет удобно."
)
HOTLINE_TEXT = (
    "Если Вы чувствуете необходимость в экстренной помощи - телефон доверия • "
    "ФГБУ «НМИЦ ПН им. В.П. Сербского» Минздрава России. "
    "Горячая линия помощи. Круглосуточно 8 (495) 637-70-70"
)

app = FastAPI(title="Mood Diary MVP")
FRONTEND_DIR = BASE_DIR / "frontend" / "out"
INVITE_TTL_HOURS = 24
SESSION_TTL_DAYS = 7
SESSION_COOKIE = "session"
ADMIN_DOCTOR_ID = int(os.getenv("ADMIN_DOCTOR_ID", "0"))
DEV_TELEGRAM_ID = int(os.getenv("DEV_TELEGRAM_ID", "0"))
QUESTIONNAIRES_PATH = BASE_DIR / "questionnaires.json"
WEB_LK_PATIENT_MESSAGE = (
    "Веб-кабинет предназначен только для врачей. "
    "Пациентам доступен дневник в приложении Telegram."
)
_session_cookie: ContextVar[str | None] = ContextVar("session_cookie", default=None)


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
    if scoring.get("type") in {"sum", "max"}:
        include_domains = scoring.get("include_domains")
        item_scores = [
            values[item["id"]]
            for item in definition.get("items", [])
            if item.get("id") in values
            and (not include_domains or item.get("domain") in include_domains)
        ]
        total = max(item_scores, default=0) if scoring.get("type") == "max" else sum(item_scores)
        return {"type": "sum", "score": total, "interpretation": interpret_score(total, scoring.get("bands", []))}
    return {}


def frontend_index():
    index = FRONTEND_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=500, detail="Интерфейс не собран. Выполните npm run build в frontend/.")
    return FileResponse(index, headers={"Cache-Control": "no-store, no-cache, must-revalidate"})


@app.get("/")
def index():
    return frontend_index()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)


@app.middleware("http")
async def bind_session_cookie(request: Request, call_next):
    token = _session_cookie.set(request.cookies.get(SESSION_COOKIE))
    try:
        return await call_next(request)
    finally:
        _session_cookie.reset(token)


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


def _upsert_telegram_profile(uid: int, first_name: str, last_name: str, username: str):
    default_name = " ".join(
        part.strip()
        for part in (first_name, last_name)
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
            (uid, first_name, last_name, username, default_name),
        )


def _session_uid(token: str | None) -> int | None:
    if not token or not token.strip():
        return None
    return session_telegram_id(token.strip())


def current_user(authorization: str | None):
    if authorization and authorization.lower().startswith("tma "):
        raw = authorization[4:].strip()
        if raw:
            try:
                data = validate_init_data(raw)
            except AuthError as e:
                raise HTTPException(status_code=401, detail=str(e)) from e
            uid = data["telegram_id"]
            telegram_user = data.get("user", {})
            _upsert_telegram_profile(
                uid,
                telegram_user.get("first_name", ""),
                telegram_user.get("last_name", ""),
                telegram_user.get("username", ""),
            )
            return uid
    if authorization and authorization.lower().startswith("bearer "):
        uid = _session_uid(authorization[7:])
        if uid:
            return uid
        raise HTTPException(status_code=401, detail="Сессия недействительна или истекла.")
    uid = _session_uid(_session_cookie.get())
    if uid:
        return uid
    if DEV_TELEGRAM_ID > 0:
        with conn() as db:
            db.execute(
                "INSERT INTO users (telegram_id) VALUES (?) ON CONFLICT(telegram_id) DO NOTHING",
                (DEV_TELEGRAM_ID,),
            )
        return DEV_TELEGRAM_ID
    raise HTTPException(status_code=401, detail="Missing Telegram initData")


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=SESSION_TTL_DAYS * 24 * 3600,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )


def _issue_session(uid: int, response: Response) -> dict:
    token = secrets.token_urlsafe(32)
    expires = (datetime.utcnow() + timedelta(days=SESSION_TTL_DAYS)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    create_session(token, uid, expires)
    _set_session_cookie(response, token)
    return {"ok": True, "token": token, "expires": expires, "telegram_id": uid}


class TelegramLoginIn(BaseModel):
    id: int
    first_name: str = ""
    last_name: str = ""
    username: str = ""
    photo_url: str = ""
    auth_date: int | str
    hash: str


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


class TreatmentQueryIn(BaseModel):
    question: str = Field(min_length=3, max_length=4000)
    include_patient_data: bool = True


class PillItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    time: str = Field(default="", max_length=40)
    note: str = Field(default="", max_length=200)


class PillsRegimenIn(BaseModel):
    items: list[PillItemIn] = Field(min_length=1, max_length=20)
    confirm: bool = False


class PillsLogIn(BaseModel):
    taken: str = Field(pattern="^(yes|no|partial)$")
    note: str = Field(default="", max_length=200)
    date: str | None = None


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


def _telegram_api_url(method: str) -> str | None:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        return None
    # TELEGRAM_PROXY is a Bot API host (TelegramAPIServer.from_base), not an HTTP proxy.
    base = os.getenv("TELEGRAM_PROXY", "").strip() or "https://api.telegram.org"
    return f"{base.rstrip('/')}/bot{token}/{method}"


def _mini_app_url(path: str) -> str | None:
    app_url = os.getenv("APP_URL", "").strip()
    if not app_url.startswith("https://"):
        return None
    return f"{app_url.rstrip('/')}/{path.strip('/')}/"


def send_telegram_message(chat_id: int, text: str, reply_markup: dict | None = None) -> bool:
    url = _telegram_api_url("sendMessage")
    if not url:
        return False
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read() or b"{}")
        return bool(isinstance(data, dict) and data.get("ok"))
    except urllib.error.HTTPError as exc:
        log.warning("Telegram вернул %s при отправке сообщения %s", exc.code, chat_id)
        return False
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        log.warning("Не удалось отправить сообщение %s", chat_id)
        return False


def send_panas_intro(telegram_id: int) -> bool:
    web_app_url = _mini_app_url("panas")
    if not web_app_url:
        return False
    return send_telegram_message(
        telegram_id,
        PANAS_INTRO_TEXT,
        {
            "inline_keyboard": [[
                {"text": "Заполнить ШПАНА", "web_app": {"url": web_app_url}},
            ]],
        },
    )


def maybe_send_panas_intro(telegram_id: int) -> None:
    """One-time ШПАНА after first HADS/BAI/BDI. Daily mood is Mini App /mood/, not PANAS."""
    if not _telegram_api_url("sendMessage") or not _mini_app_url("panas"):
        log.warning("ШПАНА: нет BOT_TOKEN или HTTPS APP_URL, пропуск")
        return
    if not claim_panas_intro(telegram_id):
        return
    if not send_panas_intro(telegram_id):
        release_panas_intro(telegram_id)


def send_pills_intro(telegram_id: int) -> bool:
    web_app_url = _mini_app_url("pills-diary")
    if not web_app_url:
        return False
    return send_telegram_message(
        telegram_id,
        PILLS_INTRO_TEXT,
        {
            "inline_keyboard": [[
                {"text": "Указать лекарства", "web_app": {"url": web_app_url}},
            ]],
        },
    )


def maybe_send_pills_intro(telegram_id: int) -> None:
    """One-time pills-diary Mini App after a completed HADS. Skipped if a regimen exists."""
    if not _telegram_api_url("sendMessage") or not _mini_app_url("pills-diary"):
        log.warning("Дневник таблеток: нет BOT_TOKEN или HTTPS APP_URL, пропуск")
        return
    if not claim_pills_intro(telegram_id):
        return
    if not send_pills_intro(telegram_id):
        release_pills_intro(telegram_id)


def send_scales_intro(telegram_id: int) -> bool:
    for path, label, text in SCALES_INTRO:
        web_app_url = _mini_app_url(path)
        if not web_app_url:
            return False
        if not send_telegram_message(
            telegram_id,
            text,
            {
                "inline_keyboard": [[
                    {"text": label, "web_app": {"url": web_app_url}},
                ]],
            },
        ):
            return False
    return True


def maybe_send_scales_intro(telegram_id: int) -> None:
    """One-time HADS/BAI/BDI after the first completed ОСР. Not sent on /start."""
    if not _telegram_api_url("sendMessage") or not _mini_app_url("hads"):
        log.warning("Стартовые шкалы: нет BOT_TOKEN или HTTPS APP_URL, пропуск")
        return
    if not claim_scales_intro(telegram_id):
        return
    if not send_scales_intro(telegram_id):
        release_scales_intro(telegram_id)


def questionnaire_item_sum(definition, answers) -> int:
    """Sum of numeric values for every item in the definition (not C-SSRS max)."""
    values = {str(key): int(value) for key, value in answers.items() if str(value).lstrip("-").isdigit()}
    return sum(
        values[item["id"]]
        for item in (definition or {}).get("items", [])
        if item.get("id") in values
    )


def patient_display_label(user_row, telegram_id: int) -> str:
    if not user_row:
        return str(telegram_id)
    name = (user_row["display_name"] or "").strip()
    if not name:
        name = " ".join(
            part.strip()
            for part in (user_row["first_name"] or "", user_row["last_name"] or "")
            if part and part.strip()
        )
    return name or str(telegram_id)


def doctor_alert_for_result(questionnaire_id: str, definition, answers, result):
    """Doctor-alert payload if this completed result crosses the scale threshold.

    HADS: domain D only (anxiety alone does not alert).
    BDI: total score.
    ОСР: sum of all item values, because stored/chart score is C-SSRS max 0–5.
    """
    if questionnaire_id == "suicide-risk":
        score = questionnaire_item_sum(definition, answers)
        if score >= OSR_SUM_THRESHOLD:
            return {
                "scale": "Оценка суицидального риска (ОСР)",
                "score_line": (
                    f"Балл: {score} (сумма пунктов; уведомление при "
                    f"{OSR_SUM_THRESHOLD} и выше)"
                ),
            }
        return None
    if questionnaire_id == "hads":
        domain = ((result or {}).get("domains") or {}).get("D") or {}
        try:
            score = int(domain.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        if score >= HADS_D_THRESHOLD:
            interp = str(domain.get("interpretation") or "").strip()
            extra = f" — {interp}" if interp else ""
            return {
                "scale": "HADS, подшкала депрессии (D)",
                "score_line": f"Балл: {score}{extra}",
            }
        return None
    if questionnaire_id == "bdi":
        try:
            score = int((result or {}).get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        if score >= BDI_THRESHOLD:
            interp = str((result or {}).get("interpretation") or "").strip()
            extra = f" — {interp}" if interp else ""
            return {
                "scale": "BDI (Бек)",
                "score_line": f"Балл: {score}{extra}",
            }
        return None
    return None


def _doctor_alert_text(patient_label: str, alert: dict, stamp: str) -> str:
    return (
        f"Пациент: {patient_label}\n\n"
        f"Шкала: {alert['scale']}\n"
        f"{alert['score_line']}\n"
        f"Время: {stamp}\n\n"
        "Это сигнал по заполненной шкале, а не диагноз. "
        "Пожалуйста, откройте карту пациента в Mini App."
    )


def send_doctor_scale_alert(doctor_id: int, patient_label: str, alert: dict, stamp: str) -> bool:
    text = _doctor_alert_text(patient_label, alert, stamp)
    doctor_url = _mini_app_url("doctor")
    markup = None
    if doctor_url:
        markup = {
            "inline_keyboard": [[
                {"text": "Открыть карту пациента", "web_app": {"url": doctor_url}},
            ]],
        }
    return send_telegram_message(doctor_id, text, markup)


def maybe_notify_scale_alerts(patient_id: int, questionnaire_id: str, definition, answers, result) -> None:
    """After a completed ОСР / HADS / BDI: doctor if threshold crossed, else patient hotline."""
    if questionnaire_id not in ALERT_QUESTIONNAIRES:
        return
    with conn() as db:
        user = db.execute(
            """
            SELECT role, display_name, first_name, last_name
            FROM users WHERE telegram_id = ?
            """,
            (patient_id,),
        ).fetchone()
        link = db.execute(
            "SELECT doctor_telegram_id FROM links WHERE patient_telegram_id = ?",
            (patient_id,),
        ).fetchone()
    if not user or user["role"] == "doctor":
        return
    alert = doctor_alert_for_result(questionnaire_id, definition, answers, result)
    if alert:
        if not link:
            return
        if not _telegram_api_url("sendMessage"):
            log.warning("Уведомление врачу: нет BOT_TOKEN, пропуск")
            return
        stamp = datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC")
        send_doctor_scale_alert(
            link["doctor_telegram_id"],
            patient_display_label(user, patient_id),
            alert,
            stamp,
        )
        return
    if not _telegram_api_url("sendMessage"):
        log.warning("Горячая линия: нет BOT_TOKEN, пропуск")
        return
    if not claim_hotline_notice(patient_id, questionnaire_id):
        return
    if not send_telegram_message(patient_id, HOTLINE_TEXT):
        release_hotline_notice(patient_id, questionnaire_id)


@app.get("/api/auth/config")
def auth_config():
    return {"bot_username": os.getenv("BOT_USERNAME", "").lstrip("@")}


@app.post("/api/auth/telegram")
def auth_telegram(body: TelegramLoginIn, response: Response):
    try:
        data = validate_login_widget(body.model_dump())
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    uid = data["telegram_id"]
    with conn() as db:
        user = db.execute(
            "SELECT role FROM users WHERE telegram_id = ?", (uid,)
        ).fetchone()
    is_admin = uid == ADMIN_DOCTOR_ID
    if not is_admin and (not user or user["role"] != "doctor"):
        raise HTTPException(status_code=403, detail=WEB_LK_PATIENT_MESSAGE)
    _upsert_telegram_profile(
        uid,
        data.get("first_name", ""),
        data.get("last_name", ""),
        data.get("username", ""),
    )
    if is_admin:
        with conn() as db:
            db.execute(
                """
                INSERT INTO users (telegram_id, role)
                VALUES (?, 'doctor')
                ON CONFLICT(telegram_id) DO UPDATE SET role = 'doctor'
                """,
                (uid,),
            )
    return _issue_session(uid, response)


@app.post("/api/auth/logout")
def auth_logout(
    response: Response,
    authorization: str | None = Header(default=None),
):
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        token = _session_cookie.get()
    delete_session(token)
    _clear_session_cookie(response)
    return {"ok": True}


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
        "suicide_risk_completed": has_completed_suicide_risk(uid),
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


def _normalize_regimen_items(items: list[PillItemIn]):
    normalized = []
    for item in items:
        name = item.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Укажите название лекарства.")
        time = item.time.strip()
        note = item.note.strip()
        entry = {"name": name}
        if time:
            entry["time"] = time
        if note:
            entry["note"] = note
        normalized.append(entry)
    if not normalized:
        raise HTTPException(status_code=422, detail="Добавьте хотя бы одно лекарство.")
    return normalized


def _regimen_payload(row):
    if not row:
        return {"configured": False, "items": []}
    return {
        "configured": True,
        "items": row["items"],
        "created_at": row["created_at"],
    }


def _parse_log_date(raw: str | None):
    today = _utc_date()
    day = (raw or today).strip()
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Дата должна быть в формате YYYY-MM-DD.") from exc
    if day > today:
        raise HTTPException(status_code=422, detail="Нельзя отметить будущий день.")
    return day


@app.get("/api/pills")
def get_pills(authorization: str | None = Header(default=None)):
    uid = current_user(authorization)
    require_patient(uid)
    return _regimen_payload(get_regimen(uid))


@app.post("/api/pills")
def save_pills(body: PillsRegimenIn, authorization: str | None = Header(default=None)):
    uid = current_user(authorization)
    require_patient(uid)
    existing = get_regimen(uid)
    if existing and not body.confirm:
        raise HTTPException(
            status_code=409,
            detail="Схема уже заполнена. Повторное сохранение только после подтверждения.",
        )
    saved = upsert_regimen(uid, _normalize_regimen_items(body.items))
    return _regimen_payload(saved)


@app.post("/api/pills/log")
def save_pills_log(body: PillsLogIn, authorization: str | None = Header(default=None)):
    uid = current_user(authorization)
    require_patient(uid)
    if not get_regimen(uid):
        raise HTTPException(status_code=409, detail="Сначала заполните схему лекарств.")
    if body.taken not in PILLS_TAKEN_VALUES:
        raise HTTPException(status_code=422, detail="Отметьте: yes, no или partial.")
    day = _parse_log_date(body.date)
    row, _created = write_pills_log(uid, day, body.taken, body.note, overwrite=True)
    return {"ok": True, "item": row}


@app.get("/api/doctor/patients/{patient_id}/pills")
def doctor_patient_pills(
    patient_id: int, authorization: str | None = Header(default=None)
):
    uid = current_user(authorization)
    require_doctor(uid)
    with conn() as db:
        ensure_patient_link(db, uid, patient_id)
    regimen = get_regimen(patient_id)
    since = (datetime.utcnow() - timedelta(days=29)).strftime("%Y-%m-%d")
    log = list_pills_log(patient_id, since, limit=30)
    payload = _regimen_payload(regimen)
    payload["log"] = log
    return payload


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
    for row in rows:
        if row["questionnaire_id"] not in latest:
            item = dict(row)
            item["result"] = json.loads(item["result"] or "{}")
            latest[row["questionnaire_id"]] = item
    return {
        "items": [
            {**definition, "latest": latest.get(questionnaire_id)}
            for questionnaire_id, definition in definitions.items()
            if definition.get("available")
        ]
    }


def parse_answer_row(row) -> dict:
    item = dict(row)
    for key in ("payload", "result"):
        if key not in item:
            continue
        try:
            item[key] = json.loads(item[key] or "{}")
        except (TypeError, json.JSONDecodeError):
            item[key] = {}
    return item


def series_from_answers(answers: list[dict], definitions: dict) -> dict:
    """One latest completed attempt per calendar day for each questionnaire."""
    by_id: dict[str, dict[str, dict]] = {}
    for item in answers:
        if item.get("status") != "completed":
            continue
        qid = item["questionnaire_id"]
        if qid not in definitions:
            continue
        day = (item.get("created_at") or "")[:10]
        if not day:
            continue
        by_id.setdefault(qid, {})[day] = item
    series = {}
    for qid, by_day in by_id.items():
        definition = definitions[qid]
        scoring = definition.get("scoring") or {}
        points = []
        for day in sorted(by_day):
            result = by_day[day].get("result") or {}
            point = {"date": day, "created_at": by_day[day].get("created_at")}
            if result.get("type") == "sum":
                point["score"] = result.get("score")
                point["interpretation"] = result.get("interpretation")
            elif result.get("type") == "domains":
                point["domains"] = result.get("domains") or {}
            points.append(point)
        series[qid] = {
            "id": qid,
            "title": definition.get("title", qid),
            "scoring": scoring,
            "points": points,
        }
    return series


@app.get("/api/questionnaires/{questionnaire_id}")
def get_questionnaire(questionnaire_id: str, authorization: str | None = Header(default=None)):
    uid = current_user(authorization)
    require_patient(uid)
    definition = questionnaire_definitions().get(questionnaire_id)
    if not definition or not definition.get("available"):
        raise HTTPException(status_code=404, detail="Опросник не найден или недоступен.")
    with conn() as db:
        rows = db.execute(
            """SELECT questionnaire_id, payload, status, result, created_at FROM answers
               WHERE patient_telegram_id = ? AND questionnaire_id = ?
               ORDER BY id DESC LIMIT 60""",
            (uid, questionnaire_id),
        ).fetchall()
    history = [parse_answer_row(row) for row in rows]
    return {**definition, "latest": history[0] if history else None, "history": history}


@app.get("/api/me/results")
def my_results(authorization: str | None = Header(default=None)):
    uid = current_user(authorization)
    require_patient(uid)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    definitions = {
        qid: definition
        for qid, definition in questionnaire_definitions().items()
        if definition.get("available")
    }
    with conn() as db:
        moods = [
            dict(row)
            for row in db.execute(
                """SELECT score, emotions, created_at FROM moods
                   WHERE patient_telegram_id = ? ORDER BY id ASC""",
                (uid,),
            ).fetchall()
            if (row["created_at"] or "")[:10] < today
        ]
        answers = [
            parse_answer_row(row)
            for row in db.execute(
                """SELECT questionnaire_id, payload, status, result, created_at FROM answers
                   WHERE patient_telegram_id = ? ORDER BY id ASC""",
                (uid,),
            ).fetchall()
            if (row["created_at"] or "")[:10] < today
        ]
    return {
        "today": today,
        "moods": moods,
        "series": series_from_answers(answers, definitions),
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
    delete_sessions_for_user(doctor_id)
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


@app.get("/api/doctor/patients/{patient_id}/documents")
def doctor_patient_documents(
    patient_id: int, authorization: str | None = Header(default=None)
):
    uid = current_user(authorization)
    require_doctor(uid)
    with conn() as db:
        ensure_patient_link(db, uid, patient_id)
    return {"items": list_patient_documents(patient_id)}


@app.get("/api/doctor/patients/{patient_id}/documents/{doc_id}")
def doctor_patient_document_file(
    patient_id: int, doc_id: int, authorization: str | None = Header(default=None)
):
    uid = current_user(authorization)
    require_doctor(uid)
    with conn() as db:
        ensure_patient_link(db, uid, patient_id)
    found = patient_document_file(patient_id, doc_id)
    if not found:
        raise HTTPException(status_code=404, detail="Документ не найден.")
    row, path = found
    return FileResponse(
        path,
        media_type=row.get("mime") or "application/octet-stream",
        filename=row.get("original_name") or path.name,
    )


@app.delete("/api/doctor/patients/{patient_id}/documents/{doc_id}")
def doctor_delete_patient_document(
    patient_id: int, doc_id: int, authorization: str | None = Header(default=None)
):
    uid = current_user(authorization)
    require_doctor(uid)
    with conn() as db:
        ensure_patient_link(db, uid, patient_id)
    if not delete_patient_document(patient_id, doc_id):
        raise HTTPException(status_code=404, detail="Документ не найден.")
    return {"ok": True}


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


@app.get("/api/doctor/guidelines")
def guidelines_status(authorization: str | None = Header(default=None)):
    uid = current_user(authorization)
    require_doctor(uid)
    return clinical_rag.status()


@app.post("/api/admin/guidelines/reload")
def reload_guidelines(authorization: str | None = Header(default=None)):
    uid = current_user(authorization)
    require_admin_doctor(uid)
    clinical_rag.get_index(reload=True)
    return clinical_rag.status()


@app.post("/api/doctor/patients/{patient_id}/treatment-options")
def treatment_options(
    patient_id: int,
    body: TreatmentQueryIn,
    authorization: str | None = Header(default=None),
):
    uid = current_user(authorization)
    require_doctor(uid)
    question = body.question.strip()
    with conn() as db:
        ensure_patient_link(db, uid, patient_id)
        moods, answers = [], []
        if body.include_patient_data:
            moods = [dict(row) for row in db.execute(
                """SELECT score, emotions, created_at FROM moods
                   WHERE patient_telegram_id = ? ORDER BY id DESC LIMIT 60""",
                (patient_id,),
            ).fetchall()]
            for row in db.execute(
                """SELECT questionnaire_id, payload, status, result, created_at FROM answers
                   WHERE patient_telegram_id = ? ORDER BY id DESC LIMIT 100""",
                (patient_id,),
            ).fetchall():
                item = dict(row)
                for key in ("payload", "result"):
                    try:
                        item[key] = json.loads(item[key] or "{}")
                    except json.JSONDecodeError:
                        item[key] = {}
                answers.append(item)

    patient = None
    if body.include_patient_data:
        patient = clinical_rag.summarize_patient(moods, answers, questionnaire_definitions())
    try:
        response = clinical_rag.propose_treatments(question, patient)
    except clinical_rag.RagError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    with conn() as db:
        db.execute(
            """INSERT INTO treatment_suggestions(doctor_telegram_id, patient_telegram_id, question, response)
               VALUES (?, ?, ?, ?)""",
            (uid, patient_id, question, json.dumps(response, ensure_ascii=False)),
        )
    return response


@app.post("/api/answers")
def save_answers(body: AnswersIn, authorization: str | None = Header(default=None)):
    uid = current_user(authorization)
    require_patient(uid)
    if body.questionnaire_id in GATED_AFTER_OSR and not has_completed_suicide_risk(uid):
        raise HTTPException(
            status_code=403,
            detail="Сначала заполните оценку суицидального риска.",
        )
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
    if body.questionnaire_id == "suicide-risk" and body.status == "completed":
        maybe_send_scales_intro(uid)
    if body.questionnaire_id in STARTER_QUESTIONNAIRES and body.status == "completed":
        maybe_send_panas_intro(uid)
    if body.questionnaire_id == "hads" and body.status == "completed":
        maybe_send_pills_intro(uid)
    if body.status == "completed":
        maybe_notify_scale_alerts(uid, body.questionnaire_id, definition, body.answers, result)
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


@app.get("/{full_path:path}")
def frontend(full_path: str):
    if full_path.startswith("api/") or full_path == "api":
        raise HTTPException(status_code=404, detail="Not found")
    file = FRONTEND_DIR / full_path
    if file.is_file():
        return FileResponse(file)
    nested = FRONTEND_DIR / full_path / "index.html"
    if nested.is_file():
        return FileResponse(nested, headers={"Cache-Control": "no-store, no-cache, must-revalidate"})
    html = FRONTEND_DIR / f"{full_path}.html"
    if html.is_file():
        return FileResponse(html, headers={"Cache-Control": "no-store, no-cache, must-revalidate"})
    return frontend_index()


if (FRONTEND_DIR / "_next").is_dir():
    app.mount("/_next", StaticFiles(directory=FRONTEND_DIR / "_next"), name="next-assets")
