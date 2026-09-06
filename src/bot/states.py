from enum import Enum

from aiogram.fsm.state import State, StatesGroup


class InputMode(str, Enum):
    PREFERENCE = "preference"
    RESUME = "resume"


class SearchStates(StatesGroup):
    waiting_preference = State()
    waiting_resume = State()
    setup = State()             # единый экран настройки поиска
    waiting_region = State()
    waiting_salary = State()
    waiting_experience = State()
    waiting_site_url = State()
    searching = State()
