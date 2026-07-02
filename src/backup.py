from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiogram import Bot
from aiogram.types import FSInputFile
from dotenv import load_dotenv

from src.config import BASE_DIR, DATA_DIR, LOGS_DIR


DATABASE_PATH = DATA_DIR / "boxes.db"
LOG_PATH = LOGS_DIR / "bot.log"


def setup_logging() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    file_handler = RotatingFileHandler(
        LOG_PATH,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[file_handler], force=True)


def load_backup_settings() -> tuple[str, int | str]:
    load_dotenv(BASE_DIR / ".env")
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    backup_chat_id = os.getenv("BACKUP_CHAT_ID", "").strip()

    if not bot_token:
        raise RuntimeError("Не задан BOT_TOKEN в .env")
    if not backup_chat_id:
        raise RuntimeError("Не задан BACKUP_CHAT_ID в .env")

    try:
        chat_id: int | str = int(backup_chat_id)
    except ValueError:
        chat_id = backup_chat_id

    return bot_token, chat_id


def backup_database(source_path: Path, backup_path: Path) -> None:
    if not source_path.exists():
        raise FileNotFoundError(f"База данных не найдена: {source_path}")

    source_uri = f"file:{source_path}?mode=ro"
    with sqlite3.connect(source_uri, uri=True, timeout=30) as source:
        with sqlite3.connect(backup_path, timeout=30) as backup:
            source.backup(backup)


def create_backup_zip(database_path: Path, temp_dir: Path) -> Path:
    date_stamp = datetime.now(timezone.utc).date().isoformat()
    backup_db_path = temp_dir / "boxes.db"
    zip_path = temp_dir / f"boxes-backup-{date_stamp}.zip"

    backup_database(database_path, backup_db_path)

    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(backup_db_path, arcname="boxes.db")

    return zip_path


async def send_backup(bot_token: str, chat_id: int | str, zip_path: Path) -> None:
    bot = Bot(token=bot_token)
    try:
        document = FSInputFile(zip_path)
        await bot.send_document(
            chat_id=chat_id,
            document=document,
            caption=f"Backup базы коробок: {zip_path.name}",
        )
    finally:
        await bot.session.close()


async def main() -> None:
    setup_logging()
    logger = logging.getLogger("backup")

    try:
        bot_token, chat_id = load_backup_settings()
        with tempfile.TemporaryDirectory(prefix="box-bot-backup-") as temp_dir_name:
            zip_path = create_backup_zip(DATABASE_PATH, Path(temp_dir_name))
            await send_backup(bot_token, chat_id, zip_path)
            logger.info("Backup sent successfully: %s", zip_path.name)
    except Exception:
        logger.exception("Backup failed")
        raise


if __name__ == "__main__":
    asyncio.run(main())
