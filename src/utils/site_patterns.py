"""Паттерны URL для job-сайтов, пресеты и стратегии scraping."""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus, urlparse


class SiteType(str, Enum):
    HH = "hh"
    HH_POLAND = "hh_poland"
    LINKEDIN = "linkedin"
    INDEED = "indeed"
    PRACUJ = "pracuj"
    PRACA = "praca"
    INFOPRACA = "infopraca"
    OLX = "olx"
    ADZUNA = "adzuna"
    JOOBLE = "jooble"
    JOBS_PL = "jobs_pl"
    GOLDENLINE = "goldenline"
    GENERIC = "generic"


class ScrapingBackend(str, Enum):
    """Какой движок использовать для сбора вакансий."""
    PLAYWRIGHT = "playwright"
    APIFY = "apify"
    AUTO = "auto"  # Playwright → Apify fallback


@dataclass(frozen=True)
class SiteConfig:
    site_type: SiteType
    name: str
    domains: Tuple[str, ...]
    job_detail_patterns: Tuple[re.Pattern, ...]
    listing_patterns: Tuple[re.Pattern, ...]
    search_url_template: Optional[str] = None
    prefer_apify: bool = False
    apify_actor_env: Optional[str] = None
    default_backend: ScrapingBackend = ScrapingBackend.PLAYWRIGHT
    indeed_country: Optional[str] = None


def _patterns(*raw: str) -> Tuple[re.Pattern, ...]:
    return tuple(re.compile(p, re.I) for p in raw)


SITE_CONFIGS: Tuple[SiteConfig, ...] = (
    SiteConfig(
        site_type=SiteType.HH,
        name="HeadHunter (RU)",
        domains=("hh.ru", "hh.kz", "hh.uz", "headhunter.ru"),
        job_detail_patterns=_patterns(r"/vacancy/\d+"),
        listing_patterns=_patterns(r"/search/vacancy", r"/vacancies"),
        search_url_template="https://hh.ru/search/vacancy?text={query}&search_field=name&search_field=company_name&search_field=description",
    ),
    SiteConfig(
        site_type=SiteType.HH_POLAND,
        name="HeadHunter (Poland)",
        domains=("poland.hh.ru",),
        job_detail_patterns=_patterns(r"/vacancy/\d+"),
        listing_patterns=_patterns(r"/search/vacancy"),
        search_url_template="https://poland.hh.ru/search/vacancy?text={query}+{location}",
    ),
    SiteConfig(
        site_type=SiteType.LINKEDIN,
        name="LinkedIn",
        domains=("linkedin.com",),
        job_detail_patterns=_patterns(r"/jobs/view/\d+", r"/jobs/view/[^/?#]+"),
        listing_patterns=_patterns(r"/jobs/search", r"/jobs/collections"),
        search_url_template="https://www.linkedin.com/jobs/search/?keywords={query}&location={location}",
        prefer_apify=True,
        apify_actor_env="APIFY_LINKEDIN_ACTOR",
        default_backend=ScrapingBackend.AUTO,
    ),
    SiteConfig(
        site_type=SiteType.INDEED,
        name="Indeed",
        domains=("indeed.com", "indeed.pl", "pl.indeed.com", "www.indeed.pl"),
        job_detail_patterns=_patterns(r"/viewjob", r"/rc/clk", r"jk="),
        listing_patterns=_patterns(r"/jobs\?", r"/jobs$"),
        search_url_template="https://pl.indeed.com/jobs?q={query}&l={location}",
        prefer_apify=True,
        apify_actor_env="APIFY_INDEED_ACTOR",
        default_backend=ScrapingBackend.AUTO,
        indeed_country="PL",
    ),
    SiteConfig(
        site_type=SiteType.PRACUJ,
        name="Pracuj.pl",
        domains=("pracuj.pl",),
        job_detail_patterns=_patterns(r"/praca/[^/?#]+,?\d*", r"/oferta/", r"/oferty/praca/"),
        listing_patterns=_patterns(r"/praca\?", r"/praca$", r"/praca/[^,]+,oferty"),
        search_url_template="https://www.pracuj.pl/praca?kw={query}&wp={location}",
    ),
    SiteConfig(
        site_type=SiteType.PRACA,
        name="Praca.pl",
        domains=("praca.pl",),
        job_detail_patterns=_patterns(r"/oferta/", r"/oferty/praca/[^/?#]+"),
        listing_patterns=_patterns(r"/oferty/praca", r"/szukaj"),
        search_url_template="https://www.praca.pl/szukaj?keyword={query}&location={location}",
    ),
    SiteConfig(
        site_type=SiteType.INFOPRACA,
        name="InfoPraca.pl",
        domains=("infopraca.pl",),
        job_detail_patterns=_patterns(r"/oferta/", r"/praca/[^/?#]+-\d+"),
        listing_patterns=_patterns(r"/praca\?", r"/praca$", r"/szukaj"),
        search_url_template="https://www.infopraca.pl/szukaj?query={query}&location={location}",
    ),
    SiteConfig(
        site_type=SiteType.OLX,
        name="OLX Praca",
        domains=("olx.pl",),
        job_detail_patterns=_patterns(r"/oferta/", r"/d/oferta/"),
        listing_patterns=_patterns(r"/praca/q-", r"/praca/\?", r"/praca$"),
        search_url_template="https://www.olx.pl/praca/q-{slug}-{loc_slug}/",
    ),
    SiteConfig(
        site_type=SiteType.ADZUNA,
        name="Adzuna.pl",
        domains=("adzuna.pl", "adzuna.com"),
        job_detail_patterns=_patterns(r"/details/\d+", r"/land/ad/"),
        listing_patterns=_patterns(r"/search", r"/jobs/search"),
        search_url_template="https://www.adzuna.pl/search?q={query}&loc={location}",
    ),
    SiteConfig(
        site_type=SiteType.JOOBLE,
        name="Jooble.pl",
        domains=("jooble.org", "jooble.pl", "pl.jooble.org"),
        job_detail_patterns=_patterns(r"/desc/", r"/job/", r"/details/"),
        listing_patterns=_patterns(r"/SearchResult", r"/search"),
        search_url_template="https://pl.jooble.org/SearchResult?keywords={query}&location={location}",
    ),
    SiteConfig(
        site_type=SiteType.JOBS_PL,
        name="Jobs.pl",
        domains=("jobs.pl",),
        job_detail_patterns=_patterns(r"/oferta/", r"/praca/[^/?#]+-\d+"),
        listing_patterns=_patterns(r"/szukaj", r"/oferty", r"/praca\?"),
        search_url_template="https://www.jobs.pl/szukaj/?q={query}&location={location}",
    ),
    SiteConfig(
        site_type=SiteType.GOLDENLINE,
        name="GoldenLine",
        domains=("goldenline.pl",),
        job_detail_patterns=_patterns(r"/praca/oferta/", r"/oferta/", r"/job/"),
        listing_patterns=_patterns(r"/praca/szukaj", r"/praca\?", r"/praca$"),
        search_url_template="https://www.goldenline.pl/praca/szukaj/{slug}?location={location}",
    ),
)

# Пресеты для Telegram-кнопок (короткие ключи → URL)
SITE_PRESETS: Dict[str, str] = {
    "hh_ru": "https://hh.ru/",
    "hh_pl": "https://poland.hh.ru/",
    "linkedin": "https://www.linkedin.com/jobs/",
    "indeed_pl": "https://pl.indeed.com/",
    "indeed_com": "https://www.indeed.com/",
    "pracuj": "https://www.pracuj.pl/",
    "praca": "https://www.praca.pl/",
    "infopraca": "https://www.infopraca.pl/",
    "olx": "https://www.olx.pl/praca/",
    "adzuna": "https://www.adzuna.pl/",
    "jooble": "https://pl.jooble.org/",
    "jobs_pl": "https://www.jobs.pl/",
    "goldenline": "https://www.goldenline.pl/praca/",
}

PRESET_LABELS: Dict[str, str] = {
    "hh_ru": "HH.ru",
    "hh_pl": "HH PL",
    "linkedin": "LinkedIn",
    "indeed_pl": "Indeed.pl",
    "indeed_com": "Indeed.com",
    "pracuj": "Pracuj.pl",
    "praca": "Praca.pl",
    "infopraca": "InfoPraca",
    "olx": "OLX Praca",
    "adzuna": "Adzuna",
    "jooble": "Jooble",
    "jobs_pl": "Jobs.pl",
    "goldenline": "GoldenLine",
}

# Порядок отображения в боте
PRESET_GROUPS: Tuple[Tuple[str, ...], ...] = (
    ("hh_ru", "hh_pl", "linkedin", "indeed_pl"),
    ("indeed_com", "pracuj", "praca", "infopraca"),
    ("olx", "adzuna", "jooble", "jobs_pl"),
    ("goldenline",),
)


def _hostname(url: str) -> str:
    return (urlparse(url).hostname or "").lower().lstrip("www.")


def detect_site_type(url: str) -> SiteType:
    host = _hostname(url)
    for config in SITE_CONFIGS:
        if any(host == d or host.endswith("." + d) for d in config.domains):
            return config.site_type
    return SiteType.GENERIC


def get_site_config(url: str) -> Optional[SiteConfig]:
    host = _hostname(url)
    for config in SITE_CONFIGS:
        if any(host == d or host.endswith("." + d) for d in config.domains):
            return config
    return None


def get_site_config_by_type(site_type: SiteType) -> Optional[SiteConfig]:
    for config in SITE_CONFIGS:
        if config.site_type == site_type:
            return config
    return None


def is_supported_job_site(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    if get_site_config(url):
        return True
    return bool(parsed.hostname)


def get_scraping_backend(url: str) -> ScrapingBackend:
    config = get_site_config(url)
    if config:
        return config.default_backend
    return ScrapingBackend.PLAYWRIGHT


def should_try_apify(url: str, apify_available: bool) -> bool:
    if not apify_available:
        return False
    config = get_site_config(url)
    if config and config.prefer_apify:
        return True
    return False


def categorize_link_heuristic(url: str) -> Optional[str]:
    """Быстрая категоризация без LLM."""
    config = get_site_config(url)
    path = urlparse(url).path + "?" + urlparse(url).query

    if config:
        for pattern in config.job_detail_patterns:
            if pattern.search(path):
                return "job_detail_links"
        for pattern in config.listing_patterns:
            if pattern.search(path):
                return "job_listing_pages"

    path_lower = urlparse(url).path.lower()
    listing_hints = (
        "search", "find", "szukaj", "oferty", "results", "q-",
        "поиск", "vacancies", "вакансии", "listing",
    )
    if any(h in path_lower or h in url.lower() for h in listing_hints):
        if "viewjob" not in path_lower and "/desc/" not in path_lower:
            return "job_listing_pages"

    detail_hints = (
        "vacancy", "vacature", "job", "career", "position", "opening",
        "oferta", "ogloszenie", "stanowisko", "praca/",
        "ваканс", "работ", "должность", "desc/",
    )
    segments = path_lower.strip("/").split("/")
    if any(
        seg == hint or seg.startswith(hint.rstrip("/"))
        for seg in segments
        for hint in detail_hints
    ):
        return "job_detail_links"

    nav_hints = ("page=", "p=", "offset=", "cursor=", "start=", "from=")
    if any(h in url.lower() for h in nav_hints):
        return "navigation_links"

    return None


def categorize_links_heuristic(links: List[str]) -> dict:
    result = {
        "job_detail_links": [],
        "job_listing_pages": [],
        "navigation_links": [],
        "uncategorized": [],
    }
    for link in links:
        category = categorize_link_heuristic(link)
        if category:
            result[category].append(link)
        else:
            result["uncategorized"].append(link)
    return result


def build_search_url(
    site_type: SiteType,
    query: str,
    location: Optional[str] = None,
) -> Optional[str]:
    from src.utils.location_utils import normalize_location_for_url

    loc_display = normalize_location_for_url(location or "Polska")
    loc_encoded = quote_plus(loc_display)
    loc_slug = loc_display.strip().replace(" ", "-").lower()

    for config in SITE_CONFIGS:
        if config.site_type == site_type and config.search_url_template:
            encoded = quote_plus(query)
            slug = query.strip().replace(" ", "-").lower()
            return config.search_url_template.format(
                query=encoded,
                slug=slug,
                location=loc_encoded,
                loc_slug=loc_slug,
            )
    return None


def build_search_urls_for_site(
    url: str,
    query: str,
    location: Optional[str] = None,
) -> List[str]:
    """Возвращает URL для поиска: сгенерированный search URL + исходный."""
    site_type = detect_site_type(url)
    urls: List[str] = []
    built = build_search_url(site_type, query[:100], location=location)
    if built:
        urls.append(built)
    if url not in urls:
        urls.append(url)
    return urls


def list_supported_sites_text() -> str:
    names = [c.name for c in SITE_CONFIGS]
    return ", ".join(names) + ", а также любые другие job-порталы"
