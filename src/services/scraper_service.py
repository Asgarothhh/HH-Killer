"""Оркестрация поиска вакансий по нескольким сайтам."""

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional

from src.bot.search_session import add_partial_job
from src.graph import stream_job_scraper
from src.models.models import JobInfo, ResumeProfile
from src.nodes.job_matcher import _score_job
from src.services.apify_service import (
    apify_status_message,
    can_run_apify,
    is_apify_available,
    reset_session_runs,
    search_via_apify,
)
from src.services.link_validator import BatchValidationResult, validate_user_links
from src.services.job_filters import apply_search_filters
from src.services.search_query import SearchFilters, build_matching_context, build_role_query
from src.utils.logger import get_logger
from src.utils.telegram_html import h, h_attr
from src.utils.site_patterns import (
    ScrapingBackend,
    build_search_urls_for_site,
    detect_site_type,
    get_scraping_backend,
    get_site_config,
    should_try_apify,
)

logger = get_logger(__name__)

ProgressCallback = Callable[[str, dict], Awaitable[None]]


@dataclass
class SearchRunResult:
    jobs: List[JobInfo]
    cancelled: bool = False


def _is_cancelled(cancel_event: asyncio.Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


# Минимальный score для показа пользователю (0–100)
MIN_MATCH_SCORE = 50


async def score_jobs(
    jobs: List[JobInfo],
    preference: str,
    profile: ResumeProfile | None = None,
    filters: SearchFilters | None = None,
) -> List[JobInfo]:
    if not jobs:
        return []
    scored = await asyncio.gather(
        *[_score_job(job, preference, profile, filters) for job in jobs]
    )
    return sorted(scored, key=lambda j: j.match_score or 0, reverse=True)


def filter_relevant_jobs(
    jobs: List[JobInfo],
    min_score: float = MIN_MATCH_SCORE,
) -> List[JobInfo]:
    """Оставляет только вакансии с достаточной релевантностью."""
    relevant = [j for j in jobs if (j.match_score or 0) >= min_score]
    dropped = len(jobs) - len(relevant)
    if dropped:
        logger.info(
            "Скрыто %d нерелевантных вакансий (score < %d)",
            dropped,
            min_score,
        )
    return relevant


def _field_line(emoji: str, label: str, value: Optional[str]) -> Optional[str]:
    if not value or not str(value).strip():
        return None
    return f"{emoji} <b>{label}:</b> {h(str(value).strip())}"


def format_job_card(job: JobInfo, index: int) -> str:
    """Форматирует карточку строго по полям JobInfo / JobExtraction."""
    url = job.source_url or job.application_info
    config = get_site_config(url) if url else None

    header = f"<b>{index}. {h(job.title)}</b>"
    if job.match_score is not None:
        header += f" 🎯 {job.match_score:.0f}%"
    if config:
        header += f" [{h(config.name)}]"

    lines = [header]

    for line in (
        _field_line("🏢", "Компания", job.company),
        _field_line("📍", "Локация", job.job_location),
        _field_line("💼", "Занятость", job.employment_type),
        _field_line("💰", "Зарплата", job.salary_range),
    ):
        if line:
            lines.append(line)

    if job.posted_date:
        lines.append(f"📅 <b>Дата:</b> {job.posted_date.strftime('%d.%m.%Y')}")

    if job.description and job.description.strip():
        desc = job.description.strip()
        lines.append(
            f"📝 <b>Описание:</b> {h(desc[:450])}{'…' if len(desc) > 450 else ''}"
        )

    if job.key_matches:
        matches = ", ".join(job.key_matches[:8])
        lines.append(f"✅ <b>Совпадения:</b> {h(matches)}")

    if job.match_gaps:
        gaps = ", ".join(job.match_gaps[:5])
        lines.append(f"⚠️ <b>Несоответствия:</b> {h(gaps)}")

    if job.match_reason:
        lines.append(f"💡 <b>Релевантность:</b> {h(job.match_reason)}")

    apply_target = url if url and url.startswith("http") else None
    if apply_target:
        lines.append(f'🔗 <a href="{h_attr(apply_target)}">Открыть вакансию</a>')
    elif job.application_info and job.application_info.strip() not in {"—", "-"}:
        lines.append(f"📨 <b>Отклик:</b> {h(job.application_info.strip())}")

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
    role_query: str,
    max_jobs: int,
    resume_profile: ResumeProfile | None,
    input_mode: str,
    on_progress: ProgressCallback | None,
    location: str = "Polska",
    cancel_event: asyncio.Event | None = None,
    user_id: int | None = None,
) -> List[JobInfo]:
    """Выбирает Apify или Playwright в зависимости от сайта и квоты."""
    if _is_cancelled(cancel_event):
        return []

    backend = get_scraping_backend(url)
    apify_ok = is_apify_available()
    jobs: List[JobInfo] = []

    use_apify_first = (
        backend in {ScrapingBackend.APIFY, ScrapingBackend.AUTO}
        and should_try_apify(url, apify_ok)
        and can_run_apify()[0]
    )

    if use_apify_first and not _is_cancelled(cancel_event):
        if on_progress:
            config = get_site_config(url)
            name = config.name if config else url
            await on_progress("apify", {
                "status_message": f"Apify: поиск на {name}",
                "website": url,
            })
        jobs = await search_via_apify(url, role_query, max_jobs, location=location)

    if not jobs and not _is_cancelled(cancel_event):
        search_urls = build_search_urls_for_site(url, role_query, location=location)
        for start_url in search_urls:
            if _is_cancelled(cancel_event):
                break
            if on_progress:
                await on_progress("playwright", {
                    "status_message": f"Stealth: {start_url[:50]}…",
                    "website": start_url,
                })
            pw_jobs = await _search_with_playwright(
                start_url, user_job_preference, max_jobs,
                resume_profile, input_mode, on_progress, cancel_event,
            )
            jobs.extend(pw_jobs)
            if len(jobs) >= max_jobs:
                break

        if not jobs and apify_ok and can_run_apify()[0] and not _is_cancelled(cancel_event):
            from src.services.apify_service import apify_fallback_scrape
            from src.utils.bot_detection import is_content_usable

            if on_progress:
                await on_progress("apify_fallback", {
                    "status_message": "Apify fallback (bot-защита)",
                    "website": url,
                })
            scraped = await apify_fallback_scrape(url, role_query, location=location)
            if scraped and is_content_usable(scraped.get("text", "")):
                logger.info("Apify fallback получил контент для %s", url[:50])

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
    """Запускает поиск на нескольких сайтах и объединяет результаты."""
    reset_session_runs()
    all_jobs: List[JobInfo] = []
    seen_urls: set[str] = set()
    location = (filters.location_for_url() if filters else "Polska")
    search_role = role_query or build_role_query(user_job_preference)
    matching_context = build_matching_context(search_role, filters) if filters else user_job_preference
    cancelled = False

    logger.info(apify_status_message())

    for i, url in enumerate(urls):
        if _is_cancelled(cancel_event):
            cancelled = True
            break

        config = get_site_config(url)
        site_name = config.name if config else url[:40]

        if on_progress:
            await on_progress("start_site", {
                "website": url,
                "status_message": f"Сайт {i + 1}/{len(urls)}: {site_name}",
                "_site_index": i,
                "_site_name": site_name,
                "_total_sites": len(urls),
            })

        jobs = await _search_single_site(
            url=url,
            user_job_preference=matching_context,
            role_query=search_role,
            max_jobs=max_jobs_per_site,
            resume_profile=resume_profile,
            input_mode=input_mode,
            on_progress=on_progress,
            location=location,
            cancel_event=cancel_event,
            user_id=user_id,
        )

        for job in jobs:
            key = job.source_url or f"{job.title}:{job.company}"
            if key not in seen_urls:
                seen_urls.add(key)
                all_jobs.append(job)

    if _is_cancelled(cancel_event):
        cancelled = True

    if on_progress and not cancelled:
        await on_progress("scoring", {"status_message": "Фильтрация и оценка релевантности…", "jobs_found": all_jobs})

    filtered = apply_search_filters(all_jobs, filters, resume_profile) if all_jobs else []
    scored = await score_jobs(filtered, matching_context, resume_profile, filters) if filtered else []
    relevant = filter_relevant_jobs(scored)
    return SearchRunResult(
        jobs=relevant[: max_jobs_per_site * len(urls)],
        cancelled=cancelled,
    )


async def validate_and_prepare_urls(urls: List[str]) -> BatchValidationResult:
    return await validate_user_links(urls, check_reachability=True)
