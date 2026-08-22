"""Оркестрация обхода bot-защиты: Playwright stealth → Apify fallback."""

from __future__ import annotations

from typing import List, Optional
from urllib.parse import urlparse

from src.models.models import JobInfo
from src.services.apify_service import apify_fallback_scrape, can_run_apify, is_apify_web_scrape_candidate
from src.utils.bot_detection import is_content_usable
from src.utils.browser_pool import PageFetchResult, fetch_page_stealth
from src.utils.logger import get_logger

logger = get_logger(__name__)


async def fetch_with_antibot(
    url: str,
    *,
    query: str = "",
    scroll: bool = True,
    location: str = "Polska",
    site_base_url: str = "",
) -> PageFetchResult:
    """Загружает страницу; при блокировке пробует Apify."""
    result = await fetch_page_stealth(url, scroll=scroll)

    if not result.blocked.is_blocked and is_content_usable(result.text):
        return result

    apify_ok, apify_reason = can_run_apify()
    if not apify_ok:
        logger.warning("Apify недоступен для fallback: %s", apify_reason)
        return result

    logger.info("Fallback Apify для %s (причина: %s)", url[:50], result.blocked.reason)

    base = site_base_url or url
    apify_data = await apify_fallback_scrape(base, query, location=location)
    if apify_data and is_content_usable(apify_data.get("text", "")):
        return PageFetchResult(
            url=url,
            title=apify_data.get("title", ""),
            text=apify_data.get("text", ""),
            html=apify_data.get("html", ""),
            links=apify_data.get("links", []),
            blocked=result.blocked,
            strategy="apify-fallback",
        )

    if is_apify_web_scrape_candidate(url):
        from src.services.apify_service import scrape_url_via_apify

        apify_html = await scrape_url_via_apify(url)
        if apify_html and is_content_usable(apify_html.get("text", "")):
            return PageFetchResult(
                url=url,
                title=apify_html.get("title", ""),
                text=apify_html.get("text", ""),
                html=apify_html.get("html", ""),
                links=apify_html.get("links", []),
                blocked=result.blocked,
                strategy="apify-fallback",
            )

    return result


def filter_same_domain_links(links: List[dict], base_url: str) -> List[dict]:
    base_domain = urlparse(base_url).hostname
    unique: dict = {}
    for link in links:
        href = link.get("href", "")
        if urlparse(href).hostname == base_domain and href not in unique:
            unique[href] = link
    return list(unique.values())
