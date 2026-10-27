import hashlib
import json
import logging
import os
import secrets
import urllib.error
import urllib.request
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
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
    APP_TIMEZONE,
    conn,
    init_db,
    get_regimen,
    upsert_regimen,
    list_pills_log,
    write_pills_log,
    PILLS_TAKEN_VALUES,
    _utc_date,
    _utc_stamp,
    app_calendar_date,
    to_utc_iso,
    utc_iso_fields,
    claim_panas_intro,
    release_panas_intro,
    claim_pills_intro,
    release_pills_intro,
    claim_scales_intro,
    release_scales_intro,
    claim_scales_repeat_hint,
    release_scales_repeat_hint,
    claim_mood_intro,
    release_mood_intro,
    claim_mood_followup,
    release_mood_followup,
    claim_bot_menu,
    release_bot_menu,
    list_sleep_log,
    write_sleep_log,
    SLEEP_QUALITY_VALUES,
    claim_hotline_notice,
    release_hotline_notice,
    suicide_risk_unblocked,
    delete_patient_document,
    list_patient_documents,
    patient_document_file,
    create_session,
    session_telegram_id,
    delete_session,
    delete_sessions_for_user,
    reset_dev_data,
    seed_dev_patient_data,
)
from auth import validate_init_data, validate_login_widget, AuthError
import clinical_rag

log = logging.getLogger(__name__)
STARTER_QUESTIONNAIRES = frozenset({"hads", "bai", "bdi"})
ALERT_QUESTIONNAIRES = frozenset({"suicide-risk", "hads", "bdi"})
SCALES_INTRO_TEXT = (
    "Ниже я отправил тебе 3 дополнительных входных шкалы, они измерят твой уровень депрессии и тревоги.\n\n"
    "До тех пор пока у тебя нет прикрепленного врача, мы НИКОМУ не передаем эти данные"
)
SKIP_SCALES_CALLBACK = "skip:scales"
SKIP_ALLERGIES_CALLBACK = "skip:allergies"
OSR_THANKS_TEXT = "Спасибо за твой ответ! Это помогает врачу лучше понять тебя"
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
# Once ever per patient after first completed HADS/BDI/BAI. Not per scale and
# not on every later fill — the text is a reminder-policy explanation, and
# HADS already may send ШПАНА + pills intro + hotline in the same turn.
SCALES_REPEAT_HINT_TEXT = (
    "Эти опросники ты сможешь снова пройти в любое время.\n"
    "Я рекомендую проходить их как минимум раз в две недели, напоминание отправлю в этот чат"
)
# Once ever per patient after the HADS/BDI/BAI repeat hint. Same trigger, next
# message. Daily mood is Mini App /mood/, not PANAS and not ОСР.
MOOD_INTRO_TEXT = (
    "Для ежедневной оценки состояния я пришлю визуально-аналоговую шкалу, выбери то, что ближе тебе"
)
# Once ever after the first successful mood fill. Onboarding closer, not a daily
# reminder — later /mood/ taps must not send this again.
MOOD_FOLLOWUP_TEXT = (
    "Такую шкалу я буду присылать ежедневно для мониторинга твоего состояния\n"
    "\n"
    "Помимо этого ты можешь заполнить дневник сна, дневник приема таблеточек, я напомню о них, когда придет время.\n"
    "\n"
    "Если ты хочешь, чтобы твой врач увидел другие заключения или результаты анализов - просто отправь их в этот чат, они прикрепятся к твоему аккаунту автоматически.\n"
    "\n"
    "По кнопке ниже - можешь указать аллергии"
)
# Once ever after allergies save or Telegram «Пропустить». Inline Mini App
# keyboard, not a daily reminder. Later allergy edits must not send this again.
BOT_MENU_THANKS_TEXT = (
    "Спасибо за заполнение!\n"
    "Отправил тебе клавиатуру для заполнения дневника сна, таблеток, и вообще всего функционала бота"
)
SEND_CONCLUSION_BUTTON = "Отправить заключение"
SEND_CONCLUSION_CALLBACK = "send:conclusion"
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


def _ensure_admin_doctor():
    if ADMIN_DOCTOR_ID <= 0:
        return
    with conn() as db:
        db.execute(
            """
            INSERT INTO users (telegram_id, role)
            VALUES (?, 'doctor')
            ON CONFLICT(telegram_id) DO UPDATE SET role = 'doctor'
            """,
            (ADMIN_DOCTOR_ID,),
        )


@app.on_event("startup")
def startup():
    init_db()
    _ensure_admin_doctor()


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


class AllergiesIn(BaseModel):
    text: str = Field(default="", max_length=2000)


class PatientNoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


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


class SleepIn(BaseModel):
    quality: str = Field(pattern="^(good|ok|bad)$")
    hours: float | None = Field(default=None, ge=0, le=24)
    note: str = Field(default="", max_length=2000)
    date: str | None = None


class SeedIn(BaseModel):
    telegram_id: int = Field(gt=0)


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


_MARKUP_ROOT_KEYS = ("inline_keyboard", "keyboard", "remove_keyboard", "force_reply")


def _web_app_keyboard_button(text: str, path: str) -> dict | None:
    url = _mini_app_url(path)
    if not url:
        return None
    return {"text": text, "web_app": {"url": url}}


def _send_message_reply_markup(reply_markup: dict) -> dict:
    """Pass ReplyKeyboardMarkup (`keyboard`) and InlineKeyboardMarkup through as-is.

    Do not wrap a persistent reply keyboard as `inline_keyboard` — that drops
    KeyboardButton.web_app and Telegram would treat the keys as plain text.
    """
    if any(key in reply_markup for key in _MARKUP_ROOT_KEYS):
        return reply_markup
    return {"inline_keyboard": reply_markup}


def send_telegram_message(chat_id: int, text: str, reply_markup: dict | None = None) -> bool:
    url = _telegram_api_url("sendMessage")
    if not url:
        return False
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = _send_message_reply_markup(reply_markup)
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
    # if not send_panas_intro(telegram_id):
    #     release_panas_intro(telegram_id)


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


def send_mood_intro(telegram_id: int) -> bool:
    web_app_url = _mini_app_url("mood")
    if not web_app_url:
        return False
    return send_telegram_message(
        telegram_id,
        MOOD_INTRO_TEXT,
        {
            "inline_keyboard": [[
                {"text": "Оценить настроение", "web_app": {"url": web_app_url}},
            ]],
        },
    )


def maybe_send_mood_intro(telegram_id: int, allow_skip: bool = False) -> None:
    """One-time mood Mini App after the HADS/BDI/BAI repeat hint. Not ОСР/ШПАНА."""
    if not _telegram_api_url("sendMessage") or not _mini_app_url("mood"):
        log.warning("Шкала настроения: нет BOT_TOKEN или HTTPS APP_URL, пропуск")
        return
    if not claim_mood_intro(telegram_id, allow_skip=allow_skip):
        return
    if not send_mood_intro(telegram_id):
        release_mood_intro(telegram_id)


def send_mood_followup(telegram_id: int) -> bool:
    web_app_url = _mini_app_url("allergies")
    if not web_app_url:
        return False
    return send_telegram_message(
        telegram_id,
        MOOD_FOLLOWUP_TEXT,
        {
            "inline_keyboard": [
                [{"text": "Указать аллергии", "web_app": {"url": web_app_url}}],
                [{"text": "Пропустить", "callback_data": SKIP_ALLERGIES_CALLBACK}],
            ],
        },
    )


def bot_menu_markup() -> dict | None:
    mood = _web_app_keyboard_button("Оценить настроение", "mood")
    sleep = _web_app_keyboard_button("Дневник сна", "sleep")
    pills = _web_app_keyboard_button("Дневник таблеток", "pills-diary")
    allergies = _web_app_keyboard_button("Указать аллергии", "allergies")
    if not (mood and sleep and pills and allergies):
        return None
    return {
        "inline_keyboard": [
            [mood],
            [sleep, pills],
            [allergies, {"text": SEND_CONCLUSION_BUTTON, "callback_data": SEND_CONCLUSION_CALLBACK}],
        ],
    }


def send_bot_menu(telegram_id: int) -> bool:
    markup = bot_menu_markup()
    if not markup:
        return False
    return send_telegram_message(telegram_id, BOT_MENU_THANKS_TEXT, markup)


def maybe_send_bot_menu(telegram_id: int) -> None:
    """Once-ever thanks + inline Mini App keyboard after allergies save or skip."""
    if not _telegram_api_url("sendMessage") or not bot_menu_markup():
        log.warning("Клавиатура бота: нет BOT_TOKEN или HTTPS APP_URL, пропуск")
        return
    if not claim_bot_menu(telegram_id):
        return
    if not send_bot_menu(telegram_id):
        release_bot_menu(telegram_id)


def maybe_send_mood_followup(telegram_id: int) -> None:
    """Once-ever closer after the first saved mood. Not on later /mood/ taps."""
    if not _telegram_api_url("sendMessage") or not _mini_app_url("allergies"):
        log.warning("После настроения: нет BOT_TOKEN или HTTPS APP_URL, пропуск")
        return
    if not claim_mood_followup(telegram_id):
        return
    if not send_mood_followup(telegram_id):
        release_mood_followup(telegram_id)


def maybe_send_scales_repeat_hint(telegram_id: int, allow_skip: bool = False) -> None:
    """Once-ever Telegram hint after first completed HADS, BDI, or BAI.

    Debounce: `users.scales_repeat_hint_sent_at` — one send per patient, ever.
    Chosen over every completion / once per scale so the biweekly-reminder
    explanation is not repeated with other HADS follow-ups (ШПАНА, pills, hotline).
    Not sent after ОСР or ШПАНА.
    After a successful hint, send the one-time mood Mini App (hint, then mood).
    If the hint already went out, still try mood so a failed first send can retry.
    `allow_skip` is the Telegram «Пропустить» on the 3-scales message: same pack,
    no pills intro (pills still wait for a completed HADS).
    """
    if not _telegram_api_url("sendMessage"):
        log.warning("Повтор шкал: нет BOT_TOKEN, пропуск")
        return
    if not claim_scales_repeat_hint(telegram_id, allow_skip=allow_skip):
        maybe_send_mood_intro(telegram_id, allow_skip=allow_skip)
        return
    if not send_telegram_message(telegram_id, SCALES_REPEAT_HINT_TEXT):
        release_scales_repeat_hint(telegram_id)
        return
    maybe_send_mood_intro(telegram_id, allow_skip=allow_skip)


def send_scales_intro(telegram_id: int) -> bool:
    hads_url = _mini_app_url("hads")
    bdi_url = _mini_app_url("bdi")
    bai_url = _mini_app_url("bai")
    if not (hads_url and bdi_url and bai_url):
        return False
    return send_telegram_message(
        telegram_id,
        SCALES_INTRO_TEXT,
        {
            "inline_keyboard": [
                [{"text": "HADS", "web_app": {"url": hads_url}}],
                [{"text": "BDI", "web_app": {"url": bdi_url}}],
                [{"text": "BAI", "web_app": {"url": bai_url}}],
                [{"text": "Пропустить", "callback_data": SKIP_SCALES_CALLBACK}],
            ],
        },
    )


def maybe_send_scales_intro(telegram_id: int, *, force: bool = False) -> None:
    """HADS/BDI/BAI message after completed or skipped ОСР.

    One-shot via `starter_intro_sent_at` unless `force` (later Telegram «Пропустить»).
    """
    if not _telegram_api_url("sendMessage") or not _mini_app_url("hads"):
        log.warning("Стартовые шкалы: нет BOT_TOKEN или HTTPS APP_URL, пропуск")
        return
    if force:
        if send_scales_intro(telegram_id):
            claim_scales_intro(telegram_id)
        return
    if not claim_scales_intro(telegram_id):
        return
    if not send_scales_intro(telegram_id):
        release_scales_intro(telegram_id)


def maybe_send_osr_patient_followup(telegram_id: int, *, force: bool = False) -> None:
    """After completed or skipped ОСР: thanks, then starter scales. Doctor alert is separate.

    Completing ОСР in the Mini App stays one-shot. Telegram «Пропустить» passes
    `force=True` so thanks + HADS/BDI/BAI are sent again on every skip.
    """
    if not _telegram_api_url("sendMessage"):
        log.warning("Ответ после ОСР: нет BOT_TOKEN, пропуск")
        maybe_send_scales_intro(telegram_id, force=force)
        return
    if force:
        send_telegram_message(telegram_id, OSR_THANKS_TEXT)
        maybe_send_scales_intro(telegram_id, force=True)
        return
    with conn() as db:
        row = db.execute(
            "SELECT starter_intro_sent_at FROM users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
    if not (row and row["starter_intro_sent_at"]):
        send_telegram_message(telegram_id, OSR_THANKS_TEXT)
    maybe_send_scales_intro(telegram_id)


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


def maybe_send_patient_hotline(patient_id: int, questionnaire_id: str) -> None:
    """Serbsky hotline to the patient, at most once per scale per APP_TIMEZONE day."""
    if not _telegram_api_url("sendMessage"):
        log.warning("Горячая линия: нет BOT_TOKEN, пропуск")
        return
    if not claim_hotline_notice(patient_id, questionnaire_id):
        return
    if not send_telegram_message(patient_id, HOTLINE_TEXT):
        release_hotline_notice(patient_id, questionnaire_id)


def maybe_notify_scale_alerts(patient_id: int, questionnaire_id: str, definition, answers, result) -> None:
    """Doctor if ОСР sum≥18 / HADS-D≥11 / BDI≥30. Patient hotline only if HADS-D≥11 or BDI≥30."""
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
    if not alert:
        return
    if link:
        if not _telegram_api_url("sendMessage"):
            log.warning("Уведомление врачу: нет BOT_TOKEN, пропуск")
        else:
            stamp = datetime.now(APP_TIMEZONE).strftime("%d.%m.%Y %H:%M %Z")
            send_doctor_scale_alert(
                link["doctor_telegram_id"],
                patient_display_label(user, patient_id),
                alert,
                stamp,
            )
    if questionnaire_id in {"hads", "bdi"}:
        maybe_send_patient_hotline(patient_id, questionnaire_id)


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


def _dev_reset_token_ok(provided: str | None) -> bool:
    expected = os.getenv("DEV_RESET_TOKEN", "").strip()
    if not expected:
        return False
    left = hashlib.sha256(expected.encode("utf-8")).digest()
    right = hashlib.sha256((provided or "").encode("utf-8")).digest()
    return secrets.compare_digest(left, right)


@app.post("/api/dev/reset", include_in_schema=False)
def dev_reset(x_dev_reset_token: str | None = Header(default=None)):
    if not _dev_reset_token_ok(x_dev_reset_token):
        raise HTTPException(status_code=403, detail="Forbidden")
    cleared = reset_dev_data()
    _ensure_admin_doctor()
    return {"ok": True, "cleared": cleared}


SEED_WINDOW_DAYS = 14
SEED_QUESTIONNAIRES = ("suicide-risk", "hads", "bdi", "bai", "panas")
_SEED_MOOD_SCORES = (3, 4, 2, 3, 4, 5, 3, 4, 2, 3, 5, 4, 3, 4, 5)
_SEED_MOOD_EMOTIONS = (
    "спокойствие",
    "усталость",
    "тревога",
    "надежда",
    "раздражение",
    "радость",
    "",
    "спокойствие, усталость",
    "тревога",
    "",
    "радость",
    "надежда",
    "усталость",
    "спокойствие",
    "радость",
)
_SEED_MOOD_NOTES = (
    "День как обычно",
    "",
    "Сон был коротким",
    "Прогулка вечером",
    "",
    "Хороший день на работе",
    "",
    "Немного тревожно утром",
    "",
    "Спокойный вечер",
    "Встреча с друзьями",
    "",
    "Устал после дня",
    "Почти без переживаний",
    "День прошёл ровно",
)
_SEED_SLEEP_QUALITY = (
    "ok", "good", "bad", "ok", "good",
    "ok", "good", "ok", "bad", "ok",
    "good", "ok", "good", "ok", "good",
)
_SEED_PILLS_TAKEN = (
    "yes", "yes", "partial", "yes", "yes",
    "yes", "no", "yes", "yes", "partial",
    "yes", "yes", "yes", "yes", "yes",
)
_SEED_NOTE_TEXTS = (
    (2, "Неделя началась тяжеловато, но без срывов."),
    (7, "Сон лучше, настроение ровнее, чем неделю назад."),
    (12, "Таблетки почти не пропускаю. Хочу обсудить схему на приёме."),
)
_SEED_REGIMEN = (
    {"name": "Сертралин", "time": "09:00", "note": "после завтрака"},
    {"name": "Мелатонин", "time": "22:00"},
)
_SEED_ALLERGIES = "Пенициллин, пыльца берёзы"


def _seed_calendar_days():
    today = datetime.now(APP_TIMEZONE).date()
    start = today - timedelta(days=SEED_WINDOW_DAYS)
    return [start + timedelta(days=offset) for offset in range(SEED_WINDOW_DAYS + 1)]


def _moscow_naive_stamp(day: date, hour: int, minute: int) -> str:
    return f"{day.isoformat()} {hour:02d}:{minute:02d}:00"


def _utc_naive_for_moscow(day: date, hour: int, minute: int) -> str:
    local = datetime(day.year, day.month, day.day, hour, minute, 0, tzinfo=APP_TIMEZONE)
    return local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _option_values(item) -> list[int]:
    values = []
    for option in item.get("options") or []:
        value = option.get("value")
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            values.append(value)
        elif isinstance(value, str) and value.lstrip("-").isdigit():
            values.append(int(value))
    return sorted(set(values))


def _value_at_intensity(values: list[int], intensity: float) -> int:
    t = min(1.0, max(0.0, intensity))
    index = int(round(t * (len(values) - 1)))
    return values[index]


def _lower_option_value(item, current: int) -> int | None:
    values = [value for value in _option_values(item) if value < current]
    return values[-1] if values else None


def _sample_questionnaire_answers(definition, intensity: float, fill_index: int) -> dict:
    answers = {}
    qid = definition.get("id")
    for index, item in enumerate(definition.get("items") or []):
        values = _option_values(item)
        if not values:
            continue
        domain = item.get("domain")
        wave = 0.06 * ((index + fill_index * 3) % 7 - 3)
        level = intensity + wave
        if qid == "hads" and domain == "D":
            level = 0.08 + fill_index * 0.16 + level * 0.32
        elif qid == "bdi":
            level = 0.18 + fill_index * 0.08 + intensity * 0.38 + wave
        elif qid == "suicide-risk":
            level = 0.05 if index == 0 else 0.0
        elif qid == "panas" and domain == "NA":
            level = 0.15 + level * 0.25
        elif qid == "panas" and domain == "PA":
            level = 0.45 + level * 0.35
        if item.get("id") == "bdi-9":
            answers[item["id"]] = min(values)
            continue
        answers[item["id"]] = _value_at_intensity(values, level)
    return answers


def _clamp_seed_answers(qid: str, definition, answers: dict) -> tuple[dict, dict]:
    items = definition.get("items") or []

    def over_alert() -> bool:
        result = score_questionnaire(definition, answers)
        if qid == "hads":
            score = int(((result.get("domains") or {}).get("D") or {}).get("score") or 0)
            return score >= HADS_D_THRESHOLD
        if qid == "bdi":
            return int((result.get("score") or 0)) >= BDI_THRESHOLD
        if qid == "suicide-risk":
            return questionnaire_item_sum(definition, answers) >= OSR_SUM_THRESHOLD
        return False

    guard = 0
    while over_alert() and guard < 200:
        guard += 1
        candidates = items
        if qid == "hads":
            domain_items = [item for item in items if item.get("domain") == "D"]
            candidates = domain_items or items
        reduced = False
        for item in reversed(candidates):
            item_id = item.get("id")
            if item_id not in answers:
                continue
            lower = _lower_option_value(item, answers[item_id])
            if lower is None:
                continue
            answers[item_id] = lower
            reduced = True
            break
        if not reduced:
            break
    return answers, score_questionnaire(definition, answers)


def _build_seed_answers(days: list[date]) -> list[dict]:
    definitions = questionnaire_definitions()
    missing = [qid for qid in SEED_QUESTIONNAIRES if qid not in definitions]
    if missing:
        raise HTTPException(
            status_code=500,
            detail=f"Нет определений опросников: {', '.join(missing)}",
        )
    # Spread across the window so /api/me/results (excludes today) still has history.
    last = len(days) - 1
    yesterday = max(0, last - 1)
    schedule = (
        ("suicide-risk", 0, 0.1),
        ("hads", 1, 0.38),
        ("bdi", 2, 0.34),
        ("bai", 3, 0.40),
        ("panas", 6, 0.50),
        ("bai", min(10, yesterday), 0.52),
        ("bdi", min(12, yesterday), 0.46),
        ("hads", yesterday, 0.55),
    )
    fills: dict[str, int] = {}
    rows = []
    used_slots: set[tuple[str, str]] = set()
    for qid, day_index, intensity in schedule:
        day_index = min(max(day_index, 0), last)
        day = days[day_index]
        slot = (qid, day.isoformat())
        if slot in used_slots:
            continue
        used_slots.add(slot)
        fill_index = fills.get(qid, 0)
        fills[qid] = fill_index + 1
        definition = definitions[qid]
        payload = _sample_questionnaire_answers(definition, intensity, fill_index)
        payload, result = _clamp_seed_answers(qid, definition, payload)
        hour = 11 + (fill_index * 2 + day_index) % 8
        minute = 10 + (day_index * 7) % 40
        rows.append(
            {
                "questionnaire_id": qid,
                "day": day.isoformat(),
                "payload": payload,
                "result": result,
                "created_at": _utc_naive_for_moscow(day, hour, minute),
            }
        )
    return rows


def _build_seed_payload():
    days = _seed_calendar_days()
    moods = []
    sleep_rows = []
    pills_logs = []
    for index, day in enumerate(days):
        iso = day.isoformat()
        mood_hour = 9 + (index * 3) % 12
        mood_minute = 5 + (index * 11) % 50
        score = _SEED_MOOD_SCORES[index % len(_SEED_MOOD_SCORES)]
        moods.append(
            {
                "day": iso,
                "score": score,
                "emotions": _SEED_MOOD_EMOTIONS[index % len(_SEED_MOOD_EMOTIONS)],
                "note": _SEED_MOOD_NOTES[index % len(_SEED_MOOD_NOTES)],
                "created_at": _moscow_naive_stamp(day, mood_hour, mood_minute),
            }
        )
        hours = round(6.0 + (index % 5) * 0.6 + (0.4 if index % 3 else 0), 1)
        sleep_rows.append(
            {
                "date": iso,
                "quality": _SEED_SLEEP_QUALITY[index % len(_SEED_SLEEP_QUALITY)],
                "hours": hours,
                "note": "Короткий сон" if hours < 6.5 else "",
            }
        )
        taken = _SEED_PILLS_TAKEN[index % len(_SEED_PILLS_TAKEN)]
        pills_logs.append(
            {
                "date": iso,
                "taken": taken,
                "note": "Пропустил вечернюю" if taken != "yes" else "",
            }
        )
    notes = [
        {
            "day": days[offset].isoformat(),
            "text": text,
            "created_at": _moscow_naive_stamp(days[offset], 18, 20 + offset),
        }
        for offset, text in _SEED_NOTE_TEXTS
        if offset < len(days)
    ]
    return {
        "days": [day.isoformat() for day in days],
        "moods": moods,
        "sleep_rows": sleep_rows,
        "pills_logs": pills_logs,
        "notes": notes,
        "answers": _build_seed_answers(days),
    }


def _run_dev_seed(telegram_id: int) -> dict:
    if telegram_id <= 0:
        raise HTTPException(status_code=422, detail="telegram_id must be a positive integer")
    payload = _build_seed_payload()
    stats = seed_dev_patient_data(
        telegram_id,
        days=payload["days"],
        moods=payload["moods"],
        sleep_rows=payload["sleep_rows"],
        pills_logs=payload["pills_logs"],
        regimen_items=list(_SEED_REGIMEN),
        allergies_text=_SEED_ALLERGIES,
        notes=payload["notes"],
        answers=payload["answers"],
    )
    return {
        "ok": True,
        "telegram_id": telegram_id,
        "timezone": str(APP_TIMEZONE),
        "from": payload["days"][0],
        "to": payload["days"][-1],
        "days": len(payload["days"]),
        "inserted": stats,
        "idempotency": (
            "Re-seed replaces moods/sleep/pills_log and questionnaire answers "
            "for the same calendar days. Creates the user row if missing. "
            "Creates a pills regimen and allergies only when empty. "
            "Skips a note if that day already has one. Does not send Telegram messages."
        ),
    }


@app.post("/api/dev/seed", include_in_schema=False)
def dev_seed(body: SeedIn, x_dev_reset_token: str | None = Header(default=None)):
    if not _dev_reset_token_ok(x_dev_reset_token):
        raise HTTPException(status_code=403, detail="Forbidden")
    return _run_dev_seed(body.telegram_id)


@app.post("/api/dev/seed/{telegram_id}", include_in_schema=False)
def dev_seed_path(telegram_id: int, x_dev_reset_token: str | None = Header(default=None)):
    if not _dev_reset_token_ok(x_dev_reset_token):
        raise HTTPException(status_code=403, detail="Forbidden")
    return _run_dev_seed(telegram_id)


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
        "suicide_risk_completed": suicide_risk_unblocked(uid),
    }


@app.post("/api/moods")
def create_mood(body: MoodIn, authorization: str | None = Header(default=None)):
    uid = current_user(authorization)
    require_patient(uid)
    emotions = [e.strip() for e in body.emotions if e.strip()]
    note = body.note.strip()
    stamp = datetime.now(APP_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    with conn() as db:
        db.execute(
            """INSERT INTO moods(patient_telegram_id, score, emotions, note, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (uid, body.score, ", ".join(emotions), note, stamp),
        )
    maybe_send_mood_followup(uid)
    return {"ok": True}


def _allergies_payload(text: str | None, completed: bool = False) -> dict:
    return {"text": (text or "").strip(), "completed": completed}


@app.get("/api/allergies")
def get_allergies(authorization: str | None = Header(default=None)):
    uid = current_user(authorization)
    require_patient(uid)
    with conn() as db:
        row = db.execute(
            "SELECT allergies, allergies_completed FROM users WHERE telegram_id = ?",
            (uid,),
        ).fetchone()
    return _allergies_payload(
        row["allergies"] if row else "",
        bool(row["allergies_completed"]) if row else False,
    )


@app.get("/api/notes")
def list_patient_notes(authorization: str | None = Header(default=None)):
    uid = current_user(authorization)
    require_patient(uid)
    with conn() as db:
        rows = db.execute(
            """
            SELECT id, text, created_at
            FROM patient_notes
            WHERE patient_telegram_id = ?
            ORDER BY id
            """,
            (uid,),
        ).fetchall()
    return {"items": [utc_iso_fields(row, "created_at", naive_tz=APP_TIMEZONE) for row in rows]}


@app.post("/api/notes")
def create_patient_note(
    body: PatientNoteIn,
    authorization: str | None = Header(default=None),
):
    uid = current_user(authorization)
    require_patient(uid)
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Введите текст заметки.")
    created_at = datetime.now(APP_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    with conn() as db:
        cursor = db.execute(
            """
            INSERT INTO patient_notes (patient_telegram_id, text, created_at)
            VALUES (?, ?, ?)
            """,
            (uid, text, created_at),
        )
        note_id = cursor.lastrowid
    return {
        "id": note_id,
        "text": text,
        "created_at": to_utc_iso(created_at, naive_tz=APP_TIMEZONE),
    }


@app.post("/api/allergies")
def save_allergies(body: AllergiesIn, authorization: str | None = Header(default=None)):
    uid = current_user(authorization)
    require_patient(uid)
    text = body.text.strip()
    with conn() as db:
        db.execute(
            "INSERT INTO users (telegram_id) VALUES (?) ON CONFLICT(telegram_id) DO NOTHING",
            (uid,),
        )
        db.execute(
            """
            UPDATE users
            SET allergies = ?, allergies_completed = 1
            WHERE telegram_id = ?
            """,
            (text, uid),
        )
    maybe_send_bot_menu(uid)
    return _allergies_payload(text, completed=True)


@app.get("/api/doctor/patients/{patient_id}/allergies")
def doctor_patient_allergies(
    patient_id: int, authorization: str | None = Header(default=None)
):
    uid = current_user(authorization)
    require_doctor(uid)
    with conn() as db:
        ensure_patient_link(db, uid, patient_id)
        row = db.execute(
            "SELECT allergies, allergies_completed FROM users WHERE telegram_id = ?",
            (patient_id,),
        ).fetchone()
    return _allergies_payload(
        row["allergies"] if row else "",
        bool(row["allergies_completed"]) if row else False,
    )


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
        "created_at": to_utc_iso(row["created_at"]),
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
    return {"ok": True, "item": utc_iso_fields(row, "created_at") if row else row}


@app.get("/api/doctor/patients/{patient_id}/pills")
def doctor_patient_pills(
    patient_id: int, authorization: str | None = Header(default=None)
):
    uid = current_user(authorization)
    require_doctor(uid)
    with conn() as db:
        ensure_patient_link(db, uid, patient_id)
    regimen = get_regimen(patient_id)
    since = (datetime.now(APP_TIMEZONE).date() - timedelta(days=29)).isoformat()
    log = list_pills_log(patient_id, since, limit=30)
    payload = _regimen_payload(regimen)
    payload["log"] = [utc_iso_fields(item, "created_at") for item in log]
    return payload


def _sleep_item(row) -> dict:
    item = utc_iso_fields(row, "created_at")
    hours = item.get("hours")
    item["hours"] = float(hours) if hours is not None else None
    return item


@app.get("/api/sleep")
def get_sleep(authorization: str | None = Header(default=None)):
    uid = current_user(authorization)
    require_patient(uid)
    today = _utc_date()
    since = (datetime.now(APP_TIMEZONE).date() - timedelta(days=29)).isoformat()
    items = [_sleep_item(row) for row in list_sleep_log(uid, since, limit=30)]
    return {"today": today, "items": items}


@app.post("/api/sleep")
def save_sleep(body: SleepIn, authorization: str | None = Header(default=None)):
    uid = current_user(authorization)
    require_patient(uid)
    if body.quality not in SLEEP_QUALITY_VALUES:
        raise HTTPException(status_code=422, detail="Отметьте, как вы спали.")
    day = _parse_log_date(body.date)
    row = write_sleep_log(uid, day, body.quality, body.hours, body.note)
    return {"ok": True, "item": _sleep_item(row) if row else row}


@app.get("/api/doctor/patients/{patient_id}/sleep")
def doctor_patient_sleep(
    patient_id: int, authorization: str | None = Header(default=None)
):
    uid = current_user(authorization)
    require_doctor(uid)
    with conn() as db:
        ensure_patient_link(db, uid, patient_id)
    since = (datetime.now(APP_TIMEZONE).date() - timedelta(days=29)).isoformat()
    items = [_sleep_item(row) for row in list_sleep_log(patient_id, since, limit=30)]
    return {"items": items}


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
    return {"items": [utc_iso_fields(row, "created_at", naive_tz=APP_TIMEZONE) for row in rows]}


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
            latest[row["questionnaire_id"]] = parse_answer_row(row)
    return {
        "items": [
            {**definition, "latest": latest.get(questionnaire_id)}
            for questionnaire_id, definition in definitions.items()
            if definition.get("available")
        ]
    }


def parse_answer_row(row) -> dict:
    item = utc_iso_fields(row, "created_at")
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
        day = app_calendar_date(item.get("created_at"))
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
    today = _utc_date()
    definitions = {
        qid: definition
        for qid, definition in questionnaire_definitions().items()
        if definition.get("available")
    }
    with conn() as db:
        moods = [
            utc_iso_fields(row, "created_at", naive_tz=APP_TIMEZONE)
            for row in db.execute(
                """SELECT score, emotions, created_at FROM moods
                   WHERE patient_telegram_id = ? ORDER BY id ASC""",
                (uid,),
            ).fetchall()
            if app_calendar_date(row["created_at"], naive_tz=APP_TIMEZONE) < today
        ]
        answers = [
            parse_answer_row(row)
            for row in db.execute(
                """SELECT questionnaire_id, payload, status, result, created_at FROM answers
                   WHERE patient_telegram_id = ? ORDER BY id ASC""",
                (uid,),
            ).fetchall()
            if app_calendar_date(row["created_at"]) < today
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
    return {"items": [utc_iso_fields(row, "last_mood_at", naive_tz=APP_TIMEZONE) for row in rows]}


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
    return {
        "items": [utc_iso_fields(item, "created_at") for item in list_patient_documents(patient_id)]
    }


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
    return {"items": [utc_iso_fields(row, "created_at", naive_tz=APP_TIMEZONE) for row in rows]}


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
        item = parse_answer_row(row)
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
            moods = [
                utc_iso_fields(row, "created_at", naive_tz=APP_TIMEZONE)
                for row in db.execute(
                    """SELECT score, emotions, created_at FROM moods
                       WHERE patient_telegram_id = ? ORDER BY id DESC LIMIT 60""",
                    (patient_id,),
                ).fetchall()
            ]
            for row in db.execute(
                """SELECT questionnaire_id, payload, status, result, created_at FROM answers
                   WHERE patient_telegram_id = ? ORDER BY id DESC LIMIT 100""",
                (patient_id,),
            ).fetchall():
                answers.append(parse_answer_row(row))

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
    created_at = _utc_stamp()
    with conn() as db:
        db.execute(
            """INSERT INTO answers(patient_telegram_id, questionnaire_id, payload, status, result, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (uid, body.questionnaire_id, json.dumps(body.answers, ensure_ascii=False),
             body.status, json.dumps(result, ensure_ascii=False), created_at),
        )
    if body.questionnaire_id == "suicide-risk" and body.status == "completed":
        maybe_send_osr_patient_followup(uid)
    if body.questionnaire_id in STARTER_QUESTIONNAIRES and body.status == "completed":
        maybe_send_panas_intro(uid)
    if body.questionnaire_id in STARTER_QUESTIONNAIRES and body.status == "completed":
        maybe_send_scales_repeat_hint(uid)
    if body.status == "completed":
        maybe_notify_scale_alerts(uid, body.questionnaire_id, definition, body.answers, result)
    return {
        "ok": True,
        "status": body.status,
        "result": result,
        "created_at": to_utc_iso(created_at),
    }


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
