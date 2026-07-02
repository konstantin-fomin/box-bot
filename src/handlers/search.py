import re
import html

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import database
from ..config import Config
from ..database import Box, STATUS_LABELS
from ..keyboards import (
    BTN_LIST,
    BTN_SEARCH,
    boxes_list_keyboard,
    cancel_keyboard,
    list_filters_keyboard,
    main_menu,
    statuses_filter_keyboard,
)
from ..states import Search
from .boxes import box_code_html, send_box_card


router = Router(name="search")


def normalize_search_query(text: str) -> str:
    query = text.strip()
    query = re.sub(r"^где\s+", "", query, flags=re.IGNORECASE)
    query = query.strip(" ?!.,;:")
    return query


def boxes_list_text(title: str, boxes: list[Box]) -> str:
    rows = "\n".join(f"• {box_code_html(box.code)} · {html.escape(box.room)}" for box in boxes)
    return f"{title}\n{rows}"


async def send_boxes_list(message: Message, config: Config, *, status: str | None = None, room: str | None = None) -> None:
    boxes = await database.list_boxes(config.database_path, status=status, room=room)
    if not boxes:
        await message.answer("Коробок по этому фильтру нет.", reply_markup=main_menu())
        return
    await message.answer(
        boxes_list_text("Выберите коробку:", boxes),
        reply_markup=boxes_list_keyboard(boxes),
        parse_mode="HTML",
    )


@router.message(F.text == BTN_SEARCH)
async def search_start(message: Message, state: FSMContext) -> None:
    await state.set_state(Search.waiting_for_query)
    await message.answer("Что ищем?", reply_markup=cancel_keyboard())


@router.message(Search.waiting_for_query, F.text)
async def search_finish(message: Message, state: FSMContext, config: Config) -> None:
    query = normalize_search_query(message.text)
    if not query:
        await message.answer("Введите название вещи для поиска.")
        return

    await state.clear()
    boxes = await database.search_boxes(config.database_path, query)
    if not boxes:
        await message.answer("Ничего не найдено.", reply_markup=main_menu())
        return

    await message.answer(f"Найдено коробок: {len(boxes)}", reply_markup=main_menu())
    for box in boxes[:5]:
        await send_box_card(message, box)


@router.message(F.text.regexp(r"(?i)^где\s+.+"))
async def search_by_phrase(message: Message, config: Config) -> None:
    query = normalize_search_query(message.text)
    boxes = await database.search_boxes(config.database_path, query)
    if not boxes:
        await message.answer("Ничего не найдено.", reply_markup=main_menu())
        return
    await message.answer(f"Найдено коробок: {len(boxes)}", reply_markup=main_menu())
    for box in boxes[:5]:
        await send_box_card(message, box)


@router.message(F.text == BTN_LIST)
async def list_start(message: Message, config: Config) -> None:
    rooms = await database.list_rooms(config.database_path)
    await message.answer("Фильтры списка:", reply_markup=list_filters_keyboard(rooms))
    await send_boxes_list(message, config)


@router.callback_query(F.data == "list:all")
async def list_all(callback: CallbackQuery, config: Config) -> None:
    boxes = await database.list_boxes(config.database_path)
    await callback.answer()
    if not boxes:
        await callback.message.answer("Коробок пока нет.", reply_markup=main_menu())
        return
    await callback.message.answer(
        boxes_list_text("Все коробки:", boxes),
        reply_markup=boxes_list_keyboard(boxes),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "list:statuses")
async def list_statuses(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer("Выберите статус:", reply_markup=statuses_filter_keyboard())


@router.callback_query(F.data.startswith("list:status:"))
async def list_by_status(callback: CallbackQuery, config: Config) -> None:
    status = callback.data.split(":")[-1]
    boxes = await database.list_boxes(config.database_path, status=status)
    await callback.answer()
    if not boxes:
        await callback.message.answer(f"Коробок со статусом «{STATUS_LABELS.get(status, status)}» нет.")
        return
    await callback.message.answer(
        boxes_list_text(f"Коробки со статусом «{STATUS_LABELS.get(status, status)}»:", boxes),
        reply_markup=boxes_list_keyboard(boxes),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("list:room:"))
async def list_by_room(callback: CallbackQuery, config: Config) -> None:
    room = callback.data.removeprefix("list:room:")
    boxes = await database.list_boxes(config.database_path, room=room)
    await callback.answer()
    if not boxes:
        await callback.message.answer(f"В комнате «{room}» коробок нет.")
        return
    await callback.message.answer(
        boxes_list_text(f"Коробки в комнате «{html.escape(room)}»:", boxes),
        reply_markup=boxes_list_keyboard(boxes),
        parse_mode="HTML",
    )
