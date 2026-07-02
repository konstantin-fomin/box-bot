from aiogram import F, Router
from aiogram.types import CallbackQuery

from .. import database
from ..config import Config
from ..keyboards import status_keyboard
from .boxes import box_code_html, send_box_card


router = Router(name="statuses")


@router.callback_query(F.data.startswith("box:status:"))
async def status_start(callback: CallbackQuery, config: Config) -> None:
    box_id = int(callback.data.split(":")[-1])
    box = await database.get_box_by_id(config.database_path, box_id)
    if box is None:
        await callback.answer("Коробка не найдена.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        f"Выберите новый статус для {box_code_html(box.code)}.",
        reply_markup=status_keyboard(box.id),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("status:set:"))
async def status_set(callback: CallbackQuery, config: Config) -> None:
    _, _, box_id_raw, status = callback.data.split(":")
    box_id = int(box_id_raw)
    await database.update_status(config.database_path, box_id, status)
    box = await database.get_box_by_id(config.database_path, box_id)
    await callback.answer("Статус обновлён.")
    if box:
        await send_box_card(callback.message, box)
