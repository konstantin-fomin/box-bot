from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from .config import Config
from .database import get_user_household, upsert_user
from .keyboards import BTN_CREATE_HOUSEHOLD, BTN_JOIN_HOUSEHOLD, household_onboarding_keyboard
from .states import HouseholdOnboarding


class HouseholdMembershipMiddleware(BaseMiddleware):
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

        full_name = user.full_name if user.full_name else user.username
        await upsert_user(self.config.database_path, user.id, full_name)

        household = await get_user_household(self.config.database_path, user.id)
        data["household"] = household
        data["household_id"] = household.id if household else None

        if household is not None:
            return await handler(event, data)

        if isinstance(event, Message) and await self._is_onboarding_message(event, data):
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            await event.answer("Сначала создайте группу или присоединитесь по коду.", show_alert=True)
            return None

        if isinstance(event, Message):
            await event.answer(
                "Чтобы пользоваться ботом, создайте свою группу или введите код приглашения.",
                reply_markup=household_onboarding_keyboard(),
            )
        return None

    async def _is_onboarding_message(self, event: Message, data: dict[str, Any]) -> bool:
        text = event.text or ""
        state = data.get("state")
        current_state = await state.get_state() if state else None
        return (
            text.startswith("/start")
            or text in {BTN_CREATE_HOUSEHOLD, BTN_JOIN_HOUSEHOLD}
            or current_state
            in {
                HouseholdOnboarding.waiting_for_name.state,
                HouseholdOnboarding.waiting_for_invite_code.state,
            }
        )
