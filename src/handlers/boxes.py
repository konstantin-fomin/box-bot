from __future__ import annotations

import html
import logging
import re
import tempfile
from datetime import datetime
from io import BytesIO
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, InputMediaPhoto, Message

from .. import database
from ..config import Config
from ..database import Box, STATUS_LABELS
from ..keyboards import (
    BTN_CANCEL,
    BTN_CREATE_HOUSEHOLD,
    BTN_DONE_PHOTOS,
    BTN_EXPORT_PDF,
    BTN_JOIN_HOUSEHOLD,
    BTN_MY_GROUP,
    BTN_NEW_BOX,
    BTN_SKIP_PHOTO,
    ai_confirmation_keyboard,
    confirm_switch_household_keyboard,
    box_actions,
    cancel_keyboard,
    household_invite_keyboard,
    household_onboarding_keyboard,
    main_menu,
    more_keyboard,
    photos_keyboard,
)
from ..pdf_export import generate_boxes_pdf
from ..qr import make_qr_file
from ..services.ai_actions import AiCommand, ai_commands_to_dicts, format_ai_confirmation
from ..services.gemini import BoxAiParser
from ..states import AddItem, CreateBox, HouseholdOnboarding
from ..states import AiAction


router = Router(name="boxes")
logger = logging.getLogger(__name__)
MAX_MEDIA_GROUP_PHOTOS = 10

WELCOME_TEXT = """👋 Привет! Я бот-органайзер для переезда.

Помогаю не забыть, что где лежит:
📦 записываю содержимое каждой коробки
🔍 нахожу нужную коробку по одной фразе — "где блендер?"
🖨 делаю QR-коды для наклеек на коробки

Все твои коробки видны только тебе и людям в твоей группе (например семье) — никто посторонний их не увидит.

Для начала:"""

HELP_TEXT = """📦 Как пользоваться ботом:

- "Новая коробка" — создать коробку, указать комнату и вещи внутри
- "Найти вещь" — написать название вещи, бот скажет в какой она коробке
- "Список коробок" — увидеть все свои коробки
- В карточке коробки можно добавить фото, сменить статус, удалить

👥 Группа:
- "Моя группа" в разделе "Ещё" — посмотреть участников и пригласить друга"""


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


def normalize_invite_code(raw_code: str) -> str:
    return raw_code.strip().upper()


async def send_box_card(message: Message, box: Box) -> None:
    if len(box.photos) == 1:
        await message.answer_photo(
            photo=box.photos[0],
            caption=box_text(box),
            reply_markup=box_actions(box),
            parse_mode="HTML",
        )
    elif len(box.photos) > 1:
        media = [
            InputMediaPhoto(
                media=file_id,
                caption=box_text(box) if index == 0 else None,
                parse_mode="HTML" if index == 0 else None,
            )
            for index, file_id in enumerate(box.photos[:MAX_MEDIA_GROUP_PHOTOS])
        ]
        await message.bot.send_media_group(chat_id=message.chat.id, media=media)
        await message.answer("Действия с коробкой:", reply_markup=box_actions(box))
    else:
        await message.answer(box_text(box), reply_markup=box_actions(box), parse_mode="HTML")


async def finish_create_box(
    message: Message,
    state: FSMContext,
    config: Config,
    bot_username: str,
    household_id: int,
) -> None:
    data = await state.get_data()
    room = data["room"]
    items = data.get("items", [])
    photo_file_ids = data.get("photo_file_ids", [])

    box = await database.create_box(
        config.database_path,
        household_id=household_id,
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


async def join_by_invite_code(
    message: Message,
    config: Config,
    invite_code: str,
    current_household: database.Household | None,
) -> None:
    code = normalize_invite_code(invite_code)
    if not re.fullmatch(r"[A-Z0-9]{6}", code):
        await message.answer("Код приглашения должен состоять из 6 букв или цифр.")
        return

    target_household = await database.get_household_by_invite_code(config.database_path, code)
    if target_household is None:
        await message.answer("Группа с таким кодом не найдена.")
        return

    if current_household is not None:
        if current_household.id == target_household.id:
            await message.answer(f"Ты уже в группе «{html.escape(current_household.name)}».", parse_mode="HTML")
            return
        await message.answer(
            (
                f"Ты уже в группе «{html.escape(current_household.name)}». "
                "Присоединиться к новой группе означает выйти из текущей — все твои коробки останутся "
                "в старой группе, но ты потеряешь к ним доступ. Продолжить?"
            ),
            reply_markup=confirm_switch_household_keyboard(code),
            parse_mode="HTML",
        )
        return

    joined = await database.join_household(config.database_path, target_household.id, message.from_user.id)
    if joined is None:
        await message.answer("Группа с таким кодом не найдена.")
        return
    await message.answer(
        f"Готово. Вы присоединились к группе «{html.escape(joined.name)}».",
        reply_markup=main_menu(),
        parse_mode="HTML",
    )


@router.message(CommandStart())
async def start(
    message: Message,
    command: CommandObject,
    config: Config,
    household: database.Household | None = None,
    household_id: int | None = None,
    welcome_seen: bool = True,
) -> None:
    if command.args and command.args.startswith("join_"):
        await join_by_invite_code(message, config, command.args.removeprefix("join_"), household)
        return

    if household_id is None:
        text = WELCOME_TEXT
        if welcome_seen:
            text = "Чтобы пользоваться ботом, создайте свою группу или введите код приглашения."
        await message.answer(
            text,
            reply_markup=household_onboarding_keyboard(),
        )
        if not welcome_seen:
            await database.mark_user_welcome_seen(config.database_path, message.from_user.id)
        return

    if command.args and command.args.startswith("box_"):
        code = command.args.removeprefix("box_").strip()
        box = await database.get_box_by_code(config.database_path, code, household_id)
        if box is None:
            await message.answer("Коробка не найдена.", reply_markup=main_menu())
            return
        await send_box_card(message, box)
        return

    await message.answer(
        "Бот для учёта коробок при переезде. Выберите действие в меню.",
        reply_markup=main_menu(),
    )


@router.message(F.text == BTN_CREATE_HOUSEHOLD)
async def create_household_start(
    message: Message,
    state: FSMContext,
    household: database.Household | None = None,
) -> None:
    if household is not None:
        await message.answer(f"Ты уже в группе «{html.escape(household.name)}».", parse_mode="HTML")
        return
    await state.set_state(HouseholdOnboarding.waiting_for_name)
    await message.answer("Введите название группы.", reply_markup=cancel_keyboard())


@router.message(HouseholdOnboarding.waiting_for_name, F.text)
async def create_household_finish(message: Message, state: FSMContext, config: Config) -> None:
    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer("Действие отменено.", reply_markup=household_onboarding_keyboard())
        return
    name = message.text.strip()
    if not name:
        await message.answer("Введите название группы текстом.")
        return
    household = await database.create_household(config.database_path, name, message.from_user.id)
    await state.clear()
    await message.answer(
        (
            f"Группа «{html.escape(household.name)}» создана.\n"
            f"Код приглашения: <code>{html.escape(household.invite_code)}</code>"
        ),
        reply_markup=main_menu(),
        parse_mode="HTML",
    )


@router.message(F.text == BTN_JOIN_HOUSEHOLD)
async def join_household_start(message: Message, state: FSMContext) -> None:
    await state.set_state(HouseholdOnboarding.waiting_for_invite_code)
    await message.answer("Введите код приглашения.", reply_markup=cancel_keyboard())


@router.message(HouseholdOnboarding.waiting_for_invite_code, F.text)
async def join_household_finish(
    message: Message,
    state: FSMContext,
    config: Config,
    household: database.Household | None = None,
) -> None:
    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=main_menu() if household else household_onboarding_keyboard(),
        )
        return
    await state.clear()
    await join_by_invite_code(message, config, message.text, household)


@router.callback_query(F.data.startswith("household:switch:"))
async def switch_household_confirm(callback: CallbackQuery, config: Config) -> None:
    invite_code = callback.data.split(":")[-1]
    target_household = await database.get_household_by_invite_code(config.database_path, invite_code)
    if target_household is None:
        await callback.answer("Группа не найдена.", show_alert=True)
        return
    joined = await database.join_household(config.database_path, target_household.id, callback.from_user.id)
    await callback.answer("Группа изменена.")
    if joined and callback.message:
        await callback.message.answer(
            f"Теперь ты в группе «{html.escape(joined.name)}».",
            reply_markup=main_menu(),
            parse_mode="HTML",
        )


@router.callback_query(F.data == "household:switch_cancel")
async def switch_household_cancel(callback: CallbackQuery) -> None:
    await callback.answer("Отменено.")
    if callback.message:
        await callback.message.answer("Присоединение отменено.", reply_markup=main_menu())


@router.message(Command("help"))
async def help_command(message: Message, household: database.Household | None = None) -> None:
    await message.answer(
        HELP_TEXT,
        reply_markup=main_menu() if household else household_onboarding_keyboard(),
    )


@router.message(F.text.regexp(r"^/box_[A-Za-z0-9]+-\d{2,}$"))
async def legacy_box_command(message: Message, config: Config, household_id: int) -> None:
    code = message.text.removeprefix("/box_").strip()
    box = await database.get_box_by_code(config.database_path, code, household_id)
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
async def create_box_voice_items(
    message: Message,
    bot: Bot,
    state: FSMContext,
    ai_parser: BoxAiParser | None = None,
) -> None:
    if ai_parser is None:
        await message.answer("Распознавание голосовых сообщений пока не настроено. Пришлите список вещей текстом.")
        return
    if message.voice is None:
        return

    buffer = BytesIO()
    await bot.download(message.voice, destination=buffer)
    try:
        items = await ai_parser.parse_items_voice(buffer.getvalue(), message.voice.mime_type or "audio/ogg")
    except Exception:
        logger.exception("Не удалось распознать голосовой список вещей через Gemini")
        await message.answer("Не смогла разобрать голосовое сообщение. Пришлите список вещей текстом.")
        return

    if not items:
        await message.answer("Не удалось найти вещи в голосовом сообщении. Пришлите список текстом.")
        return

    await state.update_data(items=items, photo_file_ids=[])
    await state.set_state(CreateBox.photos)
    rows = "\n".join(f"• {html.escape(item)}" for item in items)
    await message.answer(
        (
            f"Распознала вещи:\n{rows}\n\n"
            "Пришлите одно или несколько фото коробки либо нажмите «Пропустить фото»."
        ),
        reply_markup=photos_keyboard(),
        parse_mode="HTML",
    )


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
async def create_box_finish(
    message: Message,
    state: FSMContext,
    config: Config,
    bot_username: str,
    household_id: int,
) -> None:
    await finish_create_box(message, state, config, bot_username, household_id)


@router.message(CreateBox.photos)
async def create_box_photo_unknown(message: Message) -> None:
    await message.answer("Пришлите фото, нажмите «Готово» или «Пропустить фото».")


@router.callback_query(F.data.startswith("box:show:"))
async def show_box_callback(callback: CallbackQuery, config: Config, household_id: int) -> None:
    box_id = int(callback.data.split(":")[-1])
    box = await database.get_box_by_id(config.database_path, box_id, household_id)
    if box is None:
        await callback.answer("Коробка не найдена.", show_alert=True)
        return
    await callback.answer()
    await send_box_card(callback.message, box)


@router.callback_query(F.data.startswith("box:add_item:"))
async def add_item_start(callback: CallbackQuery, state: FSMContext, config: Config, household_id: int) -> None:
    box_id = int(callback.data.split(":")[-1])
    box = await database.get_box_by_id(config.database_path, box_id, household_id)
    if box is None:
        await callback.answer("Коробка не найдена.", show_alert=True)
        return
    await state.set_state(AddItem.waiting_for_items)
    await state.update_data(box_id=box_id)
    await callback.answer()
    await callback.message.answer(
        "Введите вещи, которые нужно добавить. Можно через запятую или с новой строки.",
        reply_markup=cancel_keyboard(),
    )


@router.message(AddItem.waiting_for_items, F.voice)
async def add_item_voice(
    message: Message,
    bot: Bot,
    state: FSMContext,
    config: Config,
    household_id: int,
    ai_parser: BoxAiParser | None = None,
) -> None:
    if ai_parser is None:
        await message.answer("Распознавание голосовых сообщений пока не настроено. Пришлите вещи текстом.")
        return
    if message.voice is None:
        return

    data = await state.get_data()
    box_id = int(data["box_id"])
    box = await database.get_box_by_id(config.database_path, box_id, household_id)
    if box is None:
        await state.clear()
        await message.answer("Коробка не найдена.", reply_markup=main_menu())
        return

    buffer = BytesIO()
    await bot.download(message.voice, destination=buffer)
    try:
        items = await ai_parser.parse_items_voice(buffer.getvalue(), message.voice.mime_type or "audio/ogg")
    except Exception:
        logger.exception("Не удалось распознать голосовой список вещей через Gemini")
        await message.answer("Не смогла разобрать голосовое сообщение. Пришлите вещи текстом.")
        return

    if not items:
        await message.answer("Не удалось найти вещи в голосовом сообщении. Пришлите список текстом.")
        return

    commands = [AiCommand(action="add_items", box_id=box.id, box_code=box.code, items=items)]
    await state.update_data(ai_commands=ai_commands_to_dicts(commands))
    await state.set_state(AiAction.waiting_confirmation)
    await message.answer(
        format_ai_confirmation(commands, [box]),
        reply_markup=ai_confirmation_keyboard(),
        parse_mode="HTML",
    )


@router.message(AddItem.waiting_for_items, F.text)
async def add_item_finish(message: Message, state: FSMContext, config: Config, household_id: int) -> None:
    data = await state.get_data()
    box_id = int(data["box_id"])
    items = split_items(message.text)
    if not items:
        await message.answer("Не удалось найти вещи в сообщении. Пришлите список текстом.")
        return

    added = await database.add_items(config.database_path, box_id, household_id, items)
    await state.clear()
    if not added:
        await message.answer("Коробка не найдена.", reply_markup=main_menu())
        return
    box = await database.get_box_by_id(config.database_path, box_id, household_id)
    await message.answer("Вещи добавлены.", reply_markup=main_menu())
    if box:
        await send_box_card(message, box)


@router.callback_query(F.data.startswith("box:delete:"))
async def delete_box_callback(callback: CallbackQuery, config: Config, household_id: int) -> None:
    box_id = int(callback.data.split(":")[-1])
    box = await database.get_box_by_id(config.database_path, box_id, household_id)
    if box is None:
        await callback.answer("Коробка не найдена.", show_alert=True)
        return
    await database.delete_box(config.database_path, box_id, household_id)
    await callback.answer("Коробка удалена.")
    await callback.message.answer(
        f"Коробка {box_code_html(box.code)} удалена.",
        reply_markup=main_menu(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("box:qr:"))
async def send_qr_callback(callback: CallbackQuery, config: Config, bot_username: str, household_id: int) -> None:
    box_id = int(callback.data.split(":")[-1])
    box = await database.get_box_by_id(config.database_path, box_id, household_id)
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


@router.message(F.text == BTN_MY_GROUP)
async def my_group(message: Message, config: Config, household: database.Household) -> None:
    members = await database.list_household_members(config.database_path, household.id)
    member_rows = "\n".join(
        f"• {html.escape(member.name) if member.name else member.user_id}" for member in members
    )
    await message.answer(
        (
            f"👥 <b>{html.escape(household.name)}</b>\n\n"
            f"Участники:\n{member_rows or 'пока нет'}"
        ),
        reply_markup=household_invite_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "household:invite")
async def household_invite(callback: CallbackQuery, household: database.Household, bot_username: str) -> None:
    link = f"https://t.me/{bot_username}?start=join_{household.invite_code}"
    await callback.answer()
    await callback.message.answer(
        (
            f"Код приглашения: <code>{html.escape(household.invite_code)}</code>\n"
            f"Ссылка для входа: {html.escape(link)}"
        ),
        parse_mode="HTML",
    )


@router.message(F.text == BTN_EXPORT_PDF)
async def export_pdf(message: Message, config: Config, bot: Bot, household_id: int) -> None:
    boxes = await database.list_all_boxes(config.database_path, household_id)
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
