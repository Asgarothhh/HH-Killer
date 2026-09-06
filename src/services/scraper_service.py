"""Оркестрация поиска вакансий по нескольким сайтам."""

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional

from src.bot.search_session import add_partial_job
from src.graph import stream_job_scraper
from src.models.models import JobExtraction, JobInfo, ResumeProfile, drop_placeholder
from src.nodes.job_matcher import score_jobs_with_summary
from src.services.apify_service import (
    apify_status_message,
    can_run_apify,
    is_apify_available,
    reset_session_runs,
    search_via_apify,
)
from src.services.job_filters import FilterReport, apply_search_filters
from src.services.link_validator import BatchValidationResult, validate_user_links
from src.services.search_query import (
    SearchFilters,
    build_matching_context,
    build_role_queries,
)
from src.utils.logger import get_logger
from src.utils.telegram_html import h, h_attr
from src.utils.site_patterns import (
    ScrapingBackend,
    build_search_urls_for_site,
    get_scraping_backend,
    get_site_config,
    should_try_apify,
)

logger = get_logger(__name__)

ProgressCallback = Callable[[str, dict], Awaitable[None]]

# Минимальный score для показа пользователю (0–100)
MIN_MATCH_SCORE = 50

# Максимальная длина описания в карточке Telegram
DESCRIPTION_LIMIT = 600


@dataclass
class SearchRunResult:
    jobs: List[JobInfo]
    cancelled: bool = False
    collected: int = 0
    report: FilterReport = field(default_factory=FilterReport)
    dropped_by_score: int = 0


def _is_cancelled(cancel_event: asyncio.Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


async def score_jobs(
    jobs: List[JobInfo],
    preference: str,
    profile: ResumeProfile | None = None,
    filters: SearchFilters | None = None,
) -> List[JobInfo]:
    return await score_jobs_with_summary(jobs, preference, profile, filters)


def filter_relevant_jobs(
    jobs: List[JobInfo],
    min_score: float = MIN_MATCH_SCORE,
) -> List[JobInfo]:
    """Оставляет только вакансии с достаточной релевантностью."""
    relevant = [j for j in jobs if (j.match_score or 0) >= min_score]
    dropped = len(jobs) - len(relevant)
    if dropped:
        logger.info("Скрыто %d нерелевантных вакансий (score < %d)", dropped, min_score)
    return relevant


def _field_line(emoji: str, label: str, value: Optional[str]) -> Optional[str]:
    text = drop_placeholder(value)
    if not text:
        return None
    return f"{emoji} <b>{label}:</b> {h(text)}"


def format_job_card(job: JobInfo, index: int) -> str:
    """
    Карточка вакансии: строго поля JobExtraction + сводка ИИ-агента.

    Внутренние метрики (score, совпадения, пробелы) пользователю не показываются.
    """
    data: JobExtraction = job.to_extraction()

    lines = [f"<b>{index}. {h(data.job_title)}</b>"]

    for line in (
        _field_line("🏢", "Компания", data.company_name),
        _field_line("📍", "Локация", data.location),
        _field_line("💼", "Занятость", data.employment_type),
        _field_line("💰", "Зарплата", data.salary_range),
        _field_line("📅", "Опубликовано", data.posted_date),
    ):
        if line:
            lines.append(line)

    description = (data.job_description or "").strip()
    if description:
        clipped = description[:DESCRIPTION_LIMIT]
        suffix = "…" if len(description) > DESCRIPTION_LIMIT else ""
        lines.append(f"📝 <b>Описание:</b> {h(clipped)}{suffix}")

    if job.ai_summary:
        lines.append(f"🤖 <b>Сводка ИИ:</b> {h(job.ai_summary)}")

    apply_method = (data.application_method or "").strip()
    if apply_method.startswith("http"):
        lines.append(f'🔗 <a href="{h_attr(apply_method)}">Откликнуться</a>')
    elif apply_method:
        lines.append(f"🔗 <b>Отклик:</b> {h(apply_method)}")

    return "\n".join(lines)


async def _search_with_playwright(
    start_url: str,
    user_job_preference: str,
    max_jobs: int,
    resume_profile: ResumeProfile | None,
    input_mode: str,
    on_progress: ProgressCallback | None,
    cancel_event: asyncio.Event | None = None,
) -> List[JobInfo]:
    if _is_cancelled(cancel_event):
        return []
    return await stream_job_scraper(
        website=start_url,
        user_job_preference=user_job_preference,
        max_jobs=max_jobs,
        resume_profile=resume_profile,
        input_mode=input_mode,
        on_progress=on_progress,
        cancel_event=cancel_event,
    )


async def _search_single_site(
    url: str,
    user_job_preference: str,
    role_queries: List[str],
    max_jobs: int,
    resume_profile: ResumeProfile | None,
    input_mode: str,
    on_progress: ProgressCallback | None,
    filters: SearchFilters | None = None,
    location: str = "Polska",
    cancel_event: asyncio.Event | None = None,
    user_id: int | None = None,
) -> List[JobInfo]:
    """Выбирает Apify или Playwright и перебирает варианты keyword-запроса."""
    if _is_cancelled(cancel_event):
        return []

    backend = get_scraping_backend(url)
    apify_ok = is_apify_available()
    config = get_site_config(url)
    site_name = config.name if config else url
    jobs: List[JobInfo] = []

    use_apify_first = (
        backend in {ScrapingBackend.APIFY, ScrapingBackend.AUTO}
        and should_try_apify(url, apify_ok)
        and can_run_apify()[0]
    )

    for query in role_queries or [""]:
        if jobs or _is_cancelled(cancel_event):
            break

        if use_apify_first:
            if on_progress:
                await on_progress("apify", {
                    "status_message": f"Apify: «{query}» — {site_name}",
                    "website": url,
                })
            jobs = await search_via_apify(url, query, max_jobs, location=location)
            if jobs:
                break

        search_urls = build_search_urls_for_site(
            url, query, location=location, filters=filters,
        )
        for start_url in search_urls:
            if _is_cancelled(cancel_event):
                break
            if on_progress:
                await on_progress("playwright", {
                    "status_message": f"Поиск «{query}» в {location}",
                    "website": start_url,
                })
            jobs.extend(await _search_with_playwright(
                start_url, user_job_preference, max_jobs,
                resume_profile, input_mode, on_progress, cancel_event,
            ))
            if len(jobs) >= max_jobs:
                break

    if user_id:
        for job in jobs:
            add_partial_job(user_id, job)

    return jobs[:max_jobs]


async def run_multi_site_search(
    urls: List[str],
    user_job_preference: str,
    max_jobs_per_site: int = 5,
    resume_profile: ResumeProfile | None = None,
    input_mode: str = "preference",
    on_progress: ProgressCallback | None = None,
    filters: SearchFilters | None = None,
    cancel_event: asyncio.Event | None = None,
    user_id: int | None = None,
    role_query: str | None = None,
) -> SearchRunResult:
    """Ищет вакансии на нескольких сайтах строго в заданном регионе."""
    reset_session_runs()
    all_jobs: List[JobInfo] = []
    seen_keys: set[str] = set()
    cancelled = False

    location = filters.location_for_url() if filters else "Polska"
    role_queries = (
        [role_query] if role_query
        else build_role_queries(user_job_preference, resume_profile)
    )
    matching_context = build_matching_context(user_job_preference, filters)

    logger.info(apify_status_message())
    logger.info("Регион поиска: %s · запросы: %s", location, role_queries)

    for index, url in enumerate(urls):
        if _is_cancelled(cancel_event):
            cancelled = True
            break

        config = get_site_config(url)
        site_name = config.name if config else url[:40]

        if on_progress:
            await on_progress("start_site", {
                "website": url,
                "status_message": f"Сайт {index + 1}/{len(urls)}: {site_name}",
                "_site_index": index,
                "_site_name": site_name,
                "_total_sites": len(urls),
            })

        jobs = await _search_single_site(
            url=url,
            user_job_preference=matching_context,
            role_queries=role_queries,
            max_jobs=max_jobs_per_site,
            resume_profile=resume_profile,
            input_mode=input_mode,
            on_progress=on_progress,
            filters=filters,
            location=location,
            cancel_event=cancel_event,
            user_id=user_id,
        )

        for job in jobs:
            key = job.source_url or f"{job.title}:{job.company}"
            if key not in seen_keys:
                seen_keys.add(key)
                all_jobs.append(job)

    if _is_cancelled(cancel_event):
        cancelled = True

    if on_progress:
        await on_progress("filtering", {
            "status_message": f"Проверяю регион «{location}» и фильтры…",
        })

    outcome = apply_search_filters(all_jobs, filters, resume_profile)

    if on_progress and outcome.jobs:
        await on_progress("scoring", {
            "status_message": f"Оценка релевантности: {len(outcome.jobs)} вакансий",
        })

    scored = await score_jobs_with_summary(
        outcome.jobs, matching_context, resume_profile, filters,
    )
    relevant = filter_relevant_jobs(scored)

    return SearchRunResult(
        jobs=relevant[: max_jobs_per_site * max(len(urls), 1)],
        cancelled=cancelled,
        collected=len(all_jobs),
        report=outcome.report,
        dropped_by_score=len(scored) - len(relevant),
    )


async def validate_and_prepare_urls(urls: List[str]) -> BatchValidationResult:
    return await validate_user_links(urls, check_reachability=True)
