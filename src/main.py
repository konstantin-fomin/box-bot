import asyncio
import concurrent.futures
import logging
import socket
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TypeVar

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiohttp.abc import AbstractResolver, ResolveResult
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent

from src.config import Config, load_config
from src.database import init_db
from src.handlers import setup_routers
from src.middlewares import HouseholdMembershipMiddleware


_T = TypeVar("_T")


class ExecutorResolver(AbstractResolver):
    def __init__(self) -> None:
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="aiohttp-dns")

    async def _await_future(self, future: concurrent.futures.Future[_T]) -> _T:
        while not future.done():
            await asyncio.sleep(0.001)
        return future.result()

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_INET,
    ) -> list[ResolveResult]:
        infos = await self._await_future(
            self._executor.submit(socket.getaddrinfo, host, port, family, socket.SOCK_STREAM)
        )

        results: list[ResolveResult] = []
        for address_family, _, proto, _, address in infos:
            resolved_host, resolved_port = address[:2]
            results.append(
                ResolveResult(
                    hostname=host,
                    host=resolved_host,
                    port=resolved_port,
                    family=address_family,
                    proto=proto,
                    flags=socket.AI_NUMERICHOST | socket.AI_NUMERICSERV,
                )
            )
        return results

    async def close(self) -> None:
        self._executor.shutdown(wait=True)


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
    logging.info("Bot starting...")
    await init_db(config.database_path)

    session = AiohttpSession()
    session._connector_init["resolver"] = ExecutorResolver()
    bot = Bot(
        token=config.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    membership = HouseholdMembershipMiddleware(config)
    dp.message.outer_middleware(membership)
    dp.callback_query.outer_middleware(membership)
    dp.errors.register(on_error)
    dp.include_router(setup_routers())

    try:
        bot_info = await bot.get_me()
        if bot_info.username is None:
            raise RuntimeError("У бота нет username, QR-ссылки не смогут работать")

        logging.info("Telegram API connected, bot username: @%s", bot_info.username)
        await bot.delete_webhook(drop_pending_updates=True)
        logging.info("Bot started, polling...")
        await dp.start_polling(
            bot,
            config=config,
            bot_username=bot_info.username,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
