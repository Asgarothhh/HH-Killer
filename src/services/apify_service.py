"""Интеграция с Apify для сложных job-сайтов (LinkedIn, Indeed).

Оптимизировано под бесплатный план Apify (~$5/мес):
- жёсткий лимит результатов и запусков за сессию;
- Apify только для LinkedIn/Indeed (Playwright для остальных);
- fallback на Playwright, если квота исчерпана.
"""

from __future__ import annotations

import asyncio
import inspect
import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from src.models.models import JobInfo
from src.utils.logger import get_logger
from src.utils.site_patterns import SiteType, detect_site_type, get_site_config
from src.utils.utils import parse_date_string

logger = get_logger(__name__)

DEFAULT_LINKEDIN_ACTOR = "curious_coder/linkedin-jobs-scraper"
DEFAULT_INDEED_ACTOR = "misceres/indeed-scraper"
DEFAULT_WEB_SCRAPER = "apify/web-scraper"

_session_runs = 0


@dataclass
class ApifyLimits:
    max_results_per_run: int
    max_runs_per_session: int
    fallback_to_playwright: bool


def get_apify_limits() -> ApifyLimits:
    load_dotenv()
    return ApifyLimits(
        max_results_per_run=int(os.getenv("APIFY_MAX_RESULTS", "10")),
        max_runs_per_session=int(os.getenv("APIFY_MAX_RUNS_PER_SESSION", "5")),
        fallback_to_playwright=os.getenv("APIFY_FALLBACK_PLAYWRIGHT", "true").lower() == "true",
    )


def is_apify_available() -> bool:
    load_dotenv()
    return bool(os.getenv("APIFY_API_TOKEN"))


def get_apify_token() -> Optional[str]:
    load_dotenv()
    return os.getenv("APIFY_API_TOKEN")


def _actor_id(env_key: str, default: str) -> str:
    load_dotenv()
    return os.getenv(env_key, default)


def can_run_apify() -> tuple[bool, str]:
    """Проверяет, можно ли запустить ещё один Apify run в этой сессии."""
    global _session_runs

    if not is_apify_available():
        return False, "APIFY_API_TOKEN не задан — используется Playwright"

    limits = get_apify_limits()
    if _session_runs >= limits.max_runs_per_session:
        return False, (
            f"Лимит Apify ({limits.max_runs_per_session} запусков/сессию) исчерпан. "
            "Playwright fallback."
        )
    return True, "OK"


def reset_session_runs() -> None:
    global _session_runs
    _session_runs = 0


def _extract_field(item: dict, *keys: str, default: str = "") -> str:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _items_to_jobs(items: List[dict], source_hint: str = "") -> List[JobInfo]:
    jobs: List[JobInfo] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        title = _extract_field(
            item, "title", "jobTitle", "position", "name", "job_title",
        )
        if not title or title == "—":
            continue

        company = _extract_field(
            item, "company", "companyName", "company_name", "employer",
        ) or "—"
        description = _extract_field(
            item, "description", "jobDescription", "job_description", "snippet",
        )
        url = _extract_field(
            item, "url", "link", "jobUrl", "job_url", "applyUrl", "externalApplyLink",
        ) or source_hint

        posted = _extract_field(item, "postedDate", "posted_at", "datePosted", "listedAt")
        salary = _extract_field(item, "salary", "salaryRange", "salaryText")
        location = _extract_field(item, "location", "jobLocation", "place")
        employment = _extract_field(
            item, "employmentType", "employment_type", "jobType", "workType",
        )

        jobs.append(JobInfo(
            title=title,
            company=company,
            description=description or "—",
            application_info=url or "—",
            posted_date=parse_date_string(posted or None),
            source_url=url or None,
            job_location=location,
            employment_type=employment,
            salary_range=salary,
        ))
    return jobs


def _run_dataset_id(run: Any) -> Optional[str]:
    if run is None:
        return None
    if isinstance(run, dict):
        return run.get("defaultDatasetId") or run.get("default_dataset_id")
    return getattr(run, "default_dataset_id", None) or getattr(run, "defaultDatasetId", None)


def _dataset_items(result: Any) -> List[dict]:
    if result is None:
        return []
    if hasattr(result, "items"):
        items = result.items
        return list(items) if items else []
    if isinstance(result, dict):
        return list(result.get("items") or [])
    return []


def _default_memory_mb(actor_id: str) -> int:
    load_dotenv()
    if "web-scraper" in actor_id.lower():
        return int(os.getenv("APIFY_WEB_SCRAPER_MEMORY_MB", "512"))
    return int(os.getenv("APIFY_MEMORY_MB", "256"))


def is_apify_web_scrape_candidate(url: str) -> bool:
    """Web-scraper имеет смысл только для страниц поиска/вакансий, не для /pracodawcy и т.п."""
    from src.utils.site_patterns import categorize_link_heuristic

    category = categorize_link_heuristic(url)
    if category in {"job_detail_links", "job_listing_pages"}:
        return True

    path = url.lower()
    job_hints = ("/search", "/szukaj", "/jobs", "/praca?", "/oferty", "/vacancy", "q=", "keywords=")
    bad_hints = ("/pracodawcy", "/employers", "/company/", "/login", "/register", "/about")
    if any(h in path for h in bad_hints):
        return False
    return any(h in path for h in job_hints)


def _call_actor(
    client: Any,
    actor_id: str,
    run_input: dict,
    *,
    memory_mbytes: Optional[int] = None,
) -> Optional[Any]:
    """Запуск Actor — передаём только параметры, поддерживаемые установленной версией SDK."""
    actor = client.actor(actor_id)
    mem = memory_mbytes if memory_mbytes is not None else _default_memory_mb(actor_id)
    try:
        params = inspect.signature(actor.call).parameters
    except (TypeError, ValueError):
        return actor.call(run_input=run_input)

    kwargs: Dict[str, Any] = {"run_input": run_input}

    if "memory_mbytes" in params:
        kwargs["memory_mbytes"] = mem
    if "run_timeout" in params:
        kwargs["run_timeout"] = timedelta(seconds=180)
    elif "timeout_secs" in params:
        kwargs["timeout_secs"] = 180
    if "wait_duration" in params:
        kwargs["wait_duration"] = timedelta(seconds=240)
    elif "wait_secs" in params:
        kwargs["wait_secs"] = 240

    try:
        return actor.call(**kwargs)
    except TypeError:
        return actor.call(run_input=run_input)


async def _run_actor(
    actor_id: str,
    run_input: dict,
    *,
    memory_mbytes: Optional[int] = None,
) -> List[dict]:
    """Запускает Apify Actor и возвращает items из dataset."""
    global _session_runs

    token = get_apify_token()
    if not token:
        return []

    ok, reason = can_run_apify()
    if not ok:
        logger.warning(reason)
        return []

    limits = get_apify_limits()

    def _sync_run() -> List[dict]:
        from apify_client import ApifyClient

        client = ApifyClient(token)
        logger.info("Apify run: %s (max %d items)", actor_id, limits.max_results_per_run)
        run = _call_actor(client, actor_id, run_input, memory_mbytes=memory_mbytes)
        dataset_id = _run_dataset_id(run)
        if not dataset_id:
            return []
        result = client.dataset(dataset_id).list_items(limit=limits.max_results_per_run)
        return _dataset_items(result)

    try:
        items = await asyncio.to_thread(_sync_run)
        _session_runs += 1
        logger.info("Apify вернул %d items (run %d)", len(items), _session_runs)
        return items
    except Exception as error:
        logger.error("Apify ошибка (%s): %s", actor_id, error)
        return []


def _linkedin_input(query: str, max_results: int, location: str = "Poland") -> dict:
    """Input для curious_coder/linkedin-jobs-scraper и аналогов."""
    return {
        "keywords": query,
        "location": location,
        "maxResults": max_results,
        "maxItems": max_results,
        "count": max_results,
    }


def _indeed_input(
    query: str,
    max_results: int,
    start_url: Optional[str] = None,
    country: str = "PL",
    location: str = "Polska",
) -> dict:
    run_input: Dict[str, Any] = {
        "position": query,
        "maxItemsPerSearch": max_results,
        "saveOnlyUniqueItems": True,
        "country": country,
        "location": location,
        "parseCompanyDetails": False,
        "followApplyRedirects": False,
    }
    if start_url:
        run_input["startUrls"] = [{"url": start_url}]
    return run_input


async def scrape_url_via_apify(url: str) -> dict:
    """Scrape одной URL через Apify web-scraper (fallback при bot-защите)."""
    if not is_apify_web_scrape_candidate(url):
        logger.info("Apify web-scraper пропущен (нерелевантный URL): %s", url[:70])
        return {}

    actor = _actor_id("APIFY_WEB_SCRAPER", DEFAULT_WEB_SCRAPER)
    mem = _default_memory_mb(actor)

    page_function = """
    async function pageFunction(context) {
        const { page, request } = context;
        await page.waitForSelector('body', { timeout: 20000 }).catch(() => null);
        await page.waitForTimeout(1500);
        const title = await page.title();
        const text = await page.evaluate(() => {
            const main = document.querySelector(
                'main, [class*="job"], [class*="offer"], [class*="oferta"], article, #content'
            ) || document.body;
            return (main.innerText || '').slice(0, 12000);
        });
        const links = await page.evaluate(() =>
            Array.from(document.querySelectorAll('a[href]'))
                .map(a => ({ href: a.href, text: (a.textContent || '').trim().slice(0, 100) }))
                .filter(l => l.href && l.href.startsWith('http'))
                .slice(0, 200)
        );
        return { url: request.url, title, text, links };
    }
    """

    run_input = {
        "startUrls": [{"url": url}],
        "pageFunction": page_function,
        "maxPagesPerCrawl": 1,
        "maxConcurrency": 1,
        "maxRequestRetries": 1,
        "pageLoadTimeoutSecs": 45,
        "pageFunctionTimeoutSecs": 60,
        "proxyConfiguration": {"useApifyProxy": True},
        "browserPoolOptions": {
            "useFingerprints": True,
        },
    }

    items = await _run_actor(actor, run_input, memory_mbytes=mem)
    if not items:
        return {}
    item = items[0]
    if isinstance(item, dict):
        return {
            "title": item.get("title", ""),
            "text": item.get("text", ""),
            "html": item.get("html", ""),
            "links": item.get("links", []),
        }
    return {}


async def apify_fallback_scrape(
    base_url: str,
    query: str,
    location: str = "Polska",
) -> dict:
    """Пробует Apify: job-scraper для LinkedIn/Indeed, иначе web-scraper на search URL."""
    from src.utils.site_patterns import build_search_urls_for_site

    site_type = detect_site_type(base_url)

    if query and site_type in {SiteType.LINKEDIN, SiteType.INDEED}:
        jobs = await search_via_apify(base_url, query, max_jobs=5, location=location)
        if jobs:
            first = jobs[0]
            return {
                "title": first.title,
                "text": f"{first.title}\n{first.company}\n{first.description}",
                "html": "",
                "links": [{"href": first.source_url or base_url, "text": first.title}],
            }

    candidates = build_search_urls_for_site(base_url, query[:100], location=location) if query else [base_url]
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if not is_apify_web_scrape_candidate(candidate):
            continue
        scraped = await scrape_url_via_apify(candidate)
        if scraped and scraped.get("text", "").strip():
            return scraped

    return {}


async def search_via_apify(
    url: str,
    query: str,
    max_jobs: int,
    location: str = "Polska",
) -> List[JobInfo]:
    """Запускает Apify для поддерживаемого сайта."""
    site_type = detect_site_type(url)
    config = get_site_config(url)
    limits = get_apify_limits()
    max_results = min(max_jobs, limits.max_results_per_run)

    if site_type == SiteType.LINKEDIN:
        actor = _actor_id("APIFY_LINKEDIN_ACTOR", DEFAULT_LINKEDIN_ACTOR)
        items = await _run_actor(actor, _linkedin_input(query, max_results, location=location))
        return _items_to_jobs(items)

    if site_type == SiteType.INDEED:
        actor = _actor_id("APIFY_INDEED_ACTOR", DEFAULT_INDEED_ACTOR)
        country = (config.indeed_country if config else None) or "PL"
        if "indeed.com" in url and "pl.indeed" not in url and "indeed.pl" not in url:
            country = "US"
            location = "United States"
        items = await _run_actor(
            actor,
            _indeed_input(query, max_results, start_url=url, country=country, location=location),
        )
        return _items_to_jobs(items)

    return []


def apify_status_message() -> str:
    """Краткий статус Apify для пользователя."""
    if not is_apify_available():
        return "Apify: не настроен (только Playwright)"
    limits = get_apify_limits()
    remaining = max(0, limits.max_runs_per_session - _session_runs)
    return (
        f"Apify: ✅ (free plan, до {limits.max_results_per_run} вак./run, "
        f"осталось {remaining} запусков)"
    )
