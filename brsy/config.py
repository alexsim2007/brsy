import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    bot_token: str = ""
    encryption_key: str = ""
    database_path: str = "data/brsy.sqlite3"
    host: str = "127.0.0.1"
    port: int = 8000
    mini_app_url: str = ""
    poll_interval: int = 300
    demo: bool = False

    @classmethod
    def load(cls):
        load_dotenv()
        demo = os.getenv("DEMO_MODE", "false").lower() == "true"
        token = os.getenv("BOT_TOKEN", "").strip()
        key = os.getenv("ENCRYPTION_KEY", "").strip()
        url = os.getenv("MINI_APP_URL", "").strip().rstrip("/")
        if demo and token:
            raise ValueError("DEMO_MODE несовместим с BOT_TOKEN. Для демо уберите BOT_TOKEN.")
        if not demo and (not token or not key):
            raise ValueError("Заполните BOT_TOKEN и ENCRYPTION_KEY в .env или включите DEMO_MODE=true.")
        if key:
            Fernet(key.encode())
        if url and (urlsplit(url).scheme != "https" or not urlsplit(url).netloc or urlsplit(url).query or urlsplit(url).fragment):
            raise ValueError("MINI_APP_URL должен быть HTTPS URL без query и fragment.")
        return cls(token, key, os.getenv("DATABASE_PATH", "data/brsy.sqlite3"),
                   os.getenv("HOST", "127.0.0.1"), int(os.getenv("PORT", "8000")), url,
                   max(60, int(os.getenv("POLL_INTERVAL", "300"))), demo)


ROOT = Path(__file__).resolve().parent.parent
