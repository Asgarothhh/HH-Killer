from enum import Enum

from aiogram.fsm.state import State, StatesGroup


class InputMode(str, Enum):
    PREFERENCE = "preference"
    RESUME = "resume"


class SearchStates(StatesGroup):
    choosing_mode = State()
    waiting_preference = State()
    waiting_resume = State()
    waiting_filters = State()
    waiting_city = State()
    waiting_salary = State()
    waiting_experience = State()
    waiting_urls = State()
    searching = State()
