from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from .database import Box, STATUS_LABELS, STATUSES


BTN_NEW_BOX = "📦 Новая коробка"
BTN_SEARCH = "🔍 Найти вещь"
BTN_LIST = "📋 Список коробок"
BTN_MORE = "⚙️ Ещё"
BTN_EXPORT_PDF = "📄 Экспорт в PDF"
BTN_SKIP_PHOTO = "Пропустить фото"
BTN_DONE_PHOTOS = "Готово"
BTN_CANCEL = "Отмена"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_NEW_BOX), KeyboardButton(text=BTN_SEARCH)],
            [KeyboardButton(text=BTN_LIST), KeyboardButton(text=BTN_MORE)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_CANCEL)]],
        resize_keyboard=True,
    )


def photos_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_DONE_PHOTOS), KeyboardButton(text=BTN_SKIP_PHOTO)],
            [KeyboardButton(text=BTN_CANCEL)],
        ],
        resize_keyboard=True,
    )


def more_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_EXPORT_PDF)],
            [KeyboardButton(text=BTN_NEW_BOX), KeyboardButton(text=BTN_SEARCH)],
            [KeyboardButton(text=BTN_LIST), KeyboardButton(text=BTN_MORE)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Дополнительные действия",
    )


def box_actions(box: Box) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✏️ Добавить вещь", callback_data=f"box:add_item:{box.id}"),
                InlineKeyboardButton(text="🗑 Удалить коробку", callback_data=f"box:delete:{box.id}"),
            ],
            [
                InlineKeyboardButton(text="📷 Добавить фото", callback_data=f"box:add_photo:{box.id}"),
                InlineKeyboardButton(text="🔄 Сменить статус", callback_data=f"box:status:{box.id}"),
            ],
            [
                InlineKeyboardButton(text="🔳 QR-код", callback_data=f"box:qr:{box.id}"),
            ],
        ]
    )


def status_keyboard(box_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=STATUS_LABELS[status], callback_data=f"status:set:{box_id}:{status}")]
            for status in STATUSES
        ]
    )


def boxes_list_keyboard(boxes: list[Box]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Открыть {index} · {box.room}", callback_data=f"box:show:{box.id}")]
            for index, box in enumerate(boxes, start=1)
        ]
    )


def list_filters_keyboard(rooms: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="Все", callback_data="list:all"),
            InlineKeyboardButton(text="По статусу", callback_data="list:statuses"),
        ]
    ]
    for room in rooms[:20]:
        rows.append([InlineKeyboardButton(text=f"Комната: {room}", callback_data=f"list:room:{room}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def statuses_filter_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=STATUS_LABELS[status], callback_data=f"list:status:{status}")]
            for status in STATUSES
        ]
    )
