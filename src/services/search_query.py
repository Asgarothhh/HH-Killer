"""Сборка поискового запроса и фильтров поиска."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from src.models.models import ResumeProfile, parse_experience_years
from src.utils.location_utils import (
    ResolvedRegion,
    WorkFormat,
    region_display,
    resolve_region,
)

DEFAULT_REGION = "Polska"

# Стоп-слова, которые не должны попадать в keyword-параметр поиска
_QUERY_NOISE = re.compile(
    r"\b(?:ваканси\w*|работа|поиск|ищу|хочу|нужна|нужен|please|looking\s+for|"
    r"job|jobs|position|praca|oferta|удал[её]нк\w*|remote|hybrid|гибрид\w*|"
    r"опыт|experience|зарплата|salary|от|до|лет|years?|в\s+городе)\b",
    re.I,
)


@dataclass(frozen=True)
class EmploymentType:
    key: str
    label: str
    keywords: Tuple[str, ...]
    linkedin_code: Optional[str] = None
    indeed_code: Optional[str] = None


EMPLOYMENT_TYPES: Tuple[EmploymentType, ...] = (
    EmploymentType(
        "full_time", "Полная занятость",
        ("full time", "full-time", "pelny etat", "pełny etat", "полная занятость",
         "полный день", "umowa o prace", "umowa o pracę", "stała praca"),
        "F", "fulltime",
    ),
    EmploymentType(
        "part_time", "Частичная занятость",
        ("part time", "part-time", "pol etatu", "pół etatu", "czesciowy etat",
         "частичная занятость", "неполный день"),
        "P", "parttime",
    ),
    EmploymentType(
        "contract", "Контракт / B2B",
        ("contract", "b2b", "kontrakt", "umowa zlecenie", "umowa o dzielo",
         "umowa o dzieło", "freelance", "самозанятость", "подряд", "контракт"),
        "C", "contract",
    ),
    EmploymentType(
        "internship", "Стажировка",
        ("internship", "intern", "staz", "staż", "praktyki", "стажировка",
         "практика", "trainee"),
        "I", "internship",
    ),
    EmploymentType(
        "temporary", "Временная",
        ("temporary", "tymczasowa", "sezonowa", "временная", "проектная"),
        "T", "temporary",
    ),
)

EMPLOYMENT_BY_KEY: Dict[str, EmploymentType] = {e.key: e for e in EMPLOYMENT_TYPES}

WORK_FORMAT_LABELS: Dict[str, str] = {
    WorkFormat.ANY.value: "Любой",
    WorkFormat.ONSITE.value: "Офис",
    WorkFormat.HYBRID.value: "Гибрид",
    WorkFormat.REMOTE.value: "Удалённо",
}

FRESHNESS_LABELS: Dict[int, str] = {
    1: "за сутки",
    3: "за 3 дня",
    7: "за неделю",
    30: "за месяц",
}


@dataclass
class SearchFilters:
    """Фильтры поиска. Регион обязателен — поиск идёт строго по нему."""

    region: Optional[str] = None
    work_format: str = WorkFormat.ANY.value
    employment_types: Tuple[str, ...] = ()
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    currency: str = "PLN"
    experience_years: Optional[float] = None
    experience_job_min: Optional[float] = None
    experience_job_max: Optional[float] = None
    posted_within_days: Optional[int] = None

    # ── Регион ──────────────────────────────────────────────────────
    @property
    def city(self) -> Optional[str]:
        """Совместимость с прежним названием поля."""
        return self.region

    @property
    def resolved_region(self) -> Optional[ResolvedRegion]:
        return resolve_region(self.region or "")

    @property
    def region_label(self) -> str:
        region = self.resolved_region
        if region:
            return region.label
        return self.region or DEFAULT_REGION

    def location_for_url(self) -> str:
        return region_display(self.region, default=DEFAULT_REGION)

    def location_for_apify(self) -> str:
        return self.location_for_url()

    # ── Формат работы ───────────────────────────────────────────────
    @property
    def format_enum(self) -> WorkFormat:
        try:
            return WorkFormat(self.work_format)
        except ValueError:
            return WorkFormat.ANY

    @property
    def allow_remote(self) -> bool:
        """Разрешает ли пользователь вакансии без привязки к региону."""
        return self.format_enum in {WorkFormat.REMOTE, WorkFormat.HYBRID}

    @property
    def format_label(self) -> Optional[str]:
        if self.format_enum == WorkFormat.ANY:
            return None
        return WORK_FORMAT_LABELS[self.format_enum.value]

    # ── Занятость ───────────────────────────────────────────────────
    @property
    def employment_label(self) -> Optional[str]:
        labels = [
            EMPLOYMENT_BY_KEY[key].label
            for key in self.employment_types
            if key in EMPLOYMENT_BY_KEY
        ]
        return " / ".join(labels) if labels else None

    # ── Зарплата и опыт ─────────────────────────────────────────────
    @property
    def salary_label(self) -> Optional[str]:
        def fmt(value: int) -> str:
            return f"{value:,}".replace(",", " ")

        if self.salary_min and self.salary_max:
            return f"{fmt(self.salary_min)}–{fmt(self.salary_max)} {self.currency}"
        if self.salary_min:
            return f"от {fmt(self.salary_min)} {self.currency}"
        if self.salary_max:
            return f"до {fmt(self.salary_max)} {self.currency}"
        return None

    @property
    def experience_label(self) -> Optional[str]:
        if self.experience_years is not None:
            base = f"{self.experience_years:g} лет"
            jmin, jmax = self.experience_job_min, self.experience_job_max
            if jmin is not None and jmax is not None:
                return f"{base} · вакансии {jmin:g}–{jmax:g} лет"
            if jmin is not None:
                return f"{base} · вакансии от {jmin:g} лет"
            if jmax is not None:
                return f"{base} · вакансии до {jmax:g} лет"
            return base
        if self.experience_job_min is not None and self.experience_job_max is not None:
            return f"вакансии {self.experience_job_min:g}–{self.experience_job_max:g} лет"
        if self.experience_job_min is not None:
            return f"вакансии от {self.experience_job_min:g} лет"
        if self.experience_job_max is not None:
            return f"вакансии до {self.experience_job_max:g} лет"
        return None

    @property
    def freshness_label(self) -> Optional[str]:
        if self.posted_within_days is None:
            return None
        return FRESHNESS_LABELS.get(self.posted_within_days, f"за {self.posted_within_days} дн.")

    # ── Сводки ──────────────────────────────────────────────────────
    def active_labels(self) -> List[str]:
        labels = [f"📍 {self.region_label}"]
        for emoji, value in (
            ("🏢", self.format_label),
            ("💼", self.employment_label),
            ("💰", self.salary_label),
            ("📈", self.experience_label),
            ("🕒", self.freshness_label),
        ):
            if value:
                labels.append(f"{emoji} {value}")
        return labels

    def summary_line(self) -> str:
        return " · ".join(self.active_labels())


def parse_salary(text: str) -> tuple[Optional[int], Optional[int], str]:
    """Парсит «8000-12000», «10k PLN», «от 9000»."""
    raw = (text or "").strip()
    lower = raw.lower()
    currency = "PLN"
    if "eur" in lower or "€" in raw:
        currency = "EUR"
    elif "usd" in lower or "$" in raw:
        currency = "USD"

    nums: List[int] = []
    for match in re.finditer(r"(\d+(?:[.,]\d+)?)\s*(k|K|tys)?", raw.replace(" ", "")):
        value = float(match.group(1).replace(",", "."))
        if match.group(2):
            value *= 1000
        nums.append(int(value))

    if len(nums) >= 2:
        return min(nums[0], nums[1]), max(nums[0], nums[1]), currency
    if len(nums) == 1:
        if any(word in lower for word in ("от", "from", "min", "+")):
            return nums[0], None, currency
        if any(word in lower for word in ("до", "to", "max")):
            return None, nums[0], currency
        return nums[0], None, currency
    return None, None, currency


def parse_experience(text: str) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Парсит опыт: «6», «6+», «3-5», «от 3 лет», «вакансии 2-4».
    Возвращает (опыт кандидата, минимум вакансии, максимум вакансии).
    """
    raw = (text or "").strip()
    lower = raw.lower()

    user_years: Optional[float] = None
    job_min: Optional[float] = None
    job_max: Optional[float] = None

    job_range = re.search(
        r"(?:ваканси|job|position|уровень|level)[^\d]*(\d+(?:[.,]\d+)?)\s*[-–—]\s*(\d+(?:[.,]\d+)?)",
        lower,
    )
    if job_range:
        job_min = float(job_range.group(1).replace(",", "."))
        job_max = float(job_range.group(2).replace(",", "."))

    numbers = [float(n.replace(",", ".")) for n in re.findall(r"(\d+(?:[.,]\d+)?)", raw)]

    if not job_range and len(numbers) >= 2 and re.search(r"[-–—]", raw):
        job_min = min(numbers[0], numbers[1])
        job_max = max(numbers[0], numbers[1])
        numbers = numbers[2:]

    if numbers:
        if any(w in lower for w in ("мой", "мне", "у меня", "i have", "my experience", "опыт:")):
            user_years = numbers[0]
        elif job_min is None and len(numbers) == 1:
            if any(w in lower for w in ("до", "to", "max")) and "+" not in raw:
                job_max = numbers[0]
            else:
                user_years = numbers[0]
        elif job_min is None:
            user_years = numbers[0]

    if user_years is None and job_min is None and job_max is None:
        user_years = parse_experience_years(raw)

    return user_years, job_min, job_max


def parse_employment_types(text: str) -> Tuple[str, ...]:
    """Распознаёт тип занятости в свободном тексте."""
    folded = (text or "").lower()
    found = [
        emp.key
        for emp in EMPLOYMENT_TYPES
        if any(keyword in folded for keyword in emp.keywords)
    ]
    return tuple(dict.fromkeys(found))


def detect_employment_type(text: str) -> Optional[str]:
    """Ключ типа занятости для текста вакансии (первое совпадение)."""
    types = parse_employment_types(text)
    return types[0] if types else None


def build_role_query(
    preference: str,
    profile: Optional[ResumeProfile] = None,
    max_words: int = 5,
) -> str:
    """
    Короткий keyword-запрос для URL job-сайта.

    Длинные описания и списки навыков дают ноль результатов в поиске сайтов,
    поэтому в URL уходит только должность.
    """
    if profile and profile.desired_role:
        source = profile.desired_role
    else:
        source = (preference or "").strip()
        source = source.split("\n")[0]
        source = re.split(r"[.;]|,\s", source)[0]

    cleaned = _QUERY_NOISE.sub(" ", source)
    cleaned = re.sub(r"[^\w\s+#/.-]", " ", cleaned, flags=re.UNICODE)
    words = [w for w in cleaned.split() if len(w) > 1]

    if not words:
        words = [w for w in re.split(r"\W+", preference or "") if len(w) > 2][:max_words]

    return " ".join(words[:max_words]).strip()


def build_role_queries(
    preference: str,
    profile: Optional[ResumeProfile] = None,
) -> List[str]:
    """Основной запрос + более короткий вариант, если основной не дал вакансий."""
    primary = build_role_query(preference, profile)
    variants = [primary]

    words = primary.split()
    if len(words) > 2:
        variants.append(" ".join(words[:2]))

    if profile and profile.desired_role and profile.desired_role.strip() not in variants:
        variants.append(build_role_query(profile.desired_role))

    return [v for v in dict.fromkeys(variants) if v]


def build_matching_context(preference: str, filters: SearchFilters | None = None) -> str:
    """Контекст для LLM-извлечения и оценки релевантности."""
    parts = [(preference or "").strip()]
    if not filters:
        return parts[0]

    parts.append(f"Регион (строго): {filters.region_label}")

    if filters.format_label:
        parts.append(f"Формат работы: {filters.format_label}")
    if filters.employment_label:
        parts.append(f"Занятость: {filters.employment_label}")
    if filters.experience_label:
        parts.append(f"Опыт: {filters.experience_label}")
    if filters.salary_label:
        parts.append(f"Зарплата: {filters.salary_label}")
    if filters.freshness_label:
        parts.append(f"Свежесть: {filters.freshness_label}")

    return ". ".join(p for p in parts if p)
