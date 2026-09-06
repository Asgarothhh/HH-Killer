"""Сценарий поиска: настройка одним экраном, запуск, выдача результатов."""

import asyncio
from typing import List, Optional

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.types import CallbackQuery, Message

from src.bot.keyboards import (
    EXPERIENCE_PRESETS,
    after_search_kb,
    employment_kb,
    experience_kb,
    format_kb,
    freshness_kb,
    quick_site_urls,
    region_kb,
    resolve_preset_url,
    salary_kb,
    search_cancel_kb,
    setup_kb,
    site_label,
    sites_kb,
)
from src.bot.progress import SearchProgressTracker
from src.bot.search_session import (
    cancel_session,
    end_session,
    get_dropped_jobs,
    get_session,
    mark_dropped_shown,
    save_dropped_jobs,
    start_session,
)
from src.bot.states import InputMode, SearchStates
from src.models.models import JobInfo, ResumeProfile
from src.services.job_filters import FilterReport
from src.services.link_validator import extract_urls_from_text, validate_user_links
from src.services.resume_service import (
    analyze_resume_file,
    format_llm_error,
    profile_to_search_query,
)
from src.services.scraper_service import format_job_card, run_multi_site_search
from src.services.search_query import (
    SearchFilters,
    build_matching_context,
    build_role_queries,
    parse_experience,
    parse_salary,
)
from src.utils.logger import get_logger
from src.utils.location_utils import MAX_REGIONS, merge_regions, parse_region_list, toggle_region
from src.utils.telegram_html import (
    download_document_bytes,
    h,
    safe_callback_answer,
    safe_edit_text,
)

router = Router()
logger = get_logger(__name__)

MAX_JOBS_PER_SITE = 5
MAX_CARDS = 10
MAX_DROPPED_CARDS = 10


def _ru_jobs(count: int) -> str:
    n = abs(count) % 100
    if 11 <= n <= 14:
        word = "вакансий"
    else:
        last = n % 10
        word = "вакансия" if last == 1 else "вакансии" if 2 <= last <= 4 else "вакансий"
    return f"{count} {word}"


def _ru_shown_line(shown: int, total: int) -> str:
    one = shown % 10 == 1 and shown % 100 != 11
    if shown == total:
        verb = "Просмотрена" if one else "Просмотрено"
        return f"{verb} {_ru_jobs(total)} — все прошли фильтры."
    verb = "Показана" if one else "Показано"
    return f"{verb} {_ru_jobs(shown)} из {total}."


# ── Состояние поиска в FSM ─────────────────────────────────────────────

def _regions_from_data(data: dict) -> List[str]:
    stored = data.get("search_regions")
    if stored:
        return list(merge_regions(stored))
    single = data.get("search_region")
    if single:
        return list(parse_region_list(str(single)))
    return []


def _filters_from_data(data: dict) -> SearchFilters:
    return SearchFilters(
        regions=tuple(_regions_from_data(data)),
        work_format=data.get("search_format", "any"),
        employment_types=tuple(data.get("search_employment", ())),
        salary_min=data.get("search_salary_min"),
        salary_max=data.get("search_salary_max"),
        currency=data.get("search_currency", "PLN"),
        experience_years=data.get("search_experience_years"),
        experience_job_min=data.get("search_experience_job_min"),
        experience_job_max=data.get("search_experience_job_max"),
        posted_within_days=data.get("search_freshness"),
    )


def _setup_text(data: dict, filters: SearchFilters, urls: List[str]) -> str:
    mode = data.get("input_mode", InputMode.PREFERENCE.value)
    preference = str(data.get("user_job_preference", ""))
    queries = build_role_queries(preference, None)
    source = "резюме" if mode == InputMode.RESUME.value else "описание"

    lines = [
        "<b>🎯 Настройка поиска</b>",
        "",
        f"<b>Ищем:</b> {h(queries[0] if queries else preference[:60])}",
        f"<i>источник запроса: {source}</i>",
    ]

    if urls:
        names = ", ".join(site_label(u) for u in urls[:6])
        extra = f" и ещё {len(urls) - 6}" if len(urls) > 6 else ""
        lines += ["", f"<b>Сайты:</b> {h(names)}{extra}"]

    if not filters.has_region:
        lines += ["", "⚠️ <b>Укажите регион</b> — можно несколько городов, поиск идёт строго по ним."]
    elif not urls:
        lines += ["", "⚠️ Выберите хотя бы один сайт."]
    else:
        lines += ["", "Всё готово. Меняйте параметры кнопками ниже или запускайте поиск."]

    return "\n".join(lines)


async def _edit(callback: CallbackQuery, text: str, **kwargs) -> Optional[Message]:
    """Редактирует сообщение колбэка, если оно ещё доступно."""
    if not callback.message:
        await safe_callback_answer(callback, "Сообщение недоступно, откройте /start", show_alert=True)
        return None
    return await safe_edit_text(callback.message, text, **kwargs)


async def _show_setup(
    message: Optional[Message],
    state: FSMContext,
    *,
    edit: bool = True,
) -> Optional[Message]:
    """Перерисовывает единственный экран настройки."""
    if message is None:
        return None

    await state.set_state(SearchStates.setup)
    data = await state.get_data()
    filters = _filters_from_data(data)
    urls: List[str] = list(data.get("urls", []))
    ready = bool(filters.has_region and urls and data.get("user_job_preference"))

    text = _setup_text(data, filters, urls)
    markup = setup_kb(filters, len(urls), ready)

    if edit:
        result = await safe_edit_text(message, text, reply_markup=markup, parse_mode="HTML")
    else:
        result = await message.answer(text, reply_markup=markup, parse_mode="HTML")

    if result:
        await state.update_data(setup_message_id=result.message_id)
    return result


async def _return_to_setup(message: Message, state: FSMContext, bot: Bot) -> None:
    """После текстового ввода: убирает сообщение пользователя и обновляет экран."""
    data = await state.get_data()
    setup_id = data.get("setup_message_id")

    try:
        await message.delete()
    except Exception:
        pass

    if setup_id:
        filters = _filters_from_data(data)
        urls: List[str] = list(data.get("urls", []))
        ready = bool(filters.has_region and urls and data.get("user_job_preference"))
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=setup_id,
                text=_setup_text(data, filters, urls),
                reply_markup=setup_kb(filters, len(urls), ready),
                parse_mode="HTML",
            )
            await state.set_state(SearchStates.setup)
            return
        except Exception as error:
            logger.debug("Не удалось обновить экран настройки: %s", error)

    await _show_setup(message, state, edit=False)


async def _prompt(callback: CallbackQuery, text: str, state: FSMContext, next_state: State) -> None:
    await state.set_state(next_state)
    await _edit(callback, text, parse_mode="HTML")
    await safe_callback_answer(callback)


# ── Выбор режима ───────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("mode:"))
async def cb_choose_mode(callback: CallbackQuery, state: FSMContext) -> None:
    mode = InputMode(callback.data.split(":", 1)[1])
    await state.update_data(input_mode=mode.value, urls=[])

    if mode == InputMode.PREFERENCE:
        await state.set_state(SearchStates.waiting_preference)
        text = (
            "<b>📝 Кого ищем?</b>\n\n"
            "Напишите должность и, если нужно, пару уточнений.\n"
            "Например: <i>Python-разработчик, ML</i>\n\n"
            "Регион и фильтры настроим на следующем шаге."
        )
    else:
        await state.set_state(SearchStates.waiting_resume)
        text = (
            "<b>📄 Пришлите файл резюме</b>\n\n"
            "PDF, DOCX, TXT или RTF. AI извлечёт должность, навыки, "
            "опыт и город — их можно будет поправить вручную."
        )

    await _edit(callback, text, parse_mode="HTML")
    await safe_callback_answer(callback)


@router.message(SearchStates.waiting_preference, F.text)
async def receive_preference(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    if len(text) < 3:
        await message.answer("Слишком коротко — напишите должность, например «Python developer».")
        return

    await state.update_data(user_job_preference=text, resume_profile=None)
    await _apply_default_sites(state)
    await _show_setup(message, state, edit=False)


@router.message(SearchStates.waiting_resume, F.document)
async def receive_resume(message: Message, state: FSMContext, bot: Bot) -> None:
    status = await message.answer("⏳ Читаю резюме…")

    try:
        raw = await download_document_bytes(bot, message.document)
        profile: ResumeProfile = await analyze_resume_file(message.document.file_name, raw)
    except Exception as error:
        logger.error("Ошибка анализа резюме: %s", error, exc_info=True)
        await safe_edit_text(status, format_llm_error(error), parse_mode="HTML")
        return

    updates = {
        "user_job_preference": profile_to_search_query(profile),
        "resume_profile": profile.model_dump(),
    }
    if profile.experience_years is not None:
        updates["search_experience_years"] = profile.experience_years

    data = await state.get_data()
    if profile.location and not _regions_from_data(data):
        updates["search_regions"] = list(parse_region_list(profile.location))

    await state.update_data(**updates)
    await _apply_default_sites(state)

    summary = "\n".join(filter(None, (
        "<b>✅ Резюме разобрано</b>",
        "",
        f"👤 {h(profile.full_name or 'Кандидат')}",
        f"🎯 {h(profile.desired_role)}"
        + (f" · {h(profile.seniority_level)}" if profile.seniority_level else ""),
        f"📈 Опыт: {profile.experience_years:g} лет" if profile.experience_years else None,
        f"🛠 {h(', '.join(profile.skills[:8]))}" if profile.skills else None,
        f"📍 {h(profile.location)}" if profile.location else "📍 Город в резюме не найден",
    )))
    await safe_edit_text(status, summary, parse_mode="HTML")
    await _show_setup(message, state, edit=False)


@router.message(SearchStates.waiting_resume)
async def resume_expected_document(message: Message) -> None:
    await message.answer("Отправьте файл резюме: PDF, DOCX, TXT или RTF.")


async def _apply_default_sites(state: FSMContext) -> None:
    """Предзаполняет популярные польские сайты, чтобы можно было искать сразу."""
    data = await state.get_data()
    if not data.get("urls"):
        await state.update_data(urls=quick_site_urls("pl")[:2])


# ── Экран настройки ────────────────────────────────────────────────────

@router.callback_query(F.data == "setup:show")
async def cb_setup_show(callback: CallbackQuery, state: FSMContext) -> None:
    await _show_setup(callback.message, state)
    await safe_callback_answer(callback)


@router.callback_query(F.data == "setup:query")
async def cb_setup_query(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    current = h(str(data.get("user_job_preference", ""))[:200])
    await _prompt(
        callback,
        f"<b>✏️ Новый запрос</b>\n\nТекущий: <i>{current}</i>\n\n"
        "Отправьте должность одним сообщением.",
        state,
        SearchStates.waiting_preference,
    )


# ── Регион ─────────────────────────────────────────────────────────────

def _region_screen_text(selected: List[str]) -> str:
    if selected:
        listed = ", ".join(selected)
        return (
            "<b>📍 Регионы поиска</b>\n\n"
            f"Выбрано: <b>{h(listed)}</b>\n"
            f"Можно до {MAX_REGIONS} городов. Вакансия проходит, если она "
            "хотя бы в одном из них.\n\n"
            "Нажмите город ещё раз, чтобы убрать его."
        )
    return (
        "<b>📍 Регионы поиска</b>\n\n"
        "Выберите один или несколько городов. "
        "Можно ввести списком: <i>Брест, Минск и Москва</i>."
    )


async def _show_region_screen(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected = _regions_from_data(data)
    resume = (data.get("resume_profile") or {}).get("location")
    await _edit(
        callback,
        _region_screen_text(selected),
        reply_markup=region_kb(selected, resume),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "set:region")
async def cb_set_region(callback: CallbackQuery, state: FSMContext) -> None:
    await _show_region_screen(callback, state)
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("region:"))
async def cb_pick_region(callback: CallbackQuery, state: FSMContext) -> None:
    value = callback.data.split(":", 1)[1]
    data = await state.get_data()
    selected = _regions_from_data(data)

    if value == "__custom__":
        await _prompt(
            callback,
            "<b>📍 Введите города</b>\n\n"
            "Один или несколько через запятую, например:\n"
            "<i>Брест, Минск и Москва</i>\n"
            f"Максимум {MAX_REGIONS}.",
            state,
            SearchStates.waiting_region,
        )
        return

    if value == "__clear__":
        await state.update_data(search_regions=[], search_region=None)
        await _show_region_screen(callback, state)
        await safe_callback_answer(callback, "Регионы сброшены")
        return

    if value == "__resume__":
        resume = (data.get("resume_profile") or {}).get("location") or ""
        added = parse_region_list(resume)
        if not added:
            await safe_callback_answer(callback, "В резюме нет города", show_alert=True)
            return
        merged = merge_regions(selected, added)
        await state.update_data(search_regions=list(merged), search_region=None)
        await _show_region_screen(callback, state)
        await safe_callback_answer(callback, f"📍 {', '.join(merged)}")
        return

    before = tuple(selected)
    updated = toggle_region(selected, value)
    if updated == before and len(before) >= MAX_REGIONS:
        await safe_callback_answer(
            callback,
            f"Можно выбрать не больше {MAX_REGIONS} регионов",
            show_alert=True,
        )
        return

    await state.update_data(search_regions=list(updated), search_region=None)
    await _show_region_screen(callback, state)
    await safe_callback_answer(callback, f"📍 {', '.join(updated) if updated else 'не задан'}")


@router.message(SearchStates.waiting_region, F.text)
async def receive_region(message: Message, state: FSMContext, bot: Bot) -> None:
    added = parse_region_list(message.text)
    if not added:
        await message.answer(
            "Не разобрал города. Напишите, например: <i>Брест, Минск, Москва</i>",
            parse_mode="HTML",
        )
        return
    data = await state.get_data()
    merged = merge_regions(_regions_from_data(data), added)
    await state.update_data(search_regions=list(merged), search_region=None)
    await _return_to_setup(message, state, bot)


# ── Формат работы ──────────────────────────────────────────────────────

@router.callback_query(F.data == "set:format")
async def cb_set_format(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await _edit(
        callback,
        "<b>🏢 Формат работы</b>\n\n"
        "«Удалённо» оставит только вакансии с явно указанной удалёнкой.",
        reply_markup=format_kb(data.get("search_format", "any")),
        parse_mode="HTML",
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("fmt:"))
async def cb_pick_format(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(search_format=callback.data.split(":", 1)[1])
    await _show_setup(callback.message, state)
    await safe_callback_answer(callback)


# ── Тип занятости ──────────────────────────────────────────────────────

@router.callback_query(F.data == "set:employment")
async def cb_set_employment(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await _edit(
        callback,
        "<b>💼 Тип занятости</b>\n\nМожно выбрать несколько.",
        reply_markup=employment_kb(data.get("search_employment", ())),
        parse_mode="HTML",
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("emp:"))
async def cb_toggle_employment(callback: CallbackQuery, state: FSMContext) -> None:
    key = callback.data.split(":", 1)[1]
    data = await state.get_data()
    selected = list(data.get("search_employment", ()))

    if key == "__clear__":
        selected = []
    elif key in selected:
        selected.remove(key)
    else:
        selected.append(key)

    await state.update_data(search_employment=selected)
    await _edit(
        callback,
        "<b>💼 Тип занятости</b>\n\nМожно выбрать несколько.",
        reply_markup=employment_kb(selected),
        parse_mode="HTML",
    )
    await safe_callback_answer(callback)


# ── Зарплата ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "set:salary")
async def cb_set_salary(callback: CallbackQuery, state: FSMContext) -> None:
    await _edit(
        callback,
        "<b>💰 Зарплата, PLN в месяц</b>\n\n"
        "Вакансии без указанной зарплаты остаются в выдаче.",
        reply_markup=salary_kb(),
        parse_mode="HTML",
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("sal:"))
async def cb_pick_salary(callback: CallbackQuery, state: FSMContext) -> None:
    value = callback.data.split(":", 1)[1]

    if value == "__custom__":
        await _prompt(
            callback,
            "<b>💰 Свой диапазон</b>\n\n"
            "Например: <i>8000-12000 PLN</i>, <i>от 15000</i>, <i>4k EUR</i>",
            state,
            SearchStates.waiting_salary,
        )
        return

    if value == "__clear__":
        await state.update_data(search_salary_min=None, search_salary_max=None)
    else:
        await state.update_data(
            search_salary_min=int(value), search_salary_max=None, search_currency="PLN",
        )

    await _show_setup(callback.message, state)
    await safe_callback_answer(callback)


@router.message(SearchStates.waiting_salary, F.text)
async def receive_salary(message: Message, state: FSMContext, bot: Bot) -> None:
    low, high, currency = parse_salary(message.text)
    if low is None and high is None:
        await message.answer("Не понял диапазон. Пример: 8000-12000 PLN")
        return
    await state.update_data(
        search_salary_min=low, search_salary_max=high, search_currency=currency,
    )
    await _return_to_setup(message, state, bot)


# ── Опыт ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "set:experience")
async def cb_set_experience(callback: CallbackQuery, state: FSMContext) -> None:
    await _edit(
        callback,
        "<b>📈 Ваш опыт работы</b>\n\n"
        "Вакансии с требованиями существенно выше вашего опыта будут скрыты.",
        reply_markup=experience_kb(),
        parse_mode="HTML",
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("exp:"))
async def cb_pick_experience(callback: CallbackQuery, state: FSMContext) -> None:
    value = callback.data.split(":", 1)[1]

    if value == "__custom__":
        await _prompt(
            callback,
            "<b>📈 Опыт</b>\n\n"
            "Примеры:\n• <i>6</i> — ваш стаж\n"
            "• <i>3-5</i> — нужны вакансии уровня 3–5 лет",
            state,
            SearchStates.waiting_experience,
        )
        return

    if value == "__clear__":
        await state.update_data(
            search_experience_years=None,
            search_experience_job_min=None,
            search_experience_job_max=None,
        )
    else:
        years = next((y for _, key, y in EXPERIENCE_PRESETS if key == value), None)
        await state.update_data(search_experience_years=years)

    await _show_setup(callback.message, state)
    await safe_callback_answer(callback)


@router.message(SearchStates.waiting_experience, F.text)
async def receive_experience(message: Message, state: FSMContext, bot: Bot) -> None:
    years, job_min, job_max = parse_experience(message.text)
    if years is None and job_min is None and job_max is None:
        await message.answer("Не понял. Пример: 6 или 3-5")
        return
    await state.update_data(
        search_experience_years=years,
        search_experience_job_min=job_min,
        search_experience_job_max=job_max,
    )
    await _return_to_setup(message, state, bot)


# ── Свежесть ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "set:freshness")
async def cb_set_freshness(callback: CallbackQuery, state: FSMContext) -> None:
    await _edit(
        callback,
        "<b>🕒 Дата публикации</b>\n\n"
        "Вакансии без даты публикации остаются в выдаче.",
        reply_markup=freshness_kb(),
        parse_mode="HTML",
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("fresh:"))
async def cb_pick_freshness(callback: CallbackQuery, state: FSMContext) -> None:
    days = int(callback.data.split(":", 1)[1])
    await state.update_data(search_freshness=days or None)
    await _show_setup(callback.message, state)
    await safe_callback_answer(callback)


# ── Сайты ──────────────────────────────────────────────────────────────

async def _render_sites(message: Optional[Message], state: FSMContext) -> None:
    if message is None:
        return
    data = await state.get_data()
    urls: List[str] = list(data.get("urls", []))
    listed = "\n".join(f"• {h(site_label(u))}" for u in urls) or "— ничего не выбрано —"
    await safe_edit_text(
        message,
        f"<b>🌐 Где искать</b> ({len(urls)})\n\n{listed}\n\n"
        "<i>Нажмите на сайт, чтобы добавить или убрать его.</i>",
        reply_markup=sites_kb(urls),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "set:sites")
async def cb_set_sites(callback: CallbackQuery, state: FSMContext) -> None:
    await _render_sites(callback.message, state)
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("site:"))
async def cb_toggle_site(callback: CallbackQuery, state: FSMContext) -> None:
    url = resolve_preset_url(callback.data.split(":", 1)[1])
    if not url:
        await safe_callback_answer(callback, "Неизвестный сайт", show_alert=True)
        return

    data = await state.get_data()
    urls: List[str] = list(data.get("urls", []))
    if url in urls:
        urls.remove(url)
        note = f"➖ {site_label(url)}"
    else:
        urls.append(url)
        note = f"✅ {site_label(url)}"

    await state.update_data(urls=urls)
    await _render_sites(callback.message, state)
    await safe_callback_answer(callback, note)


@router.callback_query(F.data.startswith("sites:"))
async def cb_sites_action(callback: CallbackQuery, state: FSMContext) -> None:
    action = callback.data.split(":", 1)[1]

    if action == "__custom__":
        await _prompt(
            callback,
            "<b>🌐 Свой job-сайт</b>\n\n"
            "Отправьте одну или несколько ссылок вида https://…\n"
            "Подойдёт любой портал — система разберётся со структурой сама.",
            state,
            SearchStates.waiting_site_url,
        )
        return

    data = await state.get_data()
    urls: List[str] = list(data.get("urls", []))

    if action == "__clear__":
        urls = []
    else:
        for url in quick_site_urls(action):
            if url not in urls:
                urls.append(url)

    await state.update_data(urls=urls)
    await _render_sites(callback.message, state)
    await safe_callback_answer(callback)


@router.message(SearchStates.waiting_site_url, F.text)
async def receive_site_url(message: Message, state: FSMContext, bot: Bot) -> None:
    found = extract_urls_from_text(message.text)
    if not found:
        await message.answer("Не нашёл ссылок. Отправьте адрес вида https://…")
        return

    data = await state.get_data()
    urls: List[str] = list(data.get("urls", []))
    urls.extend(u for u in found if u not in urls)
    await state.update_data(urls=urls)
    await _return_to_setup(message, state, bot)


# ── Запуск поиска ──────────────────────────────────────────────────────

@router.callback_query(F.data == "search:cancel")
async def cancel_active_search(callback: CallbackQuery, state: FSMContext) -> None:
    if cancel_session(callback.from_user.id):
        await safe_callback_answer(callback, "⏹ Останавливаю, сохраняю найденное…")
    else:
        await safe_callback_answer(callback, "Активного поиска нет", show_alert=True)


@router.callback_query(F.data == "search:dropped")
async def cb_show_dropped(callback: CallbackQuery, bot: Bot) -> None:
    user_id = callback.from_user.id
    dropped = get_dropped_jobs(user_id)
    if not dropped:
        await safe_callback_answer(
            callback,
            "Список отброшенных уже недоступен — запустите поиск ещё раз",
            show_alert=True,
        )
        return
    if not mark_dropped_shown(user_id):
        await safe_callback_answer(callback, "Отброшенные вакансии уже отправлены выше")
        return

    await safe_callback_answer(callback, f"Отправляю {_ru_jobs(len(dropped))}")

    chat = callback.message.chat if callback.message else None
    if chat is None:
        return

    header = (
        f"<b>👁 Отброшенные вакансии</b> — {_ru_jobs(len(dropped))}\n"
        "Это объявления, которые не прошли выбранные фильтры. "
        "По каждой указано, почему её скрыли."
    )
    await bot.send_message(chat.id, header, parse_mode="HTML")

    to_send = dropped[:MAX_DROPPED_CARDS]
    for index, item in enumerate(to_send, 1):
        try:
            await bot.send_message(
                chat.id,
                format_job_card(item.job, index, drop_note=item.explanation),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as error:
            logger.warning("Не удалось отправить отброшенную карточку %d: %s", index, error)
        await asyncio.sleep(0.3)

    leftover = len(dropped) - len(to_send)
    if leftover:
        await bot.send_message(
            chat.id,
            f"… и ещё {_ru_jobs(leftover)} не показаны, чтобы не заспамить чат.",
        )


@router.callback_query(F.data == "search:start")
async def cb_start_search(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    urls: List[str] = list(data.get("urls", []))
    preference = data.get("user_job_preference", "")
    filters = _filters_from_data(data)

    if not preference:
        await safe_callback_answer(callback, "Сначала опишите вакансию или пришлите резюме", show_alert=True)
        return
    if not filters.has_region:
        await safe_callback_answer(callback, "Укажите регион поиска", show_alert=True)
        return
    if not urls:
        await safe_callback_answer(callback, "Добавьте хотя бы один сайт", show_alert=True)
        return

    await safe_callback_answer(callback, "🚀 Начинаю")
    await state.set_state(SearchStates.searching)

    progress_msg = await _edit(
        callback, "🔍 Проверяю ссылки…", parse_mode="HTML",
    )
    if progress_msg is None:
        await state.set_state(SearchStates.setup)
        return

    validation = await validate_user_links(urls)
    if validation.invalid_links:
        errors = "\n".join(
            f"❌ {h(site_label(r.url))}: {h(r.message)}" for r in validation.invalid_links
        )
        await safe_edit_text(
            progress_msg,
            f"<b>Не получилось использовать эти сайты:</b>\n{errors}\n\n"
            "Уберите их на экране «Сайты» и попробуйте снова.",
            reply_markup=after_search_kb(),
            parse_mode="HTML",
        )
        await state.set_state(SearchStates.setup)
        return

    valid_urls = [r.normalized_url for r in validation.valid_links]

    profile: Optional[ResumeProfile] = None
    if data.get("resume_profile"):
        try:
            profile = ResumeProfile.model_validate(data["resume_profile"])
        except Exception as error:
            logger.warning("Не удалось восстановить профиль резюме: %s", error)

    matching_context = build_matching_context(preference, filters)
    user_id = callback.from_user.id
    session = start_session(user_id)

    tracker = SearchProgressTracker(
        total_sites=len(valid_urls) * max(1, len(filters.region_values)),
        max_jobs_target=MAX_JOBS_PER_SITE * len(valid_urls) * max(1, len(filters.region_values)),
        region=filters.region_label,
        filters_line=filters.summary_line(),
        max_steps=MAX_JOBS_PER_SITE * 8 * max(1, len(filters.region_values)),
    )
    tracker.status_message = "Ссылки проверены"

    progress_stop = asyncio.Event()

    async def _ticker() -> None:
        while not progress_stop.is_set():
            try:
                await safe_edit_text(
                    progress_msg, tracker.to_html(),
                    parse_mode="HTML", reply_markup=search_cancel_kb(),
                )
            except Exception:
                pass
            try:
                await asyncio.wait_for(progress_stop.wait(), timeout=2.5)
                break
            except asyncio.TimeoutError:
                continue

    ticker_task = asyncio.create_task(_ticker())

    async def on_progress(node: str, st: dict) -> None:
        live = get_session(user_id)
        if live and live.is_cancelled:
            tracker.cancelled = True

        site_name = st.get("_site_name", tracker.current_site_name)
        if st.get("_total_sites"):
            tracker.total_sites = st["_total_sites"]
        if site_name:
            tracker.set_site(st.get("_site_index", tracker.current_site_index), site_name)
        tracker.update_from_node(node, st, site_name=site_name)
        if live:
            tracker.jobs_found = len(live.partial_jobs)

    try:
        result = await run_multi_site_search(
            urls=valid_urls,
            user_job_preference=preference,
            max_jobs_per_site=MAX_JOBS_PER_SITE,
            resume_profile=profile,
            input_mode=data.get("input_mode", "preference"),
            on_progress=on_progress,
            filters=filters,
            cancel_event=session.cancel_event,
            user_id=user_id,
        )
    except Exception as error:
        logger.error("Ошибка поиска: %s", error, exc_info=True)
        progress_stop.set()
        ticker_task.cancel()
        end_session(user_id)
        await safe_edit_text(
            progress_msg,
            f"❌ Поиск прервался: {h(str(error)[:300])}",
            reply_markup=after_search_kb(),
            parse_mode="HTML",
        )
        await state.set_state(SearchStates.setup)
        return
    finally:
        progress_stop.set()
        ticker_task.cancel()
        end_session(user_id)

    await _deliver_results(
        bot, callback.message.chat.id, progress_msg, result, filters,
        callback.from_user.id,
    )
    await state.set_state(SearchStates.setup)


def _results_header(result, filters: SearchFilters) -> str:
    count = len(result.jobs)
    report: FilterReport = result.report
    collected = result.collected or report.total
    dropped_count = len(getattr(result, "dropped", None) or [])

    if result.cancelled:
        title = (
            f"⏹ <b>Поиск остановлен</b> — показана {_ru_jobs(count)}"
            if count
            else "⏹ <b>Поиск остановлен</b> — подходящих вакансий пока нет"
        )
    elif count:
        title = f"✅ <b>Найдено {_ru_jobs(count)}</b> в {h(filters.region_label)}"
    else:
        title = f"😔 <b>В {h(filters.region_label)} ничего не подошло</b>"

    lines = [title]

    if collected:
        lines += ["", _ru_shown_line(count, collected)]

    filter_line = filters.summary_line()
    if filter_line:
        lines.append(f"<i>{h(filter_line)}</i>")

    reason_lines = report.reason_lines()
    if reason_lines:
        lines += ["", "<b>Не прошли фильтры:</b>"]
        lines.extend(f"• {h(item)}" for item in reason_lines)
        if dropped_count:
            lines += [
                "",
                "Причины по каждой вакансии — кнопка «Показать отброшенные».",
            ]

    if not count:
        lines += [
            "",
            "Попробуйте расширить регион, ослабить фильтры или добавить сайты.",
        ]

    return "\n".join(lines)


async def _deliver_results(
    bot: Bot,
    chat_id: int,
    progress_msg: Message,
    result,
    filters: SearchFilters,
    user_id: int,
) -> None:
    dropped = list(getattr(result, "dropped", None) or [])
    save_dropped_jobs(user_id, dropped)

    await safe_edit_text(
        progress_msg, _results_header(result, filters), parse_mode="HTML",
    )

    jobs: List[JobInfo] = result.jobs
    for index, job in enumerate(jobs[:MAX_CARDS], 1):
        try:
            await bot.send_message(
                chat_id,
                format_job_card(job, index),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as error:
            logger.warning("Не удалось отправить карточку %d: %s", index, error)
        await asyncio.sleep(0.3)

    tail = (
        f"… и ещё {len(jobs) - MAX_CARDS} вакансий осталось за кадром.\n\n"
        if len(jobs) > MAX_CARDS
        else ""
    )
    await bot.send_message(
        chat_id,
        f"{tail}Что дальше?",
        reply_markup=after_search_kb(len(dropped)),
    )


@router.message(SearchStates.searching)
async def busy_searching(message: Message) -> None:
    await message.answer(
        "Идёт поиск — результаты появятся в сообщении с прогрессом.\n"
        "Чтобы прервать, нажмите «⏹ Остановить и показать найденное».",
    )


@router.message(SearchStates.setup, F.text)
async def setup_free_text(message: Message, state: FSMContext, bot: Bot) -> None:
    """На экране настройки ссылки добавляются как сайты, текст — как новый запрос."""
    found = extract_urls_from_text(message.text)
    if found:
        data = await state.get_data()
        urls: List[str] = list(data.get("urls", []))
        urls.extend(u for u in found if u not in urls)
        await state.update_data(urls=urls)
    else:
        text = message.text.strip()
        if len(text) < 3:
            await message.answer("Напишите должность или отправьте ссылку на job-сайт.")
            return
        await state.update_data(user_job_preference=text)

    await _return_to_setup(message, state, bot)
