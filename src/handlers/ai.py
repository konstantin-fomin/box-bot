from __future__ import annotations

from io import BytesIO
import logging

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import database
from ..config import Config
from ..keyboards import ai_confirmation_keyboard, main_menu
from ..services.ai_actions import (
    ai_commands_from_dicts,
    ai_commands_to_dicts,
    apply_ai_commands,
    format_ai_apply_result,
    format_ai_confirmation,
    mutating_commands,
    search_commands,
    send_ai_result_cards,
)
from ..services.gemini import BoxAiParser
from ..states import AiAction


router = Router(name="ai")
logger = logging.getLogger(__name__)


@router.message(StateFilter(None), F.voice)
async def parse_voice_request(
    message: Message,
    bot: Bot,
    state: FSMContext,
    config: Config,
    household_id: int,
    ai_parser: BoxAiParser | None = None,
) -> None:
    if ai_parser is None:
        await message.answer("Распознавание голосовых сообщений пока не настроено.", reply_markup=main_menu())
        return
    if message.voice is None:
        return

    boxes = await database.list_boxes(config.database_path, household_id=household_id, limit=100)
    buffer = BytesIO()
    await bot.download(message.voice, destination=buffer)
    try:
        commands = await ai_parser.parse_voice_message(
            buffer.getvalue(),
            message.voice.mime_type or "audio/ogg",
            boxes,
        )
    except Exception:
        logger.exception("Не удалось разобрать голосовое сообщение через Gemini")
        await message.answer("Не смогла разобрать голосовое сообщение.", reply_markup=main_menu())
        return

    await _handle_ai_commands(message, state, config, household_id, commands, boxes)


@router.message(StateFilter(None), F.text)
async def parse_text_request(
    message: Message,
    state: FSMContext,
    config: Config,
    household_id: int,
    ai_parser: BoxAiParser | None = None,
) -> None:
    if ai_parser is None:
        return

    text = (message.text or "").strip()
    if not text:
        return

    boxes = await database.list_boxes(config.database_path, household_id=household_id, limit=100)
    try:
        commands = await ai_parser.parse_message(text, boxes)
    except Exception:
        logger.exception("Не удалось разобрать текстовое сообщение через Gemini")
        await message.answer("Не смогла разобрать сообщение.", reply_markup=main_menu())
        return

    await _handle_ai_commands(message, state, config, household_id, commands, boxes)


@router.callback_query(AiAction.waiting_confirmation, F.data == "ai:cancel")
async def cancel_ai_action(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if callback.message is not None:
        await callback.message.edit_text("Действие отменено.")
    await callback.answer()


@router.callback_query(AiAction.waiting_confirmation, F.data == "ai:confirm")
async def confirm_ai_action(
    callback: CallbackQuery,
    state: FSMContext,
    config: Config,
    household_id: int,
    bot_username: str,
) -> None:
    data = await state.get_data()
    raw_commands = data.get("ai_commands")
    commands = ai_commands_from_dicts(raw_commands) if isinstance(raw_commands, list) else []
    await state.clear()

    if not commands:
        if callback.message is not None:
            await callback.message.edit_text("Действие уже не найдено.")
        await callback.answer()
        return

    result = await apply_ai_commands(config, household_id, commands)
    if callback.message is not None:
        await callback.message.edit_text(format_ai_apply_result(result), parse_mode="HTML")
        await send_ai_result_cards(callback.message, result, bot_username)
    await callback.answer("Готово.")


async def _handle_ai_commands(
    message: Message,
    state: FSMContext,
    config: Config,
    household_id: int,
    commands,
    boxes,
) -> None:
    if not commands:
        await message.answer("Не поняла, что нужно сделать.", reply_markup=main_menu())
        return

    changes = mutating_commands(commands)
    searches = search_commands(commands)
    if changes:
        await state.update_data(ai_commands=ai_commands_to_dicts(changes + searches))
        await state.set_state(AiAction.waiting_confirmation)
        await message.answer(
            format_ai_confirmation(changes + searches, boxes),
            reply_markup=ai_confirmation_keyboard(),
            parse_mode="HTML",
        )
        return

    result = await apply_ai_commands(config, household_id, searches)
    await message.answer(format_ai_apply_result(result), reply_markup=main_menu(), parse_mode="HTML")
    await send_ai_result_cards(message, result, "")
