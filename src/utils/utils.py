import asyncio
import re
from datetime import datetime, timedelta
from inspect import isawaitable
from typing import Any, Callable, List, Optional
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

from src.models.models import AgentState
from src.utils.logger import get_logger

logger = get_logger(__name__)


def parse_date_string(date_str: Optional[str]) -> Optional[datetime]:
    """Преобразует дату публикации в объект `datetime`.

    Поддерживаются ISO-формат, распространённые числовые форматы и
    относительные даты на английском, русском и польском языках.
    """
    if not date_str:
        return None

    value = date_str.strip().lower()
    now = datetime.now()

    try:
        if value in {"сегодня", "today", "dzisiaj"}:
            return now
        if value in {"вчера", "yesterday", "wczoraj"}:
            return now - timedelta(days=1)

        amount_match = re.search(r"\d+", value)
        if amount_match:
            amount = int(amount_match.group())
            units = (
                (r"day|days|дн|день|дня|дней|dzień|dni|dnia", "days"),
                (r"hour|hours|час|часа|часов|godzin|godzina|godz", "hours"),
                (r"minute|minutes|мин|минута|минуты|минут|minut|minuta", "minutes"),
                (r"week|weeks|недел|tydzień|tygodni|tygodnia", "weeks"),
            )
            for pattern, unit in units:
                if re.search(pattern, value):
                    return now - timedelta(**{unit: amount})

        month_names = {
            "января": "january", "февраля": "february", "марта": "march",
            "апреля": "april", "мая": "may", "июня": "june",
            "июля": "july", "августа": "august", "сентября": "september",
            "октября": "october", "ноября": "november", "декабря": "december",
            "stycznia": "january", "lutego": "february", "marca": "march",
            "kwietnia": "april", "maja": "may", "czerwca": "june",
            "lipca": "july", "sierpnia": "august", "września": "september",
            "października": "october", "listopada": "november", "grudnia": "december",
        }
        localized_value = value
        for localized_month, english_month in month_names.items():
            localized_value = localized_value.replace(localized_month, english_month)

        for date_format in (
            "%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y", "%Y/%m/%d",
            "%d/%m/%Y", "%d %B %Y", "%d %b %Y",
        ):
            try:
                return datetime.strptime(localized_value, date_format)
            except ValueError:
                continue

        return datetime.fromisoformat(value.replace("z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None


def is_job_detail_url(url: str) -> bool:
    """Определяет, похожа ли ссылка на страницу отдельной вакансии."""
    if not isinstance(url, str):
        return False

    from src.utils.site_patterns import categorize_link_heuristic

    if categorize_link_heuristic(url) == "job_detail_links":
        return True
    if categorize_link_heuristic(url) == "job_listing_pages":
        return False

    path = unquote(urlparse(url).path).lower().strip("/")
    path_segments = path.split("/")
    listing_segments = {"search", "find", "поиск", "vacancies", "вакансии"}
    if any(segment in listing_segments for segment in path_segments):
        return False

    indicators = (
        "job", "career", "careers", "position", "opening", "vacancy", "vacature", "praca", "oferta-pracy",
        "ogloszenie", "ogłoszenie", "zatrudnienie", "stanowisko", "ваканс", "работ",
        "должность", "карьера", "вакансия",
    )
    return any(
        segment == indicator or segment.startswith(indicator)
        for segment in path_segments
        for indicator in indicators
    )


async def with_retry_and_rate_limit(
    state: AgentState,
    operation: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Выполняет операцию с паузой между запросами и повторными попытками."""
    max_retries = max(1, state["max_retries"])
    for attempt in range(max_retries):
        try:
            delay = state["delay_between_requests"]
            last_request_time = state.get("last_request_time")
            if last_request_time:
                elapsed = (datetime.now() - last_request_time).total_seconds()
                if elapsed < delay:
                    await asyncio.sleep(delay - elapsed)
            state["last_request_time"] = datetime.now()
            result = operation(*args, **kwargs)
            if isawaitable(result):
                result = await result
            state["retry_count"] = 0
            return result

        except Exception as error:
            state["retry_count"] = attempt + 1
            error_msg = f"Попытка {attempt + 1}/{max_retries} завершилась ошибкой: {error}"
            error_text = str(error).lower()
            browser_is_missing = (
                "executable doesn't exist" in error_text
                or "playwright install" in error_text
            )
            credentials_are_missing = (
                "missing credentials" in error_text
                or "openai_api_key" in error_text
                or "openrouter_api_key" in error_text
                or "pass an `api_key`" in error_text
            )

            if browser_is_missing or credentials_are_missing or attempt == max_retries - 1:
                state["error_count"] += 1
                state["status_message"] = f"Операция завершилась ошибкой: {error}"
                logger.error(error_msg)
                return None

            wait_time = delay * (2 ** attempt)
            logger.warning("%s. Повтор через %.1f сек.", error_msg, wait_time)
            await asyncio.sleep(wait_time)

    return None


def validate_environment() -> str:
    """Проверяет наличие и валидность ключа OpenRouter."""
    from src.utils.llm import validate_openrouter_key

    masked = validate_openrouter_key()
    logger.info("Переменные окружения успешно загружены")
    logger.info("Ключ OpenRouter: %s", masked)
    return masked


def normalize_url(url: str) -> str:
    """Нормализует URL независимо от языка содержимого страницы."""
    try:
        parsed = urlparse(url.strip())
        tracking_params = {
            'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
            'fbclid', 'gclid', 'msclkid', 'ref', 'source', 'campaign',
            'sessionid', 'jsessionid', 'phpsessid', '_ga', '_gid'
        }
        query = urlencode(
            [
                (key, value)
                for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                if key.lower() not in tracking_params
            ]
        )
        path = parsed.path.rstrip("/") or "/"
        return urlunparse(
            (parsed.scheme.lower(), parsed.netloc.lower(), path, "", query, "")
        )
    except (AttributeError, TypeError, ValueError):
        logger.warning("Не удалось нормализовать URL: %s", url)
        return url


def validate_and_filter_links(
    links: List[str],
    current_url: str,
    links_visited: set,
    links_to_visit: list,
    base_domain: Optional[str] = None,
) -> List[str]:
    """Проверяет и фильтрует ссылки перед добавлением в очередь.

    Args:
        links: Список ссылок для проверки.
        current_url: URL текущей страницы.
        links_visited: Уже посещённые ссылки.
        links_to_visit: Текущая очередь ссылок.
        base_domain: Домен, если нужно ограничить переходы одним сайтом.

    Returns:
        Список проверенных уникальных ссылок.
    """
    if not links:
        return []

    logger.debug("Проверка ссылок: %d", len(links))

    normalized_current = normalize_url(current_url)

    normalized_visited = {normalize_url(url) for url in links_visited}
    normalized_queue = {normalize_url(url) for url in links_to_visit}

    if not base_domain:
        base_domain = urlparse(current_url).hostname
    elif "://" in base_domain:
        base_domain = urlparse(base_domain).hostname
    base_domain = base_domain.lower() if base_domain else None

    valid_links = []
    seen_normalized = set()

    for link in links:
        try:
            if not link or not isinstance(link, str):
                continue

            if urlparse(link).scheme.lower() not in {"http", "https"}:
                logger.debug("Пропуск ссылки с неподдерживаемой схемой: %s", link)
                continue

            parsed = urlparse(link)
            if not parsed.netloc:
                logger.debug("Пропуск некорректного URL: %s", link)
                continue

            normalized_link = normalize_url(link)

            if normalized_link in normalized_visited:
                continue

            if normalized_link in normalized_queue:
                continue

            if normalized_link in seen_normalized:
                continue

            if normalized_link == normalized_current:
                continue

            if base_domain:
                link_domain = parsed.hostname.lower() if parsed.hostname else ""
                if link_domain != base_domain:
                    continue

            if len(link) > 500:
                continue

            skip_patterns = [
                r"\.(?:pdf|doc|zip|exe)$",
                r"/(?:api|ajax|wp-admin|admin)/",
                r"(?:javascript:|mailto:|tel:|ftp:)",
                r"#$|\?print=|/print/",
            ]

            if any(re.search(pattern, link, re.IGNORECASE) for pattern in skip_patterns):
                continue

            valid_links.append(link)
            seen_normalized.add(normalized_link)

        except (AttributeError, TypeError, ValueError) as error:
            logger.warning("Ошибка проверки ссылки %s: %s", link, error)
            continue

    logger.info(
        "Проверено уникальных ссылок: %d из %d",
        len(valid_links),
        len(links),
    )

    if valid_links:
        logger.debug("Примеры подходящих ссылок: %s", valid_links[:3])

    return valid_links