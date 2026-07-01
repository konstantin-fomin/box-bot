from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from .config import Config
from .database import upsert_user


class WhitelistMiddleware(BaseMiddleware):
    def __init__(self, config: Config) -> None:
        self.config = config

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = None
        if isinstance(event, Message):
            user = event.from_user
        elif isinstance(event, CallbackQuery):
            user = event.from_user

        if user is None:
            return await handler(event, data)

        if user.id not in self.config.whitelist_user_ids:
            if isinstance(event, Message):
                await event.answer("Доступ запрещён.")
            elif isinstance(event, CallbackQuery):
                await event.answer("Доступ запрещён.", show_alert=True)
            return None

        full_name = user.full_name if user.full_name else user.username
        await upsert_user(self.config.database_path, user.id, full_name)
        return await handler(event, data)
