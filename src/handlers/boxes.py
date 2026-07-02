from __future__ import annotations

import html
import re
import tempfile
from datetime import datetime
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

from .. import database
from ..config import Config
from ..database import Box, STATUS_LABELS
from ..keyboards import (
    BTN_CANCEL,
    BTN_DONE_PHOTOS,
    BTN_EXPORT_PDF,
    BTN_NEW_BOX,
    BTN_SKIP_PHOTO,
    box_actions,
    cancel_keyboard,
    main_menu,
    more_keyboard,
    photos_keyboard,
)
from ..pdf_export import generate_boxes_pdf
from ..qr import make_qr_file
from ..states import AddItem, CreateBox


router = Router(name="boxes")


TRANSLIT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "i",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "c",
    "ч": "ch",
    "ш": "sh",
    "щ": "sh",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def split_items(raw_items: str) -> list[str]:
    return [
        item.strip(" .;")
        for item in re.split(r"[\n,;]+", raw_items)
        if item.strip(" .;")
    ]


def room_prefix(room: str) -> str:
    transliterated = "".join(TRANSLIT.get(char.lower(), char) for char in room)
    words = re.findall(r"[A-Za-z0-9]+", transliterated)
    if not words:
        return "BOX"
    if len(words) > 1:
        prefix = "".join(word[0] for word in words)
    else:
        prefix = words[0][:4]
    return prefix.upper()


def box_code_html(box_code: str) -> str:
    return f"<code>{html.escape(box_code)}</code>"


def box_text(box: Box) -> str:
    items = "\n".join(f"• {html.escape(item)}" for item in box.items) or "пока не указаны"
    photos_count = len(box.photos)
    photos = f"\nФото: {photos_count}" if photos_count else "\nФото: нет"
    return (
        f"📦 {box_code_html(box.code)}\n"
        f"Статус: {STATUS_LABELS.get(box.status, box.status)}\n"
        f"Комната: {html.escape(box.room)}\n"
        f"Вещи:\n{items}"
        f"{photos}"
    )


async def send_box_card(message: Message, box: Box) -> None:
    if box.photos:
        await message.answer_photo(
            photo=box.photos[0],
            caption=box_text(box),
            reply_markup=box_actions(box),
            parse_mode="HTML",
        )
    else:
        await message.answer(box_text(box), reply_markup=box_actions(box), parse_mode="HTML")


async def finish_create_box(message: Message, state: FSMContext, config: Config, bot_username: str) -> None:
    data = await state.get_data()
    room = data["room"]
    items = data.get("items", [])
    photo_file_ids = data.get("photo_file_ids", [])

    box = await database.create_box(
        config.database_path,
        prefix=room_prefix(room),
        room=room,
        items=items,
        photo_file_ids=photo_file_ids,
    )
    await state.clear()

    await message.answer(f"Коробка {box_code_html(box.code)} создана.", reply_markup=main_menu(), parse_mode="HTML")
    await send_box_card(message, box)
    await message.answer_photo(
        photo=make_qr_file(bot_username, box.code),
        caption=f"QR-код для коробки {box_code_html(box.code)}. Его можно распечатать и наклеить на коробку.",
        parse_mode="HTML",
    )


@router.message(CommandStart())
async def start(message: Message, command: CommandObject, config: Config) -> None:
    if command.args and command.args.startswith("box_"):
        code = command.args.removeprefix("box_").strip()
        box = await database.get_box_by_code(config.database_path, code)
        if box is None:
            await message.answer("Коробка не найдена.", reply_markup=main_menu())
            return
        await send_box_card(message, box)
        return

    await message.answer(
        "Бот для учёта коробок при переезде. Выберите действие в меню.",
        reply_markup=main_menu(),
    )


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer(
        "Доступные действия: создать коробку, найти вещь, открыть список коробок или отсканировать QR-код.",
        reply_markup=main_menu(),
    )


@router.message(F.text.regexp(r"^/box_[A-Za-z0-9]+-\d{2,}$"))
async def legacy_box_command(message: Message, config: Config) -> None:
    code = message.text.removeprefix("/box_").strip()
    box = await database.get_box_by_code(config.database_path, code)
    if box is None:
        await message.answer("Коробка не найдена.", reply_markup=main_menu())
        return
    await send_box_card(message, box)


@router.message(F.text == BTN_CANCEL)
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_menu())


@router.message(F.text == BTN_NEW_BOX)
async def new_box(message: Message, state: FSMContext) -> None:
    await state.set_state(CreateBox.room)
    await message.answer("Введите комнату или категорию коробки.", reply_markup=cancel_keyboard())


@router.message(CreateBox.room, F.text)
async def create_box_room(message: Message, state: FSMContext) -> None:
    room = message.text.strip()
    if not room:
        await message.answer("Введите название комнаты текстом.")
        return
    await state.update_data(room=room)
    await state.set_state(CreateBox.items)
    await message.answer(
        "Введите список вещей. Можно писать через запятую или с новой строки.",
        reply_markup=cancel_keyboard(),
    )


@router.message(CreateBox.items, F.voice)
async def create_box_voice_stub(message: Message) -> None:
    # TODO: добавить распознавание голосовых сообщений через облачный API.
    await message.answer("Голосовые сообщения пока не распознаются. Пришлите список вещей текстом.")


@router.message(CreateBox.items, F.text)
async def create_box_items(message: Message, state: FSMContext) -> None:
    items = split_items(message.text)
    if not items:
        await message.answer("Не удалось найти вещи в сообщении. Пришлите список текстом.")
        return
    await state.update_data(items=items, photo_file_ids=[])
    await state.set_state(CreateBox.photos)
    await message.answer(
        "Пришлите одно или несколько фото коробки либо нажмите «Пропустить фото».",
        reply_markup=photos_keyboard(),
    )


@router.message(CreateBox.photos, F.photo)
async def create_box_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    photo_file_ids = list(data.get("photo_file_ids", []))
    photo_file_ids.append(message.photo[-1].file_id)
    await state.update_data(photo_file_ids=photo_file_ids)
    await message.answer("Фото добавлено. Можно прислать ещё или нажать «Готово».")


@router.message(CreateBox.photos, F.text.in_({BTN_SKIP_PHOTO, BTN_DONE_PHOTOS}))
async def create_box_finish(message: Message, state: FSMContext, config: Config, bot_username: str) -> None:
    await finish_create_box(message, state, config, bot_username)


@router.message(CreateBox.photos)
async def create_box_photo_unknown(message: Message) -> None:
    await message.answer("Пришлите фото, нажмите «Готово» или «Пропустить фото».")


@router.callback_query(F.data.startswith("box:show:"))
async def show_box_callback(callback: CallbackQuery, config: Config) -> None:
    box_id = int(callback.data.split(":")[-1])
    box = await database.get_box_by_id(config.database_path, box_id)
    if box is None:
        await callback.answer("Коробка не найдена.", show_alert=True)
        return
    await callback.answer()
    await send_box_card(callback.message, box)


@router.callback_query(F.data.startswith("box:add_item:"))
async def add_item_start(callback: CallbackQuery, state: FSMContext) -> None:
    box_id = int(callback.data.split(":")[-1])
    await state.set_state(AddItem.waiting_for_items)
    await state.update_data(box_id=box_id)
    await callback.answer()
    await callback.message.answer(
        "Введите вещи, которые нужно добавить. Можно через запятую или с новой строки.",
        reply_markup=cancel_keyboard(),
    )


@router.message(AddItem.waiting_for_items, F.voice)
async def add_item_voice_stub(message: Message) -> None:
    # TODO: добавить распознавание голосовых сообщений через облачный API.
    await message.answer("Голосовые сообщения пока не распознаются. Пришлите вещи текстом.")


@router.message(AddItem.waiting_for_items, F.text)
async def add_item_finish(message: Message, state: FSMContext, config: Config) -> None:
    data = await state.get_data()
    box_id = int(data["box_id"])
    items = split_items(message.text)
    if not items:
        await message.answer("Не удалось найти вещи в сообщении. Пришлите список текстом.")
        return

    await database.add_items(config.database_path, box_id, items)
    await state.clear()
    box = await database.get_box_by_id(config.database_path, box_id)
    await message.answer("Вещи добавлены.", reply_markup=main_menu())
    if box:
        await send_box_card(message, box)


@router.callback_query(F.data.startswith("box:delete:"))
async def delete_box_callback(callback: CallbackQuery, config: Config) -> None:
    box_id = int(callback.data.split(":")[-1])
    box = await database.get_box_by_id(config.database_path, box_id)
    if box is None:
        await callback.answer("Коробка не найдена.", show_alert=True)
        return
    await database.delete_box(config.database_path, box_id)
    await callback.answer("Коробка удалена.")
    await callback.message.answer(
        f"Коробка {box_code_html(box.code)} удалена.",
        reply_markup=main_menu(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("box:qr:"))
async def send_qr_callback(callback: CallbackQuery, config: Config, bot_username: str) -> None:
    box_id = int(callback.data.split(":")[-1])
    box = await database.get_box_by_id(config.database_path, box_id)
    if box is None:
        await callback.answer("Коробка не найдена.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer_photo(
        photo=make_qr_file(bot_username, box.code),
        caption=f"QR-код для коробки {box_code_html(box.code)}.",
        parse_mode="HTML",
    )


@router.message(F.text == "⚙️ Ещё")
async def more(message: Message) -> None:
    await message.answer(
        "Дополнительные действия.",
        reply_markup=more_keyboard(),
    )


@router.message(F.text == BTN_EXPORT_PDF)
async def export_pdf(message: Message, config: Config, bot: Bot) -> None:
    boxes = await database.list_all_boxes(config.database_path)
    if not boxes:
        await message.answer("Коробок пока нет.", reply_markup=main_menu())
        return

    await message.answer("Готовлю PDF-файл.", reply_markup=main_menu())
    filename = f"boxes-export-{datetime.now():%Y-%m-%d}.pdf"
    with tempfile.TemporaryDirectory(prefix="box-bot-pdf-") as temp_dir:
        pdf_path = Path(temp_dir) / filename
        generate_boxes_pdf(boxes, pdf_path)
        await bot.send_document(
            chat_id=message.chat.id,
            document=FSInputFile(pdf_path),
            caption=f"Экспорт коробок: {len(boxes)}",
        )


@router.message(F.voice)
async def voice_stub(message: Message) -> None:
    # TODO: добавить распознавание голосовых сообщений через облачный API.
    await message.answer("Голосовые сообщения пока не распознаются. Пришлите информацию текстом.")
