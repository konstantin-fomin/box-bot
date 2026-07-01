from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import database
from ..config import Config
from ..keyboards import BTN_CANCEL, BTN_DONE_PHOTOS, BTN_SKIP_PHOTO, main_menu, photos_keyboard
from ..states import AddPhoto
from .boxes import send_box_card


router = Router(name="photos")


@router.callback_query(F.data.startswith("box:add_photo:"))
async def add_photo_start(callback: CallbackQuery, state: FSMContext) -> None:
    box_id = int(callback.data.split(":")[-1])
    await state.set_state(AddPhoto.waiting_for_photos)
    await state.update_data(box_id=box_id, photo_file_ids=[])
    await callback.answer()
    await callback.message.answer(
        "Пришлите одно или несколько фото коробки. Когда закончите, нажмите «Готово».",
        reply_markup=photos_keyboard(),
    )


@router.message(AddPhoto.waiting_for_photos, F.photo)
async def add_photo_collect(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    photo_file_ids = list(data.get("photo_file_ids", []))
    photo_file_ids.append(message.photo[-1].file_id)
    await state.update_data(photo_file_ids=photo_file_ids)
    await message.answer("Фото добавлено. Можно прислать ещё или нажать «Готово».")


@router.message(AddPhoto.waiting_for_photos, F.text.in_({BTN_DONE_PHOTOS, BTN_SKIP_PHOTO}))
async def add_photo_finish(message: Message, state: FSMContext, config: Config) -> None:
    data = await state.get_data()
    box_id = int(data["box_id"])
    photo_file_ids = list(data.get("photo_file_ids", []))
    await state.clear()

    if photo_file_ids:
        await database.add_photos(config.database_path, box_id, photo_file_ids)
        await message.answer("Фото добавлены.", reply_markup=main_menu())
    else:
        await message.answer("Фото не добавлены.", reply_markup=main_menu())

    box = await database.get_box_by_id(config.database_path, box_id)
    if box:
        await send_box_card(message, box)


@router.message(AddPhoto.waiting_for_photos, F.text == BTN_CANCEL)
async def add_photo_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Добавление фото отменено.", reply_markup=main_menu())


@router.message(AddPhoto.waiting_for_photos)
async def add_photo_unknown(message: Message) -> None:
    await message.answer("Пришлите фото, нажмите «Готово» или «Пропустить фото».")
