"""Паттерны URL для job-сайтов, пресеты и стратегии scraping."""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote_plus, unquote, urlencode, urlparse


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
        search_url_template="https://hh.ru/search/vacancy?text={query}+{location}&search_field=name&search_field=description",
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
        # Реальная вакансия: /praca/<slug>,oferta,<id>. Всё остальное — фильтры выдачи
        # вида /praca/python;kw/warszawa;wp
        job_detail_patterns=_patterns(r",oferta,\d+", r"/oferta/\d+"),
        listing_patterns=_patterns(r"/praca(\?|$|/)", r",oferty"),
        search_url_template="https://www.pracuj.pl/praca?kw={query}&wp={location}",
    ),
    SiteConfig(
        site_type=SiteType.PRACA,
        name="Praca.pl",
        domains=("praca.pl",),
        # Вакансия: /<slug>_<id>.html или /oferta/<id>
        job_detail_patterns=_patterns(r"_\d{4,}\.html", r"/oferta/\d+"),
        listing_patterns=_patterns(r"/oferty/praca", r"/szukaj", r"/praca(\?|$|/)"),
        search_url_template="https://www.praca.pl/szukaj?keyword={query}&location={location}",
    ),
    SiteConfig(
        site_type=SiteType.INFOPRACA,
        name="InfoPraca.pl",
        domains=("infopraca.pl",),
        job_detail_patterns=_patterns(r"/oferta/\d+", r"/praca/[^/?#]*-\d{4,}"),
        listing_patterns=_patterns(r"/praca(\?|$|/)", r"/szukaj"),
        search_url_template="https://www.infopraca.pl/szukaj?query={query}&location={location}",
    ),
    SiteConfig(
        site_type=SiteType.OLX,
        name="OLX Praca",
        domains=("olx.pl",),
        job_detail_patterns=_patterns(r"/oferta/[^/?#]*-CID\d+", r"/oferta/[^/?#]*-ID\w+"),
        listing_patterns=_patterns(r"/praca/q-", r"/praca(\?|$|/)"),
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
        job_detail_patterns=_patterns(r"/oferta/\d+", r"/praca/[^/?#]*-\d{4,}"),
        listing_patterns=_patterns(r"/szukaj", r"/oferty", r"/praca(\?|$|/)"),
        search_url_template="https://www.jobs.pl/szukaj/?q={query}&location={location}",
    ),
    SiteConfig(
        site_type=SiteType.GOLDENLINE,
        name="GoldenLine",
        domains=("goldenline.pl",),
        job_detail_patterns=_patterns(r"/praca/oferta/[^/?#]*\d+", r"/oferta/\d+"),
        listing_patterns=_patterns(r"/praca/szukaj", r"/praca(\?|$|/)"),
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

    # Идентификатор в пути — главный признак конкретной вакансии, а не выдачи
    has_id = bool(re.search(r"\d{4,}|[0-9a-f]{12,}", path_lower))

    listing_hints = (
        "search", "find", "szukaj", "oferty", "results", "q-",
        "поиск", "vacancies", "вакансии", "listing",
    )
    if any(hint in path_lower for hint in listing_hints) and not has_id:
        if "viewjob" not in path_lower and "/desc/" not in path_lower:
            return "job_listing_pages"

    detail_hints = (
        "vacancy", "vacature", "job", "career", "position", "opening",
        "oferta", "ogloszenie", "stanowisko", "praca",
        "ваканс", "работ", "должность", "desc",
    )
    segments = path_lower.strip("/").split("/")
    looks_like_job_section = any(
        seg == hint or seg.startswith(hint)
        for seg in segments
        for hint in detail_hints
    )
    if looks_like_job_section:
        # Без идентификатора это раздел или фильтр выдачи (/praca/warszawa),
        # а не отдельная вакансия.
        return "job_detail_links" if has_id else "job_listing_pages"

    nav_hints = ("page=", "p=", "offset=", "cursor=", "start=", "from=")
    if any(hint in url.lower() for hint in nav_hints):
        return "navigation_links"

    return None


# Служебные слова в путях job-сайтов — не несут смысла запроса
_SCOPE_STOPWORDS = frozenset({
    "praca", "prace", "pracy", "oferty", "oferta", "jobs", "job", "vacancy",
    "vacancies", "search", "szukaj", "wyszukiwanie", "results", "listing",
    "kw", "wp", "location", "loc", "en", "pl", "ru", "d", "q", "www",
    "вакансии", "вакансия", "поиск", "работа",
})

# Параметры, в которых сайты передают поисковый запрос
_QUERY_PARAMS = ("kw", "q", "query", "keyword", "keywords", "text", "search", "s")


def search_scope_tokens(seed_url: str) -> Tuple[str, ...]:
    """
    Ключевые слова роли из стартового URL поиска.

    Нужны, чтобы обход не сползал на общую выдачу сайта: страницы без роли
    («все вакансии Варшавы») дают нерелевантных кандидатов и тратят время
    на разбор каждой вакансии. Если запрос закодирован в пути, берём слова
    пути — тогда в набор попадает и регион.
    """
    from src.utils.location_utils import fold_text

    if not seed_url:
        return ()

    parsed = urlparse(seed_url)
    raw_values: List[str] = []

    query = parse_qs(parsed.query)
    for key in _QUERY_PARAMS:
        raw_values.extend(query.get(key, []))

    if not raw_values:
        # Поиск, закодированный в пути: /praca/python-developer;kw/warszawa;wp
        raw_values = [unquote(parsed.path).replace(";", "/")]

    tokens = {
        word
        for value in raw_values
        for word in re.split(r"[^\w]+", fold_text(value))
        if len(word) >= 3 and word not in _SCOPE_STOPWORDS and not word.isdigit()
    }
    return tuple(sorted(tokens))


def preserves_search_scope(url: str, tokens: Tuple[str, ...]) -> bool:
    """Сохраняет ли страница выдачи роль из исходного запроса."""
    from src.utils.location_utils import fold_text

    if not tokens:
        return True
    folded = fold_text(unquote(url))
    return any(token in folded for token in tokens)


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


def _slugify(text: str) -> str:
    from src.utils.location_utils import fold_text

    return re.sub(r"[^\w]+", "-", fold_text(text)).strip("-")


def _linkedin_experience_codes(years: Optional[float]) -> Optional[str]:
    """f_E: 1 стажировка, 2 entry, 3 associate, 4 mid-senior, 5 director."""
    if years is None:
        return None
    if years < 1:
        return "1,2"
    if years < 3:
        return "2,3"
    if years < 7:
        return "3,4"
    return "4,5"


def build_filter_params(site_type: SiteType, filters: Any) -> Dict[str, str]:
    """
    Параметры URL, переносящие фильтры пользователя в поиск самого сайта.

    Поддерживаются только документированные параметры — для остальных сайтов
    фильтры применяются пост-фильтрацией результатов.
    """
    if filters is None:
        return {}

    from src.services.search_query import EMPLOYMENT_BY_KEY
    from src.utils.location_utils import WorkFormat

    fmt = filters.format_enum
    employment_keys = tuple(getattr(filters, "employment_types", ()) or ())
    employment = [EMPLOYMENT_BY_KEY[k] for k in employment_keys if k in EMPLOYMENT_BY_KEY]
    days = getattr(filters, "posted_within_days", None)
    params: Dict[str, str] = {}

    if site_type == SiteType.LINKEDIN:
        if fmt == WorkFormat.REMOTE:
            params["f_WT"] = "2"
        elif fmt == WorkFormat.HYBRID:
            params["f_WT"] = "3"
        elif fmt == WorkFormat.ONSITE:
            params["f_WT"] = "1"
        codes = [e.linkedin_code for e in employment if e.linkedin_code]
        if codes:
            params["f_JT"] = ",".join(codes)
        exp_codes = _linkedin_experience_codes(filters.experience_years)
        if exp_codes:
            params["f_E"] = exp_codes
        if days:
            params["f_TPR"] = f"r{int(days) * 86400}"
            params["sortBy"] = "DD"

    elif site_type == SiteType.INDEED:
        codes = [e.indeed_code for e in employment if e.indeed_code]
        if codes:
            params["jt"] = codes[0]
        if days:
            params["fromage"] = str(min((1, 3, 7, 14), key=lambda d: abs(d - int(days))))
        if fmt == WorkFormat.REMOTE:
            params["sc"] = "0kf:attr(DSQF7);"
        else:
            params["radius"] = "25"

    elif site_type == SiteType.PRACUJ:
        work_modes = {
            WorkFormat.REMOTE: "home-office",
            WorkFormat.HYBRID: "hybrid",
            WorkFormat.ONSITE: "full-office",
        }
        if fmt in work_modes:
            params["wm"] = work_modes[fmt]
        # Свежесть у pracuj через URL не задаётся (pn — номер страницы выдачи),
        # поэтому дата публикации проверяется пост-фильтром.

    elif site_type in {SiteType.HH, SiteType.HH_POLAND}:
        hh_experience = None
        years = filters.experience_years
        if years is not None:
            if years < 1:
                hh_experience = "noExperience"
            elif years < 3:
                hh_experience = "between1And3"
            elif years < 6:
                hh_experience = "between3And6"
            else:
                hh_experience = "moreThan6"
        if hh_experience:
            params["experience"] = hh_experience
        if filters.salary_min:
            params["salary"] = str(filters.salary_min)
            params["only_with_salary"] = "true"
        if fmt == WorkFormat.REMOTE:
            params["schedule"] = "remote"
        if days:
            params["search_period"] = str(min((1, 3, 7, 30), key=lambda d: abs(d - int(days))))

    return params


def build_search_url(
    site_type: SiteType,
    query: str,
    location: Optional[str] = None,
    filters: Any = None,
) -> Optional[str]:
    from src.utils.location_utils import region_display

    loc_display = region_display(location or "Polska")
    loc_encoded = quote_plus(loc_display)
    loc_slug = _slugify(loc_display)

    for config in SITE_CONFIGS:
        if config.site_type == site_type and config.search_url_template:
            built = config.search_url_template.format(
                query=quote_plus(query),
                slug=_slugify(query),
                location=loc_encoded,
                loc_slug=loc_slug,
            )
            extra = build_filter_params(site_type, filters)
            if extra:
                separator = "&" if "?" in built else "?"
                built = f"{built}{separator}{urlencode(extra, safe=':;,')}"
            return built
    return None


def build_search_urls_for_site(
    url: str,
    query: str,
    location: Optional[str] = None,
    filters: Any = None,
) -> List[str]:
    """Возвращает URL для поиска: сгенерированный search URL + исходный."""
    site_type = detect_site_type(url)
    urls: List[str] = []
    built = build_search_url(site_type, query[:80], location=location, filters=filters)
    if built:
        urls.append(built)
    if url not in urls:
        urls.append(url)
    return urls


def list_supported_sites_text() -> str:
    names = [c.name for c in SITE_CONFIGS]
    return ", ".join(names) + ", а также любые другие job-порталы"
