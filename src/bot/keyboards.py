"""Клавиатуры бота: один экран настройки + компактные подэкраны."""

from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.bot.states import InputMode
from src.services.search_query import EMPLOYMENT_TYPES, SearchFilters
from src.utils.location_utils import WorkFormat
from src.utils.site_patterns import PRESET_LABELS, SITE_PRESETS, get_site_config

# ── Пресеты подэкранов ─────────────────────────────────────────────────

REGION_PRESETS: Tuple[Tuple[str, str], ...] = (
    ("Warszawa", "Warszawa"),
    ("Kraków", "Kraków"),
    ("Wrocław", "Wrocław"),
    ("Gdańsk", "Gdańsk"),
    ("Poznań", "Poznań"),
    ("Łódź", "Łódź"),
    ("Katowice", "Katowice"),
    ("Вся Польша", "Polska"),
)

SALARY_PRESETS: Tuple[Tuple[str, int], ...] = (
    ("от 5 000", 5000),
    ("от 8 000", 8000),
    ("от 12 000", 12000),
    ("от 18 000", 18000),
    ("от 25 000", 25000),
)

EXPERIENCE_PRESETS: Tuple[Tuple[str, str, float], ...] = (
    ("Без опыта", "0", 0.0),
    ("до 1 года", "1", 1.0),
    ("1–3 года", "2", 2.0),
    ("3–6 лет", "4", 4.0),
    ("6+ лет", "7", 7.0),
)

FRESHNESS_PRESETS: Tuple[Tuple[str, int], ...] = (
    ("За сутки", 1),
    ("3 дня", 3),
    ("Неделя", 7),
    ("Месяц", 30),
)

QUICK_SITE_SETS: Dict[str, Tuple[str, ...]] = {
    "pl": ("pracuj", "praca", "olx", "infopraca"),
    "intl": ("linkedin", "indeed_pl", "jooble", "adzuna"),
    "hh": ("hh_pl", "hh_ru"),
}

# Порядок кнопок на экране выбора сайтов
SITE_ORDER: Tuple[str, ...] = (
    "pracuj", "praca", "infopraca", "olx",
    "jooble", "adzuna", "jobs_pl", "goldenline",
    "linkedin", "indeed_pl", "indeed_com",
    "hh_pl", "hh_ru",
)


def _rows(buttons: Sequence[InlineKeyboardButton], per_row: int = 2) -> List[List[InlineKeyboardButton]]:
    return [list(buttons[i:i + per_row]) for i in range(0, len(buttons), per_row)]


def _back_row(label: str = "← К настройкам") -> List[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=label, callback_data="setup:show")]


# ── Главное меню ───────────────────────────────────────────────────────

def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Найти по описанию", callback_data="mode:preference")],
        [InlineKeyboardButton(text="📄 Загрузить резюме", callback_data="mode:resume")],
        [InlineKeyboardButton(text="ℹ️ Как это работает", callback_data="help")],
    ])


def help_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 В меню", callback_data="menu:main")],
    ])


# ── Экран настройки поиска ─────────────────────────────────────────────

def setup_kb(filters: SearchFilters, sites_count: int, ready: bool) -> InlineKeyboardMarkup:
    """Единый экран: каждый параметр — одна кнопка со текущим значением."""
    region = filters.region_label if filters.region else "не задан"
    rows: List[List[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text=f"📍 Регион: {region}", callback_data="set:region"),
        ],
        [
            InlineKeyboardButton(
                text=f"🏢 {filters.format_label or 'Формат: любой'}",
                callback_data="set:format",
            ),
            InlineKeyboardButton(
                text=f"🕒 {filters.freshness_label or 'Свежесть: любая'}",
                callback_data="set:freshness",
            ),
        ],
        [
            InlineKeyboardButton(
                text=f"💰 {filters.salary_label or 'Зарплата: любая'}",
                callback_data="set:salary",
            ),
            InlineKeyboardButton(
                text=f"📈 {_short_experience(filters) or 'Опыт: любой'}",
                callback_data="set:experience",
            ),
        ],
        [
            InlineKeyboardButton(
                text=f"💼 {filters.employment_label or 'Занятость: любая'}",
                callback_data="set:employment",
            ),
        ],
        [
            InlineKeyboardButton(text=f"🌐 Сайты: {sites_count}", callback_data="set:sites"),
        ],
    ]

    if ready:
        rows.append([InlineKeyboardButton(text="🚀 Начать поиск", callback_data="search:start")])
    elif not filters.region:
        rows.append([InlineKeyboardButton(text="📍 Сначала укажите регион", callback_data="set:region")])
    else:
        rows.append([InlineKeyboardButton(text="🌐 Выберите хотя бы один сайт", callback_data="set:sites")])

    rows.append([
        InlineKeyboardButton(text="✏️ Изменить запрос", callback_data="setup:query"),
        InlineKeyboardButton(text="🏠 Меню", callback_data="menu:main"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _short_experience(filters: SearchFilters) -> Optional[str]:
    if filters.experience_years is not None:
        return f"Опыт: {filters.experience_years:g} лет"
    if filters.experience_job_min is not None or filters.experience_job_max is not None:
        return "Опыт: задан"
    return None


# ── Подэкраны ──────────────────────────────────────────────────────────

def region_kb(resume_region: Optional[str] = None) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=label, callback_data=f"region:{value}")
        for label, value in REGION_PRESETS
    ]
    rows = _rows(buttons, per_row=2)

    if resume_region:
        rows.insert(0, [InlineKeyboardButton(
            text=f"📄 Из резюме: {resume_region[:28]}",
            callback_data="region:__resume__",
        )])

    rows.append([InlineKeyboardButton(text="✍️ Другой город или страна", callback_data="region:__custom__")])
    rows.append(_back_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_kb(current: str) -> InlineKeyboardMarkup:
    options = (
        (WorkFormat.ANY, "Любой"),
        (WorkFormat.ONSITE, "Офис"),
        (WorkFormat.HYBRID, "Гибрид"),
        (WorkFormat.REMOTE, "Удалённо"),
    )
    buttons = [
        InlineKeyboardButton(
            text=f"{'✅ ' if fmt.value == current else ''}{label}",
            callback_data=f"fmt:{fmt.value}",
        )
        for fmt, label in options
    ]
    return InlineKeyboardMarkup(inline_keyboard=_rows(buttons, 2) + [_back_row()])


def employment_kb(selected: Iterable[str]) -> InlineKeyboardMarkup:
    chosen = set(selected)
    buttons = [
        InlineKeyboardButton(
            text=f"{'✅' if emp.key in chosen else '➕'} {emp.label}",
            callback_data=f"emp:{emp.key}",
        )
        for emp in EMPLOYMENT_TYPES
    ]
    rows = _rows(buttons, 1)
    rows.append([InlineKeyboardButton(text="🚫 Не важно", callback_data="emp:__clear__")])
    rows.append(_back_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def salary_kb() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=label, callback_data=f"sal:{value}")
        for label, value in SALARY_PRESETS
    ]
    rows = _rows(buttons, 2)
    rows.append([
        InlineKeyboardButton(text="✍️ Свой диапазон", callback_data="sal:__custom__"),
        InlineKeyboardButton(text="🚫 Не важно", callback_data="sal:__clear__"),
    ])
    rows.append(_back_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def experience_kb() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=label, callback_data=f"exp:{key}")
        for label, key, _ in EXPERIENCE_PRESETS
    ]
    rows = _rows(buttons, 2)
    rows.append([
        InlineKeyboardButton(text="✍️ Указать точно", callback_data="exp:__custom__"),
        InlineKeyboardButton(text="🚫 Не важно", callback_data="exp:__clear__"),
    ])
    rows.append(_back_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def freshness_kb() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=label, callback_data=f"fresh:{days}")
        for label, days in FRESHNESS_PRESETS
    ]
    rows = _rows(buttons, 2)
    rows.append([InlineKeyboardButton(text="🚫 Не важно", callback_data="fresh:0")])
    rows.append(_back_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sites_kb(selected_urls: Sequence[str]) -> InlineKeyboardMarkup:
    """Список сайтов с переключателями вместо отдельных кнопок удаления."""
    chosen = set(selected_urls)
    buttons = []
    for key in SITE_ORDER:
        url = SITE_PRESETS.get(key)
        if not url:
            continue
        mark = "✅" if url in chosen else "➕"
        buttons.append(InlineKeyboardButton(
            text=f"{mark} {PRESET_LABELS.get(key, key)}",
            callback_data=f"site:{key}",
        ))

    rows = _rows(buttons, 2)
    rows.append([
        InlineKeyboardButton(text="🇵🇱 Польские", callback_data="sites:pl"),
        InlineKeyboardButton(text="🌍 Международные", callback_data="sites:intl"),
    ])
    rows.append([
        InlineKeyboardButton(text="✍️ Свой URL", callback_data="sites:__custom__"),
        InlineKeyboardButton(text="🗑 Очистить", callback_data="sites:__clear__"),
    ])
    rows.append(_back_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def search_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏹ Остановить и показать найденное", callback_data="search:cancel")],
    ])


def after_search_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎛 Изменить фильтры", callback_data="setup:show")],
        [InlineKeyboardButton(text="🔄 Новый поиск", callback_data="menu:main")],
    ])


def site_label(url: str) -> str:
    config = get_site_config(url)
    if config:
        return config.name
    host = (urlparse(url).netloc or url).replace("www.", "")
    return host[:28] or url[:28]


def resolve_preset_url(preset_key: str) -> Optional[str]:
    return SITE_PRESETS.get(preset_key)


def quick_site_urls(set_key: str) -> List[str]:
    return [SITE_PRESETS[k] for k in QUICK_SITE_SETS.get(set_key, ()) if k in SITE_PRESETS]


def mode_label(mode: InputMode) -> str:
    return "описание вакансии" if mode == InputMode.PREFERENCE else "резюме"
