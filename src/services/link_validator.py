"""Проверка ссылок, отправленных пользователем."""

import asyncio
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlparse

import httpx

from src.utils.logger import get_logger
from src.utils.site_patterns import SiteType, detect_site_type, get_site_config, is_supported_job_site
from src.utils.stealth_browser import pick_user_agent
from src.utils.utils import normalize_url

logger = get_logger(__name__)

BLOCKED_SCHEMES = {"javascript", "mailto", "tel", "ftp", "data"}
MAX_URL_LENGTH = 500

# Типичные ответы anti-bot — не означают, что URL неверный
BOT_WALL_STATUSES = {401, 403, 429, 503}


@dataclass
class LinkValidationResult:
    url: str
    normalized_url: str
    is_valid: bool
    site_type: SiteType
    site_name: str
    message: str
    http_status: Optional[int] = None
    is_reachable: bool = False
    bot_protected: bool = False


@dataclass
class BatchValidationResult:
    valid_links: List[LinkValidationResult] = field(default_factory=list)
    invalid_links: List[LinkValidationResult] = field(default_factory=list)
    warnings: List[LinkValidationResult] = field(default_factory=list)

    @property
    def all_valid(self) -> bool:
        return len(self.invalid_links) == 0 and len(self.valid_links) > 0


def _site_display_name(site_type: SiteType, url: str) -> str:
    config = get_site_config(url)
    if config:
        return config.name
    if site_type == SiteType.GENERIC and is_supported_job_site(url):
        return "Job-сайт (универсальный)"
    return "Другой сайт"


def validate_url_format(url: str) -> LinkValidationResult:
    """Синхронная проверка формата URL без HTTP-запроса."""
    raw = (url or "").strip()
    normalized = normalize_url(raw)

    if not raw:
        return LinkValidationResult(
            url=raw, normalized_url=normalized, is_valid=False,
            site_type=SiteType.GENERIC, site_name="—", message="Пустая ссылка",
        )

    if len(raw) > MAX_URL_LENGTH:
        return LinkValidationResult(
            url=raw, normalized_url=normalized, is_valid=False,
            site_type=SiteType.GENERIC, site_name="—", message="Ссылка слишком длинная",
        )

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()

    if scheme in BLOCKED_SCHEMES:
        return LinkValidationResult(
            url=raw, normalized_url=normalized, is_valid=False,
            site_type=SiteType.GENERIC, site_name="—",
            message=f"Неподдерживаемая схема: {scheme}",
        )

    if scheme not in {"http", "https"}:
        return LinkValidationResult(
            url=raw, normalized_url=normalized, is_valid=False,
            site_type=SiteType.GENERIC, site_name="—",
            message="Ссылка должна начинаться с http:// или https://",
        )

    if not parsed.netloc:
        return LinkValidationResult(
            url=raw, normalized_url=normalized, is_valid=False,
            site_type=SiteType.GENERIC, site_name="—",
            message="Некорректный URL: отсутствует домен",
        )

    if not is_supported_job_site(raw):
        return LinkValidationResult(
            url=raw, normalized_url=normalized, is_valid=False,
            site_type=SiteType.GENERIC, site_name="—",
            message="Сайт не поддерживается или домен недоступен",
        )

    site_type = detect_site_type(raw)
    site_name = _site_display_name(site_type, raw)

    return LinkValidationResult(
        url=raw,
        normalized_url=normalized,
        is_valid=True,
        site_type=site_type,
        site_name=site_name,
        message=f"✅ {site_name}",
    )


def _browser_headers() -> dict:
    return {
        "User-Agent": pick_user_agent(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
    }


async def check_url_reachable(url: str, timeout: float = 10.0) -> tuple[bool, Optional[int]]:
    """HEAD/GET с browser-like заголовками. 403 часто = bot-защита, не мёртвый сайт."""
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            headers=_browser_headers(),
        ) as client:
            response = await client.head(url)
            if response.status_code in BOT_WALL_STATUSES or response.status_code >= 400:
                response = await client.get(url, headers={**_browser_headers(), "Range": "bytes=0-2048"})
            status = response.status_code
            ok = status < 400
            if status in BOT_WALL_STATUSES:
                return False, status
            return ok, status
    except httpx.TimeoutException:
        logger.debug("Таймаут при проверке %s", url)
        return False, None
    except httpx.HTTPError as error:
        logger.debug("HTTP-ошибка при проверке %s: %s", url, error)
        return False, None


def _accept_despite_network_issue(url: str, status: Optional[int]) -> bool:
    """Известные job-сайты принимаем даже при 403/таймауте — stealth/Apify справятся."""
    if get_site_config(url):
        return True
    if status in BOT_WALL_STATUSES:
        return is_supported_job_site(url)
    if status is None:
        return is_supported_job_site(url)
    return False


async def validate_user_link(url: str, check_reachability: bool = True) -> LinkValidationResult:
    """Проверка ссылки. HTTP 403 на job-портале не блокирует поиск."""
    result = validate_url_format(url)
    if not result.is_valid:
        return result

    if not check_reachability:
        return result

    reachable, status = await check_url_reachable(result.normalized_url)
    result.http_status = status
    result.is_reachable = reachable

    if reachable:
        result.message = f"✅ {result.site_name} — доступен"
        return result

    if _accept_despite_network_issue(url, status):
        result.is_valid = True
        result.bot_protected = status in BOT_WALL_STATUSES or status is None
        if status in BOT_WALL_STATUSES:
            result.message = f"✅ {result.site_name} — stealth/Apify (HTTP {status})"
        else:
            result.message = f"✅ {result.site_name} — stealth-поиск"
        logger.info("URL принят несмотря на HTTP %s: %s", status, url[:60])
        return result

    result.is_valid = False
    status_text = f" (HTTP {status})" if status else ""
    result.message = f"Сайт недоступен{status_text}. Проверьте ссылку."
    return result


async def validate_user_links(
    urls: List[str],
    check_reachability: bool = True,
) -> BatchValidationResult:
    """Параллельная проверка списка ссылок."""
    tasks = [validate_user_link(url, check_reachability) for url in urls]
    results = await asyncio.gather(*tasks)

    batch = BatchValidationResult()
    for result in results:
        if result.is_valid:
            batch.valid_links.append(result)
            if result.bot_protected:
                batch.warnings.append(result)
        else:
            batch.invalid_links.append(result)
    return batch


def extract_urls_from_text(text: str) -> List[str]:
    """Извлекает URL из текста сообщения."""
    import re

    pattern = r"https?://[^\s,;<>\"']+"
    found = re.findall(pattern, text)
    return list(dict.fromkeys(url.rstrip(".,)") for url in found))
