import hashlib
import hmac
import json
import re
import time
from urllib.parse import parse_qsl


class AuthError(ValueError):
    pass


def parse_token(text: str) -> str:
    """Accept a plain token or a copied quoted token; never accept arbitrary page text."""
    value = text.strip().strip('"\'`').strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{40}", value):
        raise ValueError("Нужен токен из 40 символов со страницы БРС. Скопируйте только сам токен.")
    return value


def validate_init_data(raw: str, bot_token: str, *, now: float | None = None, max_age: int = 3600) -> dict:
    if not raw or len(raw) > 16384:
        raise AuthError("Откройте приложение через Telegram-бота.")
    pairs = parse_qsl(raw, keep_blank_values=True)
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise AuthError("Некорректная авторизация Telegram.")
    supplied_hash = fields.pop("hash", "")
    data = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, supplied_hash):
        raise AuthError("Не удалось проверить подпись Telegram.")
    try:
        age = (time.time() if now is None else now) - int(fields["auth_date"])
        user = json.loads(fields["user"])
        if not isinstance(user, dict) or type(user.get("id")) is not int or user["id"] <= 0:
            raise ValueError
    except (KeyError, ValueError, TypeError):
        raise AuthError("Некорректные данные Telegram.") from None
    if age < -30 or age > max_age:
        raise AuthError("Сессия истекла. Закройте и снова откройте приложение в Telegram.")
    return user
