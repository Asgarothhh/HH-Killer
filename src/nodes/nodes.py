import asyncio
import re
from typing import Dict, List, Optional
from urllib.parse import urlparse

from src.models.models import AgentState, JobInfo, JobExtraction, LinksCategorization
from src.services.anti_bot_service import fetch_with_antibot, filter_same_domain_links
from src.utils.llm import create_chat_model
from src.utils.logger import get_logger
from src.utils.site_patterns import (
    categorize_links_heuristic,
    preserves_search_scope,
    search_scope_tokens,
)
from src.utils.utils import is_job_detail_url, with_retry_and_rate_limit, parse_date_string, validate_and_filter_links

logger = get_logger(__name__)

LLM_BATCH_SIZE = 30
MAX_LINKS_FOR_LLM = 60


_REGION_IN_CONTEXT = re.compile(
    r"(?:Регион|Локация)\s*(?:\(строго\))?\s*:\s*(?:только\s+)?([^\n.]+)",
    re.I,
)


def _location_from_preference(preference: str) -> str:
    """Достаёт регион из контекста поиска, собранного build_matching_context."""
    match = _REGION_IN_CONTEXT.search(preference or "")
    if match:
        return match.group(1).strip()
    return "Polska"


def _prioritize_links(links: List[str]) -> List[str]:
    details = [u for u in links if is_job_detail_url(u)]
    rest = [u for u in links if not is_job_detail_url(u)]
    return details + rest


async def job_info_extractor(state: AgentState) -> dict:
    links_to_visit = list(state.get("links_to_visit", []))
    links_visited = set(state.get("links_visited", set()))
    jobs_found = list(state.get("jobs_found", []))
    user_job_preference = state.get("user_job_preference", "")
    max_job = state.get("max_job", 5)

    if not links_to_visit or len(jobs_found) >= max_job:
        return {}

    job_url = None
    temp_links = []

    while links_to_visit:
        url = links_to_visit.pop(0)
        if is_job_detail_url(url):
            job_url = url
            links_to_visit = temp_links + links_to_visit
            break
        temp_links.append(url)

    if not job_url:
        links_to_visit = temp_links + links_to_visit
        return {"links_to_visit": links_to_visit}

    logger.info("Извлечение сведений о вакансии: %s", job_url)

    try:
        job_info = await with_retry_and_rate_limit(
            state,
            extract_job_details_modern,
            job_url,
            user_job_preference,
            state.get("website", ""),
        )
    except Exception as error:
        logger.error("Ошибка извлечения данных из %s: %s", job_url, error)
        job_info = None

    updates = {"current_page_url": job_url}

    if job_info:
        exists = any(j.source_url == job_info.source_url for j in jobs_found)
        if not exists:
            jobs_found.append(job_info)
            updates["jobs_found"] = [job_info]
            updates["status_message"] = f"Найдена: {job_info.title[:50]}"
            logger.info("Добавлена вакансия: %s, компания: %s", job_info.title, job_info.company)

    links_visited.add(job_url)
    updates["links_to_visit"] = links_to_visit
    updates["links_visited"] = {job_url}

    return updates


async def extract_job_details_modern(
    url: str,
    user_preference: str,
    site_base: str = "",
) -> Optional[JobInfo]:
    """Загружает страницу (stealth + anti-bot) и извлекает сведения о вакансии."""
    try:
        page_result = await fetch_with_antibot(
            url,
            query=user_preference,
            scroll=True,
            location=_location_from_preference(user_preference),
            site_base_url=site_base or url,
        )
        page_content = page_result.text

        if page_result.blocked.is_blocked and not page_content.strip():
            raise RuntimeError(
                f"Страница заблокирована ({page_result.blocked.reason}). "
                "Попробуйте Apify или задайте PLAYWRIGHT_PROXY / PLAYWRIGHT_STORAGE_STATE."
            )

        llm = create_chat_model()
        structured_llm = llm.with_structured_output(JobExtraction)

        extraction_prompt = f"""
        Извлеки сведения о вакансии из текста страницы job-портала.

        Контекст поиска пользователя: {user_preference}

        Текст страницы:
        {page_content[:12000]}

        Правила:
        - job_title — точное название должности
        - company_name — работодатель
        - job_description — обязанности и требования (без дублирования локации и зарплаты)
        - location — город и страна ровно так, как указано в вакансии.
          Не подставляй регион из контекста поиска и не догадывайся: если места
          работы на странице нет, оставь поле пустым.
        - employment_type — полная занятость, частичная, контракт, стажировка,
          плюс формат работы (офис / гибрид / удалённо), если он указан
        - salary_range — скопируй зарплату со страницы дословно, вместе с валютой
          и периодом (например «23 233 – 23 917 zł brutto / mies.»). Не переводи
          валюту на другой язык, не пересчитывай и не убирай пробелы внутри чисел:
          «23 233» — это одно число, а не «23233» или «232 33»
        - application_method — ссылка или email для отклика
        - posted_date — дата публикации, если есть

        Каждое поле заполняй отдельно согласно схеме JobExtraction.
        Не смешивай локацию и зарплату в job_description. Ничего не выдумывай.
        """

        result = await asyncio.to_thread(structured_llm.invoke, extraction_prompt)

        return JobInfo(
            title=result.job_title,
            company=result.company_name,
            description=result.job_description,
            application_info=result.application_method or url,
            posted_date=parse_date_string(result.posted_date),
            source_url=url,
            job_location=result.location,
            employment_type=result.employment_type,
            salary_range=result.salary_range,
        )

    except Exception as error:
        logger.error("Не удалось извлечь сведения о вакансии %s: %s", url, error)
        raise RuntimeError(f"Не удалось извлечь сведения о вакансии {url}: {error}") from error


async def _categorize_with_llm(
    links: List[dict],
    user_preference: str,
) -> Dict[str, List[str]]:
    llm = create_chat_model()
    structured_llm = llm.with_structured_output(LinksCategorization)

    links_text = "\n".join(
        f"URL: {link['href']}\nText: {link['text'][:80]}\n---"
        for link in links[:LLM_BATCH_SIZE]
    )

    prompt = f"""
    Пользователь ищет: {user_preference}

    Распредели ссылки:
    - job_detail_links: отдельные вакансии
    - job_listing_pages: списки вакансий
    - navigation_links: пагинация, фильтры

    Ссылки:
    {links_text}
    """

    categorized = await asyncio.to_thread(structured_llm.invoke, prompt)
    return {
        "job_detail_links": categorized.job_detail_links,
        "job_listing_pages": categorized.job_listing_pages,
        "navigation_links": categorized.navigation_links,
    }


async def job_link_extractor(state: AgentState) -> dict:
    links_to_visit = list(state.get("links_to_visit", []))
    links_visited = set(state.get("links_visited", set()))
    user_job_preference = state.get("user_job_preference", "")
    website = state.get("website", "")

    if not links_to_visit:
        return {}

    current_url = links_to_visit.pop(0)
    logger.info("Извлечение ссылок: %s", current_url)

    page_data = await with_retry_and_rate_limit(
        state,
        extract_page_links_modern,
        current_url,
        user_job_preference,
        website,
    )

    updates = {"current_page_url": current_url}

    if page_data:
        # Страницы выдачи берём только те, что сохраняют исходный запрос:
        # иначе обход сползает на общий список вакансий сайта.
        scope = search_scope_tokens(website or current_url)
        listings = [
            link for link in page_data.get("job_listing_pages", [])
            if preserves_search_scope(link, scope)
        ]
        navigation = [
            link for link in page_data.get("navigation_links", [])
            if preserves_search_scope(link, scope)
        ][:3]

        all_new = page_data.get("job_detail_links", []) + listings + navigation

        base_domain = urlparse(website or current_url).hostname
        validated = validate_and_filter_links(
            all_new, current_url, links_visited, links_to_visit, base_domain=base_domain,
        )
        validated = _prioritize_links(validated)

        for link in validated:
            if any(x in link.lower() for x in ("/pracodawcy", "/employers", "/login", "/register")):
                continue
            if link not in links_visited and link not in links_to_visit:
                links_to_visit.insert(0 if is_job_detail_url(link) else len(links_to_visit), link)

        logger.info("Добавлено проверенных ссылок: %d", len(validated))

    links_visited.add(current_url)
    updates["links_to_visit"] = links_to_visit
    updates["links_visited"] = {current_url}
    updates["status_message"] = f"Сканирую страницу ({len(links_to_visit)} в очереди)"

    return updates


async def extract_page_links_modern(
    url: str,
    user_preference: str,
    site_base: str = "",
) -> Optional[Dict[str, List[str]]]:
    """Загружает страницу (stealth + anti-bot) и распределяет ссылки."""
    try:
        page_result = await fetch_with_antibot(
            url,
            query=user_preference,
            scroll=True,
            location=_location_from_preference(user_preference),
            site_base_url=site_base or url,
        )

        if page_result.blocked.is_blocked:
            logger.warning("Bot-защита на %s: %s (strategy: %s)",
                           url[:50], page_result.blocked.reason, page_result.strategy)

        filtered = filter_same_domain_links(page_result.links, url)
        hrefs = [l["href"] for l in filtered]
        heuristic = categorize_links_heuristic(hrefs)

        result = {
            "job_detail_links": heuristic["job_detail_links"],
            "job_listing_pages": heuristic["job_listing_pages"],
            "navigation_links": heuristic["navigation_links"],
        }

        uncategorized = heuristic["uncategorized"]
        if uncategorized and len(uncategorized) <= MAX_LINKS_FOR_LLM:
            link_map = {l["href"]: l for l in filtered}
            uncategorized_links = [link_map[u] for u in uncategorized[:LLM_BATCH_SIZE] if u in link_map]
            if uncategorized_links:
                llm_result = await _categorize_with_llm(uncategorized_links, user_preference)
                for key in result:
                    result[key].extend(llm_result.get(key, []))

        for key in result:
            result[key] = list(dict.fromkeys(result[key]))

        logger.info(
            "Ссылки (%s): вакансии %d, списки %d, навигация %d",
            page_result.strategy,
            len(result["job_detail_links"]),
            len(result["job_listing_pages"]),
            len(result["navigation_links"]),
        )
        return result

    except Exception as error:
        logger.error("Не удалось извлечь ссылки из %s: %s", url, error)
        raise RuntimeError(f"Не удалось извлечь ссылки из {url}: {error}") from error

