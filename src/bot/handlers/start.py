from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot.keyboards import main_menu_kb
from src.utils.telegram_html import safe_callback_answer, safe_callback_edit
from src.bot.states import SearchStates
from src.utils.site_patterns import list_supported_sites_text

router = Router()

WELCOME = (
    "<b>👋 HH-Killer — AI-поиск вакансий</b>\n\n"
    "Найду релевантные вакансии на любом job-сайте — "
    "от польских порталов до LinkedIn и Indeed.\n\n"
    "<b>Два способа начать:</b>\n"
    "1️⃣ <b>Описать вакансию</b> — должность и пожелания\n"
    "2️⃣ <b>Загрузить резюме</b> — AI проанализирует и подберёт вакансии\n\n"
    "Выберите сайты кнопками или отправьте любую ссылку на job-портал."
)

HELP_TEXT = (
    "<b>ℹ️ Как пользоваться</b>\n\n"
    "1. Выберите режим: описание вакансии или резюме (PDF/DOCX/TXT)\n"
    "2. Опционально: город и зарплатный диапазон\n"
    "3. Выберите сайты кнопками или отправьте свои URL\n"
    "4. Нажмите «Начать поиск» — прогресс в реальном времени\n"
    "5. «⏹ Остановить» — вернёт уже найденные вакансии\n\n"
    "<b>Поддерживаемые сайты:</b>\n"
    f"{list_supported_sites_text()}\n\n"
    "<b>Движки поиска:</b>\n"
    "• Stealth Playwright — обход базовой bot-защиты\n"
    "• Apify — LinkedIn, Indeed и fallback при блокировке\n\n"
    "<b>Если сайт блокирует:</b> задайте APIFY_API_TOKEN, "
    "PLAYWRIGHT_PROXY или сохраните cookies через "
    "<code>scripts/save_storage_state.py</code>\n\n"
    "Можно указать <b>любой</b> job-сайт — система адаптируется автоматически."
)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(WELCOME, reply_markup=main_menu_kb(), parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, parse_mode="HTML")


@router.callback_query(F.data == "help")
async def cb_help(callback: CallbackQuery) -> None:
    await safe_callback_answer(callback)
    await safe_callback_edit(callback, HELP_TEXT, reply_markup=main_menu_kb(), parse_mode="HTML")


@router.callback_query(F.data == "menu:main")
async def cb_main_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await safe_callback_answer(callback)
    await state.clear()
    await safe_callback_edit(callback, WELCOME, reply_markup=main_menu_kb(), parse_mode="HTML")
