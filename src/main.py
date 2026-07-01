import asyncio
import logging
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent

from .config import Config, load_config
from .database import init_db
from .handlers import setup_routers
from .middlewares import WhitelistMiddleware


def setup_logging(config: Config) -> None:
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    file_handler = RotatingFileHandler(
        config.logs_path,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logging.basicConfig(
        level=logging.INFO,
        handlers=[file_handler, console_handler],
        force=True,
    )


async def on_error(event: ErrorEvent) -> bool:
    logging.exception("Ошибка при обработке обновления", exc_info=event.exception)
    try:
        if event.update.callback_query:
            callback = event.update.callback_query
            await callback.answer("что-то пошло не так", show_alert=True)
            if callback.message:
                await callback.message.answer("что-то пошло не так")
        elif event.update.message:
            await event.update.message.answer("что-то пошло не так")
    except Exception:
        logging.exception("Не удалось отправить сообщение об ошибке пользователю")
    return True


async def main() -> None:
    config = load_config()
    setup_logging(config)
    await init_db(config.database_path)

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    whitelist = WhitelistMiddleware(config)
    dp.message.outer_middleware(whitelist)
    dp.callback_query.outer_middleware(whitelist)
    dp.errors.register(on_error)
    dp.include_router(setup_routers())

    bot_info = await bot.get_me()
    if bot_info.username is None:
        raise RuntimeError("У бота нет username, QR-ссылки не смогут работать")

    logging.info("Бот запущен: @%s", bot_info.username)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(
        bot,
        config=config,
        bot_username=bot_info.username,
        allowed_updates=dp.resolve_used_update_types(),
    )


if __name__ == "__main__":
    asyncio.run(main())
