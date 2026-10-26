import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from urllib.parse import parse_qsl
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=False)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

class AuthError(Exception):
    pass

def validate_init_data(raw: str, max_age_seconds: int = 86400) -> dict:
    if not BOT_TOKEN:
        raise AuthError("BOT_TOKEN is not configured")
    if not raw:
        raise AuthError("Missing Telegram initData")
    params = dict(parse_qsl(raw, keep_blank_values=True))
    received_hash = params.pop("hash", None)
    if not received_hash:
        raise AuthError("Missing hash")
    auth_date = params.get("auth_date")
    if not auth_date or not auth_date.isdigit():
        raise AuthError("Missing or invalid auth_date")
    age = int(time.time()) - int(auth_date)
    if age < -60 or age > max_age_seconds:
        raise AuthError("Expired or invalid initData timestamp")
    check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise AuthError("Invalid initData signature")
    try:
        user = json.loads(params["user"])
        return {"telegram_id": int(user["id"]), "user": user, "params": params}
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AuthError("Invalid user data") from exc


def validate_login_widget(payload: dict, max_age_seconds: int = 86400) -> dict:
    """Telegram Login Widget HMAC. Secret is SHA256(bot_token), not WebAppData."""
    if not BOT_TOKEN:
        raise AuthError("BOT_TOKEN is not configured")
    if not payload:
        raise AuthError("Missing Telegram login data")
    received_hash = payload.get("hash")
    if not received_hash:
        raise AuthError("Missing hash")
    check_pairs = {
        str(key): str(value)
        for key, value in payload.items()
        if key != "hash" and value is not None and str(value) != ""
    }
    auth_date = check_pairs.get("auth_date")
    if not auth_date or not str(auth_date).isdigit():
        raise AuthError("Missing or invalid auth_date")
    age = int(time.time()) - int(auth_date)
    if age < -60 or age > max_age_seconds:
        raise AuthError("Expired or invalid login timestamp")
    check = "\n".join(f"{key}={value}" for key, value in sorted(check_pairs.items()))
    secret = hashlib.sha256(BOT_TOKEN.encode()).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise AuthError("Invalid login signature")
    try:
        return {
            "telegram_id": int(check_pairs["id"]),
            "first_name": check_pairs.get("first_name", ""),
            "last_name": check_pairs.get("last_name", ""),
            "username": check_pairs.get("username", ""),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthError("Invalid user data") from exc
