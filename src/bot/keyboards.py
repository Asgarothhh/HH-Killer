from typing import List

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton

from src.bot.states import InputMode
from src.utils.site_patterns import PRESET_GROUPS, PRESET_LABELS, SITE_PRESETS, get_site_config


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔍 Описать вакансию", callback_data="mode:preference"),
            InlineKeyboardButton(text="📄 Загрузить резюме", callback_data="mode:resume"),
        ],
        [
            InlineKeyboardButton(text="ℹ️ Как это работает", callback_data="help"),
        ],
    ])


def cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _url_label(url: str) -> str:
    config = get_site_config(url)
    if config:
        return config.name
    from urllib.parse import urlparse
    host = urlparse(url).netloc.replace("www.", "")
    return host[:28] or url[:28]


def urls_selection_kb(urls: List[str] | None = None) -> InlineKeyboardMarkup:
    """Пресеты + кнопки удаления выбранных сайтов."""
    urls = urls or []
    rows = []

    for group in PRESET_GROUPS:
        row = []
        for key in group:
            label = PRESET_LABELS.get(key, key)
            row.append(InlineKeyboardButton(text=f"+ {label}", callback_data=f"sp:{key}"))
        rows.append(row)

    for i, url in enumerate(urls[:15]):
        rows.append([
            InlineKeyboardButton(text=f"🗑 {_url_label(url)}", callback_data=f"urlrm:{i}"),
        ])

    action_row = []
    if urls:
        action_row.append(InlineKeyboardButton(text="🗑 Удалить все", callback_data="urls:clear"))
    action_row.append(InlineKeyboardButton(text="✅ Начать поиск", callback_data="search:start"))
    rows.append(action_row)

    rows.append([
        InlineKeyboardButton(text="🎛 Фильтры", callback_data="filters:edit"),
        InlineKeyboardButton(text="🏠 Меню", callback_data="menu:main"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def site_presets_kb() -> InlineKeyboardMarkup:
    """Обратная совместимость."""
    return urls_selection_kb([])


def resolve_preset_url(preset_key: str) -> str | None:
    return SITE_PRESETS.get(preset_key)


def filters_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📍 Город", callback_data="filters:city"),
            InlineKeyboardButton(text="💰 Зарплата", callback_data="filters:salary"),
        ],
        [
            InlineKeyboardButton(text="💼 Опыт", callback_data="filters:experience"),
        ],
        [
            InlineKeyboardButton(text="⏭ Пропустить → сайты", callback_data="filters:skip"),
        ],
        [
            InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main"),
        ],
    ])


def search_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏹ Остановить поиск", callback_data="search:cancel")],
    ])


def after_search_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Новый поиск", callback_data="menu:main")],
    ])


def mode_label(mode: InputMode) -> str:
    return "📝 Описание вакансии" if mode == InputMode.PREFERENCE else "📄 Резюме"
