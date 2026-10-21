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
