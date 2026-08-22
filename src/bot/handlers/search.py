import asyncio
from typing import List

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot.keyboards import (
    after_search_kb,
    cancel_kb,
    filters_kb,
    mode_label,
    resolve_preset_url,
    search_cancel_kb,
    urls_selection_kb,
    _url_label,
)
from src.bot.progress import SearchProgressTracker
from src.bot.search_session import cancel_session, end_session, get_session, start_session
from src.bot.states import InputMode, SearchStates
from src.models.models import JobInfo, ResumeProfile
from src.services.link_validator import extract_urls_from_text, validate_user_links
from src.services.resume_service import analyze_resume_file, format_llm_error, profile_to_search_query
from src.services.scraper_service import format_job_card, run_multi_site_search
from src.services.search_query import (
    SearchFilters,
    build_matching_context,
    build_role_query,
    parse_experience,
    parse_salary,
)
from src.utils.logger import get_logger
from src.utils.telegram_html import download_document_bytes, h, safe_callback_answer, safe_callback_edit, safe_edit_text

router = Router()
logger = get_logger(__name__)

MAX_JOBS = 5


async def _get_data(state: FSMContext) -> dict:
    return await state.get_data()


def _filters_from_data(data: dict) -> SearchFilters:
    return SearchFilters(
        city=data.get("search_city"),
        salary_min=data.get("search_salary_min"),
        salary_max=data.get("search_salary_max"),
        currency=data.get("search_currency", "PLN"),
        experience_years=data.get("search_experience_years"),
        experience_job_min=data.get("search_experience_job_min"),
        experience_job_max=data.get("search_experience_job_max"),
    )


def _filters_summary(filters: SearchFilters) -> str:
    parts = []
    if filters.city:
        parts.append(f"📍 {h(filters.city)}")
    if filters.experience_label:
        parts.append(f"💼 {h(filters.experience_label)}")
    if filters.salary_label:
        parts.append(f"💰 {h(filters.salary_label)}")
    return " · ".join(parts) if parts else "не заданы (опционально)"


async def _deliver_search_results(
    bot: Bot,
    chat_id: int,
    progress_msg: Message,
    jobs: List[JobInfo],
    *,
    cancelled: bool = False,
) -> None:
    if not jobs:
        text = (
            "⏹ <b>Поиск остановлен</b>\n\nРелевантные вакансии не успели найтись."
            if cancelled
            else "😔 Релевантных вакансий не найдено (ни одна не набрала достаточный score).\n"
            "Попробуйте другие сайты, измените запрос или фильтры."
        )
        await safe_edit_text(progress_msg, text, reply_markup=after_search_kb(), parse_mode="HTML")
        return

    header = (
        f"⏹ <b>Поиск остановлен</b> — найдено <b>{len(jobs)}</b> вакансий"
        if cancelled
        else f"✅ <b>Найдено {len(jobs)} релевантных вакансий</b>"
    )
    await safe_edit_text(progress_msg, header, parse_mode="HTML")

    for i, job in enumerate(jobs[:10], 1):
        try:
            await bot.send_message(
                chat_id,
                format_job_card(job, i),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as send_error:
            logger.warning("Не удалось отправить карточку %d: %s", i, send_error)
        await asyncio.sleep(0.3)

    if len(jobs) > 10:
        await bot.send_message(chat_id, f"… и ещё {len(jobs) - 10} вакансий.")

    await bot.send_message(chat_id, "Готово! Хотите новый поиск?", reply_markup=after_search_kb())


@router.callback_query(F.data.startswith("mode:"))
async def cb_choose_mode(callback: CallbackQuery, state: FSMContext) -> None:
    mode_str = callback.data.split(":", 1)[1]
    mode = InputMode(mode_str)
    await state.update_data(input_mode=mode.value, urls=[])

    if mode == InputMode.PREFERENCE:
        await state.set_state(SearchStates.waiting_preference)
        text = (
            "<b>📝 Опишите желаемую вакансию</b>\n\n"
            "Например: <i>Python-разработчик, удалёнка, ML/Data Science</i>"
        )
    else:
        await state.set_state(SearchStates.waiting_resume)
        text = (
            "<b>📄 Загрузите резюме</b>\n\n"
            "Поддерживаются форматы: PDF, DOCX, TXT\n"
            "AI проанализирует файл и сформирует поисковый запрос."
        )

    await safe_callback_edit(callback, text, parse_mode="HTML")
    await callback.message.answer("Или нажмите ❌ Отмена", reply_markup=cancel_kb())
    await safe_callback_answer(callback)


@router.message(SearchStates.waiting_preference, F.text == "❌ Отмена")
@router.message(SearchStates.waiting_resume, F.text == "❌ Отмена")
@router.message(SearchStates.waiting_filters, F.text == "❌ Отмена")
@router.message(SearchStates.waiting_city, F.text == "❌ Отмена")
@router.message(SearchStates.waiting_salary, F.text == "❌ Отмена")
@router.message(SearchStates.waiting_experience, F.text == "❌ Отмена")
@router.message(SearchStates.waiting_urls, F.text == "❌ Отмена")
async def cancel_flow(message: Message, state: FSMContext) -> None:
    from src.bot.handlers.start import WELCOME
    from src.bot.keyboards import main_menu_kb

    await state.clear()
    await message.answer(WELCOME, reply_markup=main_menu_kb(), parse_mode="HTML")


@router.message(SearchStates.waiting_preference, F.text)
async def receive_preference(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    if len(text) < 5:
        await message.answer("Опишите вакансию подробнее (минимум 5 символов).")
        return

    await state.update_data(
        user_job_preference=text,
        resume_profile=None,
    )
    await _ask_for_filters(message, state)


@router.message(SearchStates.waiting_resume, F.document)
async def receive_resume(message: Message, state: FSMContext, bot: Bot) -> None:
    doc = message.document
    allowed = {".pdf", ".docx", ".doc", ".txt", ".md"}
    ext = "." + doc.file_name.rsplit(".", 1)[-1].lower() if doc.file_name and "." in doc.file_name else ""

    if ext not in allowed:
        await message.answer(f"Формат не поддерживается. Отправьте: {', '.join(sorted(allowed))}")
        return

    status = await message.answer("⏳ Анализирую резюме…")

    try:
        raw = await download_document_bytes(bot, doc)

        profile: ResumeProfile = await analyze_resume_file(doc.file_name, raw)
        query = profile_to_search_query(profile)

        resume_updates = {
            "user_job_preference": query,
            "resume_profile": profile.model_dump(),
        }
        if profile.experience_years is not None:
            resume_updates["search_experience_years"] = profile.experience_years
        data = await _get_data(state)
        if profile.location and not data.get("search_city"):
            resume_updates["search_city"] = profile.location
        await state.update_data(**resume_updates)

        summary = (
            f"<b>✅ Резюме проанализировано</b>\n\n"
            f"👤 {h(profile.full_name or 'Кандидат')}\n"
            f"🎯 {h(profile.desired_role)}"
            f"{f' ({h(profile.seniority_level)})' if profile.seniority_level else ''}\n"
            f"💼 Опыт: {h(str(profile.experience_years) + ' лет' if profile.experience_years else '—')}\n"
            f"🛠 Навыки: {h(', '.join(profile.skills[:8]) or '—')}\n"
            f"📍 {h(profile.location or '—')}\n\n"
            f"<i>Поисковый запрос: {h(query[:200])}</i>"
        )
        await safe_edit_text(status, summary, parse_mode="HTML")
        await _ask_for_filters(message, state)

    except Exception as error:
        logger.error("Ошибка анализа резюме: %s", error, exc_info=True)
        await safe_edit_text(status, format_llm_error(error), parse_mode="HTML")


@router.message(SearchStates.waiting_resume)
async def resume_expected_document(message: Message) -> None:
    await message.answer("Отправьте файл резюме (PDF, DOCX или TXT).")


async def _ask_for_filters(message: Message, state: FSMContext) -> None:
    await state.set_state(SearchStates.waiting_filters)
    data = await _get_data(state)
    filters = _filters_from_data(data)
    await message.answer(
        "<b>🎛 Фильтры поиска</b> (необязательно)\n\n"
        f"Текущие: {_filters_summary(filters)}\n\n"
        "Укажите город, опыт и/или зарплатный диапазон — или пропустите.",
        reply_markup=filters_kb(),
        parse_mode="HTML",
    )


async def _ask_for_urls(message: Message, state: FSMContext) -> None:
    await state.set_state(SearchStates.waiting_urls)
    await message.answer(
        "<b>🔗 Укажите сайты для поиска</b>\n\n"
        "Выберите пресеты (PL + международные) или отправьте свои URL.\n"
        "Поддерживается <b>любой</b> job-сайт — система адаптируется автоматически.",
        reply_markup=urls_selection_kb(),
        parse_mode="HTML",
    )


async def _refresh_urls_view(callback: CallbackQuery, state: FSMContext) -> None:
    data = await _get_data(state)
    urls: List[str] = list(data.get("urls", []))
    await safe_callback_edit(
        callback,
        _urls_message({**data, "urls": urls}, urls),
        reply_markup=urls_selection_kb(urls),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("sp:"))
async def cb_add_preset(callback: CallbackQuery, state: FSMContext) -> None:
    preset_key = callback.data.split(":", 1)[1]
    url = resolve_preset_url(preset_key)
    if not url:
        await safe_callback_answer(callback, "Неизвестный пресет", show_alert=True)
        return

    data = await _get_data(state)
    urls: List[str] = list(data.get("urls", []))
    if url not in urls:
        urls.append(url)
    await state.update_data(urls=urls)

    await _refresh_urls_view(callback, state)
    label = preset_key.replace("_", " ")
    await safe_callback_answer(callback, f"✅ {label}")


@router.callback_query(F.data.startswith("urlrm:"))
async def cb_remove_url(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        idx = int(callback.data.split(":", 1)[1])
    except ValueError:
        await safe_callback_answer(callback, "Ошибка", show_alert=True)
        return

    data = await _get_data(state)
    urls: List[str] = list(data.get("urls", []))
    if not (0 <= idx < len(urls)):
        await safe_callback_answer(callback, "Ссылка уже удалена", show_alert=True)
        return

    removed = urls.pop(idx)
    await state.update_data(urls=urls)
    await _refresh_urls_view(callback, state)
    await safe_callback_answer(callback, f"🗑 Удалено: {_url_label(removed)}")


@router.callback_query(F.data == "urls:clear")
async def cb_clear_urls(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(urls=[])
    await _refresh_urls_view(callback, state)
    await safe_callback_answer(callback, "Список очищен")


@router.callback_query(F.data.startswith("site:"))
async def cb_add_site_legacy(callback: CallbackQuery, state: FSMContext) -> None:
    """Поддержка прямых URL в callback (legacy)."""
    url = callback.data.split(":", 1)[1]
    data = await _get_data(state)
    urls: List[str] = list(data.get("urls", []))
    if url not in urls:
        urls.append(url)
    await state.update_data(urls=urls)
    await _refresh_urls_view(callback, state)
    await safe_callback_answer(callback, f"Добавлено: {url[:40]}")


def _urls_message(data: dict, urls: List[str]) -> str:
    mode_raw = data.get("input_mode", "preference")
    try:
        mode = InputMode(mode_raw)
    except ValueError:
        mode = InputMode.PREFERENCE
    pref = h(str(data.get("user_job_preference", ""))[:120])
    filters = _filters_from_data(data)
    filter_line = _filters_summary(filters)
    if urls:
        urls_text = "\n".join(f"{i + 1}. {h(u)}" for i, u in enumerate(urls))
    else:
        urls_text = "— пока не добавлены —"
    return (
        f"<b>🔗 Сайты для поиска</b> ({len(urls)})\n\n"
        f"Режим: {mode_label(mode)}\n"
        f"Запрос: <i>{pref}</i>\n"
        f"Фильтры: {filter_line}\n\n"
        f"{urls_text}\n\n"
        f"<i>+ добавить · 🗑 убрать сайт</i>"
    )


@router.callback_query(F.data == "filters:edit")
async def cb_filters_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SearchStates.waiting_filters)
    data = await _get_data(state)
    filters = _filters_from_data(data)
    await safe_callback_edit(
        callback,
        "<b>🎛 Фильтры поиска</b> (необязательно)\n\n"
        f"Текущие: {_filters_summary(filters)}",
        reply_markup=filters_kb(),
        parse_mode="HTML",
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "filters:skip")
async def cb_filters_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SearchStates.waiting_urls)
    data = await _get_data(state)
    urls: List[str] = list(data.get("urls", []))
    await safe_callback_edit(
        callback,
        _urls_message({**data, "urls": urls}, urls),
        reply_markup=urls_selection_kb(urls),
        parse_mode="HTML",
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "filters:city")
async def cb_filters_city(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SearchStates.waiting_city)
    await safe_callback_edit(
        callback,
        "<b>📍 Укажите город</b>\n\n"
        "Например: <i>Warszawa</i>, <i>Kraków</i>, <i>Remote</i>",
        parse_mode="HTML",
    )
    await callback.message.answer("Или ❌ Отмена", reply_markup=cancel_kb())
    await safe_callback_answer(callback)


@router.callback_query(F.data == "filters:experience")
async def cb_filters_experience(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SearchStates.waiting_experience)
    data = await _get_data(state)
    exp = data.get("search_experience_years")
    hint = f"\n\nИз резюме: <b>{h(str(exp))} лет</b>" if exp else ""
    await safe_callback_edit(
        callback,
        "<b>💼 Укажите опыт работы</b>\n\n"
        "Примеры:\n"
        "• <i>6</i> или <i>6+</i> — ваш стаж\n"
        "• <i>3-5</i> — ищу вакансии уровня 3–5 лет\n"
        "• <i>мой опыт 6, вакансии 3-5</i>"
        f"{hint}",
        parse_mode="HTML",
    )
    await callback.message.answer("Или ❌ Отмена", reply_markup=cancel_kb())
    await safe_callback_answer(callback)


@router.message(SearchStates.waiting_experience, F.text)
async def receive_experience(message: Message, state: FSMContext) -> None:
    if message.text.strip() == "❌ Отмена":
        return
    user_years, job_min, job_max = parse_experience(message.text)
    if user_years is None and job_min is None and job_max is None:
        await message.answer("Не распознан опыт. Пример: 6 или 3-5 лет")
        return
    updates = {}
    if user_years is not None:
        updates["search_experience_years"] = user_years
    if job_min is not None:
        updates["search_experience_job_min"] = job_min
    if job_max is not None:
        updates["search_experience_job_max"] = job_max
    await state.update_data(**updates)
    await _ask_for_filters(message, state)


@router.callback_query(F.data == "filters:salary")
async def cb_filters_salary(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SearchStates.waiting_salary)
    await safe_callback_edit(
        callback,
        "<b>💰 Укажите зарплатный диапазон</b>\n\n"
        "Примеры:\n"
        "• <i>8000-12000 PLN</i>\n"
        "• <i>от 15000</i>\n"
        "• <i>10k EUR</i>",
        parse_mode="HTML",
    )
    await callback.message.answer("Или ❌ Отмена", reply_markup=cancel_kb())
    await safe_callback_answer(callback)


@router.message(SearchStates.waiting_city, F.text)
async def receive_city(message: Message, state: FSMContext) -> None:
    if message.text.strip() == "❌ Отмена":
        return
    city = message.text.strip()
    if len(city) < 2:
        await message.answer("Введите название города (мин. 2 символа).")
        return
    await state.update_data(search_city=city)
    await _ask_for_filters(message, state)


@router.message(SearchStates.waiting_salary, F.text)
async def receive_salary(message: Message, state: FSMContext) -> None:
    if message.text.strip() == "❌ Отмена":
        return
    sal_min, sal_max, currency = parse_salary(message.text)
    if sal_min is None and sal_max is None:
        await message.answer("Не распознан диапазон. Пример: 8000-12000 PLN")
        return
    await state.update_data(
        search_salary_min=sal_min,
        search_salary_max=sal_max,
        search_currency=currency,
    )
    await _ask_for_filters(message, state)


@router.callback_query(F.data == "search:cancel")
@router.message(SearchStates.searching, F.text == "❌ Отмена")
async def cancel_active_search(event: CallbackQuery | Message, state: FSMContext) -> None:
    user_id = event.from_user.id
    if cancel_session(user_id):
        if isinstance(event, CallbackQuery):
            await safe_callback_answer(event, "⏹ Останавливаю…")
        else:
            await event.answer("⏹ Останавливаю поиск… сохраняю найденное.")
    else:
        if isinstance(event, CallbackQuery):
            await safe_callback_answer(event, "Нет активного поиска", show_alert=True)
        else:
            from src.bot.handlers.start import WELCOME
            from src.bot.keyboards import main_menu_kb
            await state.clear()
            await event.answer(WELCOME, reply_markup=main_menu_kb(), parse_mode="HTML")


@router.message(SearchStates.waiting_urls, F.text)
async def receive_urls(message: Message, state: FSMContext) -> None:
    if message.text.strip() == "❌ Отмена":
        return

    new_urls = extract_urls_from_text(message.text)
    if not new_urls:
        await message.answer("Не найдено ссылок. Отправьте URL вида https://…")
        return

    data = await _get_data(state)
    urls: List[str] = list(data.get("urls", []))
    for u in new_urls:
        if u not in urls:
            urls.append(u)
    await state.update_data(urls=urls)

    await message.answer(
        _urls_message({**data, "urls": urls}, urls),
        reply_markup=urls_selection_kb(urls),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "search:start")
async def cb_start_search(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await _get_data(state)
    urls: List[str] = data.get("urls", [])
    preference = data.get("user_job_preference", "")

    if not preference:
        await safe_callback_answer(callback, "Сначала опишите вакансию или загрузите резюме", show_alert=True)
        return
    if not urls:
        await safe_callback_answer(callback, "Добавьте хотя бы одну ссылку на сайт", show_alert=True)
        return
    if not callback.message:
        await safe_callback_answer(callback, "Сообщение недоступно", show_alert=True)
        return

    await safe_callback_answer(callback, "🔍 Запускаю поиск…")

    await state.set_state(SearchStates.searching)
    progress_msg = await safe_edit_text(
        callback.message, "🔍 Проверяю ссылки…", parse_mode="HTML",
    )

    validation = await validate_user_links(urls)
    if validation.invalid_links:
        errors = "\n".join(f"❌ {h(r.url)}: {h(r.message)}" for r in validation.invalid_links)
        await safe_edit_text(
            progress_msg,
            f"<b>Ошибки в ссылках:</b>\n{errors}\n\nИсправьте или удалите их кнопкой 🗑",
            parse_mode="HTML",
            reply_markup=urls_selection_kb(urls),
        )
        await state.set_state(SearchStates.waiting_urls)
        return

    valid_urls = [r.normalized_url for r in validation.valid_links]

    if validation.warnings:
        warned = ", ".join(r.site_name for r in validation.warnings)
        logger.info("Сайты с bot-защитой (поиск через stealth): %s", warned)

    resume_data = data.get("resume_profile")
    profile = None
    if resume_data:
        try:
            profile = ResumeProfile.model_validate(resume_data)
        except Exception as error:
            logger.warning("Не удалось восстановить профиль резюме: %s", error)

    input_mode = data.get("input_mode", "preference")
    filters = _filters_from_data(data)
    role_query = build_role_query(preference)
    matching_context = build_matching_context(role_query, filters)

    user_id = callback.from_user.id
    session = start_session(user_id)

    tracker = SearchProgressTracker(
        total_sites=len(valid_urls),
        max_jobs_target=MAX_JOBS * len(valid_urls),
        city=filters.city,
        salary_label=filters.salary_label,
        experience_label=filters.experience_label,
        max_steps=MAX_JOBS * 8,
    )
    tracker.phase = "validating"
    tracker.status_message = "Проверка ссылок завершена"

    progress_stop = asyncio.Event()

    async def _progress_ticker() -> None:
        while not progress_stop.is_set():
            try:
                await safe_edit_text(
                    progress_msg,
                    tracker.to_html(),
                    parse_mode="HTML",
                    reply_markup=search_cancel_kb(),
                )
            except Exception:
                pass
            try:
                await asyncio.wait_for(progress_stop.wait(), timeout=2.0)
                break
            except asyncio.TimeoutError:
                continue

    ticker_task = asyncio.create_task(_progress_ticker())

    async def on_progress(node: str, st: dict) -> None:
        if session.is_cancelled:
            tracker.cancelled = True

        site_name = st.get("_site_name", tracker.current_site_name)
        site_index = st.get("_site_index", tracker.current_site_index)
        if st.get("_total_sites"):
            tracker.total_sites = st["_total_sites"]
        if site_name:
            tracker.set_site(site_index, site_name)
        tracker.update_from_node(node, st, site_name=site_name)
        live_session = get_session(user_id)
        if live_session:
            tracker.jobs_found = len(live_session.partial_jobs)
        elif st.get("jobs_found"):
            tracker.jobs_found = max(tracker.jobs_found, len(st["jobs_found"]))

    try:
        result = await run_multi_site_search(
            urls=valid_urls,
            user_job_preference=matching_context,
            role_query=role_query,
            max_jobs_per_site=MAX_JOBS,
            resume_profile=profile,
            input_mode=input_mode,
            on_progress=on_progress,
            filters=filters,
            cancel_event=session.cancel_event,
            user_id=user_id,
        )
    except Exception as error:
        logger.error("Ошибка поиска: %s", error, exc_info=True)
        progress_stop.set()
        ticker_task.cancel()
        await safe_edit_text(
            progress_msg,
            f"❌ Ошибка поиска: {h(str(error))}",
            reply_markup=after_search_kb(),
            parse_mode="HTML",
        )
        end_session(user_id)
        await state.clear()
        return
    finally:
        progress_stop.set()
        ticker_task.cancel()
        end_session(user_id)

    tracker.phase = "cancelled" if result.cancelled else "done"
    tracker.jobs_found = len(result.jobs)
    tracker.percent()

    await safe_edit_text(
        progress_msg,
        tracker.to_html(),
        parse_mode="HTML",
        reply_markup=search_cancel_kb(),
    )

    await _deliver_search_results(
        bot,
        callback.message.chat.id,
        progress_msg,
        result.jobs,
        cancelled=result.cancelled,
    )
    await state.clear()
