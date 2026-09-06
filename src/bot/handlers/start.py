from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from src.bot.keyboards import help_kb, main_menu_kb
from src.utils.telegram_html import safe_callback_answer, safe_callback_edit

router = Router()

WELCOME = (
    "<b>👋 HH-Killer — AI-поиск вакансий</b>\n\n"
    "Соберу вакансии с job-порталов <b>строго в вашем регионе</b> "
    "и покажу по каждой короткую сводку от ИИ.\n\n"
    "С чего начнём?"
)

HELP_TEXT = (
    "<b>ℹ️ Как это работает</b>\n\n"
    "<b>1. Запрос.</b> Опишите должность или пришлите резюме "
    "(PDF, DOCX, TXT, RTF) — AI сам достанет должность, навыки, опыт и город.\n\n"
    "<b>2. Настройка.</b> Один экран со всеми параметрами: регион, формат работы, "
    "занятость, зарплата, опыт, свежесть и сайты. Меняются одним нажатием.\n\n"
    "<b>3. Поиск.</b> Регион уходит прямо в поисковые запросы сайтов, "
    "а вакансии из других мест отбрасываются. Прогресс виден в реальном времени, "
    "поиск можно остановить и получить уже найденное.\n\n"
    "<b>4. Результат.</b> Карточка вакансии: должность, компания, локация, "
    "занятость, зарплата, дата, описание, ссылка на отклик — "
    "и сводка ИИ по конкретной вакансии.\n\n"
    "<b>Если сайт блокирует бота:</b> задайте <code>APIFY_API_TOKEN</code>, "
    "<code>PLAYWRIGHT_PROXY</code> (или список <code>PLAYWRIGHT_PROXIES</code>), "
    "либо сохраните cookies через <code>scripts/save_storage_state.py</code>."
)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()

    # Снимаем reply-клавиатуру, оставшуюся от предыдущих версий бота
    try:
        notice = await message.answer(
            "⌛", reply_markup=ReplyKeyboardRemove(), disable_notification=True,
        )
        await notice.delete()
    except Exception:
        pass

    await message.answer(WELCOME, reply_markup=main_menu_kb(), parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=help_kb(), parse_mode="HTML")


@router.callback_query(F.data == "help")
async def cb_help(callback: CallbackQuery) -> None:
    await safe_callback_answer(callback)
    await safe_callback_edit(callback, HELP_TEXT, reply_markup=help_kb(), parse_mode="HTML")


@router.callback_query(F.data == "menu:main")
async def cb_main_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await safe_callback_answer(callback)
    await state.clear()
    await safe_callback_edit(callback, WELCOME, reply_markup=main_menu_kb(), parse_mode="HTML")
