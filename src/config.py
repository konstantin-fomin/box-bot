from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

import os


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"


@dataclass(frozen=True)
class Config:
    bot_token: str
    whitelist_user_ids: set[int]
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash"
    database_path: Path = DATA_DIR / "boxes.db"
    logs_path: Path = LOGS_DIR / "bot.log"


def _parse_user_ids(raw_value: str) -> set[int]:
    user_ids: set[int] = set()
    for item in raw_value.replace(";", ",").split(","):
        value = item.strip()
        if not value:
            continue
        try:
            user_ids.add(int(value))
        except ValueError as exc:
            raise ValueError(f"Некорректный user_id в WHITELIST_USER_IDS: {value}") from exc
    return user_ids


def load_config() -> Config:
    load_dotenv(BASE_DIR / ".env")

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    whitelist = _parse_user_ids(os.getenv("WHITELIST_USER_IDS", ""))
    gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip() or None
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash").strip() or "gemini-3.5-flash"

    if not bot_token:
        raise RuntimeError("Не задан BOT_TOKEN в .env")
    if not whitelist:
        raise RuntimeError("Не задан WHITELIST_USER_IDS в .env")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    return Config(
        bot_token=bot_token,
        whitelist_user_ids=whitelist,
        gemini_api_key=gemini_api_key,
        gemini_model=gemini_model,
    )
