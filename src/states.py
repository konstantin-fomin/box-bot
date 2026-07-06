from aiogram.fsm.state import State, StatesGroup


class CreateBox(StatesGroup):
    room = State()
    items = State()
    photos = State()


class AddItem(StatesGroup):
    waiting_for_items = State()


class AddPhoto(StatesGroup):
    waiting_for_photos = State()


class Search(StatesGroup):
    waiting_for_query = State()


class AiAction(StatesGroup):
    waiting_confirmation = State()


class HouseholdOnboarding(StatesGroup):
    waiting_for_name = State()
    waiting_for_invite_code = State()
