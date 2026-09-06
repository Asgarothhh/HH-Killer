"""Жёсткая фильтрация вакансий: регион, формат, занятость, опыт, зарплата, свежесть."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from src.models.models import JobInfo, ResumeProfile, drop_placeholder
from src.services.search_query import (
    EMPLOYMENT_BY_KEY,
    WORK_FORMAT_LABELS,
    SearchFilters,
    detect_employment_type,
)
from src.utils.location_utils import (
    RegionMatch,
    WorkFormat,
    job_work_format,
    match_job_regions,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

EXP_PATTERNS = (
    re.compile(
        r"(?:minimum|min\.?|at least|co najmniej|wymagane|required|"
        r"minimum\s+of|od)\s*(\d+(?:[.,]\d+)?)\s*(?:\+)?\s*"
        r"(?:years?|yrs?\.?|lat|roku|roki|let|years?\s+of\s+experience)",
        re.I,
    ),
    re.compile(
        r"(\d+(?:[.,]\d+)?)\s*\+?\s*(?:years?|yrs?\.?|lat|roku|roki|let)\s*"
        r"(?:of\s+)?(?:experience|doświadczenia|doswiadczenia|exp\.?)",
        re.I,
    ),
    re.compile(
        r"(\d+(?:[.,]\d+)?)\s*[-–—]\s*(\d+(?:[.,]\d+)?)\s*"
        r"(?:years?|lat|roku|let)",
        re.I,
    ),
    re.compile(r"(\d+(?:[.,]\d+)?)\s*\+", re.I),
)

# «8 000 - 12 000 zł», «10-15 tys. PLN», «do 20000 brutto», «€4500/month»
_SALARY_PATTERN = re.compile(
    r"(?P<low>\d[\d\s.,]*)\s*(?:(?P<k1>k|tys\.?|тыс\.?)\s*)?"
    r"(?:[-–—]|do|to)?\s*"
    r"(?P<high>\d[\d\s.,]*)?\s*(?:(?P<k2>k|tys\.?|тыс\.?)\s*)?"
    r"(?P<currency>zł|zl|pln|eur|€|usd|\$|uah|грн|руб|rub)",
    re.I,
)

_CURRENCY_ALIASES: Dict[str, str] = {
    "zł": "PLN", "zl": "PLN", "pln": "PLN",
    "eur": "EUR", "€": "EUR",
    "usd": "USD", "$": "USD",
    "uah": "UAH", "грн": "UAH",
    "руб": "RUB", "rub": "RUB",
}

# Приблизительный курс к PLN — только для сравнения порядка величин
_TO_PLN: Dict[str, float] = {
    "PLN": 1.0, "EUR": 4.3, "USD": 4.0, "UAH": 0.1, "RUB": 0.045,
}

# Допуск при сравнении зарплат: вакансия «немного ниже» не отбрасывается
_SALARY_TOLERANCE = 0.15


DROP_REASON_LABELS: Dict[str, str] = {
    "region": "другой регион",
    "region_unknown": "регион в вакансии не указан",
    "work_format": "другой формат работы",
    "employment": "другой тип занятости",
    "experience": "не подходит по опыту",
    "salary": "зарплата не совпадает с фильтром",
    "freshness": "вакансия старше выбранного срока",
    "relevance": "слабое совпадение с запросом",
}


@dataclass
class DroppedJob:
    """Вакансия, не прошедшая фильтр, с человекочитаемой причиной."""
    job: JobInfo
    reason: str
    detail: str = ""

    @property
    def title(self) -> str:
        return DROP_REASON_LABELS.get(self.reason, self.reason)

    @property
    def explanation(self) -> str:
        if self.detail:
            return f"{self.title} — {self.detail}"
        return self.title


@dataclass
class FilterReport:
    """Сколько вакансий отброшено и почему — для честного ответа пользователю."""
    total: int = 0
    kept: int = 0
    dropped: Dict[str, int] = field(default_factory=dict)

    def drop(self, reason: str) -> None:
        self.dropped[reason] = self.dropped.get(reason, 0) + 1

    @property
    def dropped_total(self) -> int:
        return sum(self.dropped.values())

    def summary(self) -> str:
        parts = [
            f"{DROP_REASON_LABELS.get(reason, reason)}: {count}"
            for reason, count in sorted(self.dropped.items(), key=lambda kv: -kv[1])
            if count
        ]
        return ", ".join(parts)

    def reason_lines(self) -> List[str]:
        """Строки для пользователя: «3 — другой регион»."""
        return [
            f"{count} — {DROP_REASON_LABELS.get(reason, reason)}"
            for reason, count in sorted(self.dropped.items(), key=lambda kv: -kv[1])
            if count
        ]


@dataclass
class FilterOutcome:
    jobs: List[JobInfo]
    report: FilterReport
    dropped: List[DroppedJob] = field(default_factory=list)


def parse_job_required_experience(text: str) -> Tuple[Optional[float], Optional[float]]:
    """Требуемый опыт из описания: (минимум, максимум); максимум None для «3+»."""
    if not text:
        return None, None

    for pattern in EXP_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        groups = match.groups()
        if len(groups) >= 2 and groups[1]:
            low = float(groups[0].replace(",", "."))
            high = float(groups[1].replace(",", "."))
            return min(low, high), max(low, high)
        value = float(groups[0].replace(",", "."))
        if "+" in match.group(0) or "minimum" in match.group(0).lower():
            return value, None
        return value, value

    return None, None


def _to_number(raw: str, multiplier_flag: Optional[str]) -> Optional[float]:
    digits = re.sub(r"[\s.,](?=\d{3}\b)", "", raw.strip())
    digits = digits.replace(" ", "").replace(",", ".")
    try:
        value = float(digits)
    except ValueError:
        return None
    if multiplier_flag:
        value *= 1000
    return value


def parse_job_salary(text: str) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """Извлекает зарплатную вилку из текста вакансии."""
    if not text:
        return None, None, None

    match = _SALARY_PATTERN.search(text)
    if not match:
        return None, None, None

    currency = _CURRENCY_ALIASES.get(match.group("currency").lower())
    low = _to_number(match.group("low"), match.group("k1"))
    high = _to_number(match.group("high"), match.group("k2")) if match.group("high") else None

    if low is not None and high is not None and high < low:
        low, high = high, low

    # Отсекаем явный мусор (годы, номера телефонов)
    if low is not None and low < 100:
        return None, None, currency

    return low, high, currency


def _normalize_to_pln(value: Optional[float], currency: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    rate = _TO_PLN.get((currency or "PLN").upper(), 1.0)
    return value * rate


def _candidate_experience_years(
    filters: SearchFilters | None,
    profile: ResumeProfile | None,
) -> Optional[float]:
    if filters and filters.experience_years is not None:
        return filters.experience_years
    if profile and profile.experience_years is not None:
        return profile.experience_years
    return None


def job_matches_experience(
    job: JobInfo,
    filters: SearchFilters | None,
    profile: ResumeProfile | None,
) -> bool:
    """Опыт кандидата против требований вакансии."""
    candidate_years = _candidate_experience_years(filters, profile)
    text = f"{job.title} {job.description}"
    req_min, req_max = parse_job_required_experience(text)

    if filters:
        if filters.experience_job_min is not None and req_min is not None:
            if req_min < filters.experience_job_min - 0.5:
                return False
        if filters.experience_job_max is not None:
            effective_max = req_max if req_max is not None else req_min
            if effective_max is not None and effective_max > filters.experience_job_max + 0.5:
                return False

    if candidate_years is not None and req_min is not None:
        if req_min > candidate_years + 1.5:
            return False

    return True


def job_matches_work_format(job: JobInfo, filters: SearchFilters | None) -> bool:
    """
    Формат работы. «Удалённо» требует явного подтверждения в вакансии,
    остальные форматы отсекают только явные противоположности.
    """
    if not filters:
        return True

    wanted = filters.format_enum
    if wanted == WorkFormat.ANY:
        return True

    actual = job_work_format(job)

    if wanted == WorkFormat.REMOTE:
        return actual == WorkFormat.REMOTE
    if wanted == WorkFormat.HYBRID:
        return actual in {WorkFormat.HYBRID, WorkFormat.ANY}
    if wanted == WorkFormat.ONSITE:
        return actual in {WorkFormat.ONSITE, WorkFormat.HYBRID, WorkFormat.ANY}
    return True


def job_matches_employment(job: JobInfo, filters: SearchFilters | None) -> bool:
    """Тип занятости: отбрасываем только явное несовпадение."""
    if not filters or not filters.employment_types:
        return True

    wanted = set(filters.employment_types)
    haystack = " ".join(filter(None, (
        job.employment_type or "",
        job.title or "",
        (job.description or "")[:1200],
    )))

    detected = detect_employment_type(haystack)
    if detected is None:
        return True
    return detected in wanted


def job_matches_salary(job: JobInfo, filters: SearchFilters | None) -> bool:
    """Зарплата: отбрасываем только вакансии с явно указанной меньшей суммой."""
    if not filters or (filters.salary_min is None and filters.salary_max is None):
        return True

    haystack = " ".join(filter(None, (
        job.salary_range or "",
        (job.description or "")[:2000],
    )))
    low, high, currency = parse_job_salary(haystack)
    if low is None and high is None:
        return True

    job_low = _normalize_to_pln(low, currency)
    job_high = _normalize_to_pln(high, currency) or job_low
    wanted_low = _normalize_to_pln(filters.salary_min, filters.currency)
    wanted_high = _normalize_to_pln(filters.salary_max, filters.currency)

    if wanted_low is not None and job_high is not None:
        if job_high < wanted_low * (1 - _SALARY_TOLERANCE):
            return False
    if wanted_high is not None and job_low is not None:
        if job_low > wanted_high * (1 + _SALARY_TOLERANCE):
            return False

    return True


def explain_drop(
    reason: str,
    job: JobInfo,
    filters: SearchFilters | None = None,
) -> str:
    """Коротко, почему именно эта вакансия не прошла фильтр."""
    if reason == "region":
        found = drop_placeholder(job.job_location) or "другое место"
        wanted = filters.region_label if filters else "заданный регион"
        return f"в вакансии «{found}», искали {wanted}"
    if reason == "region_unknown":
        wanted = filters.region_label if filters else "заданный регион"
        return f"город не указан, а поиск строго по {wanted}"
    if reason == "work_format":
        actual = WORK_FORMAT_LABELS.get(job_work_format(job).value, "не указан")
        wanted = (filters.format_label if filters else None) or "заданный формат"
        return f"в вакансии: {actual}; фильтр: {wanted}"
    if reason == "employment":
        haystack = " ".join(filter(None, (
            job.employment_type or "",
            job.title or "",
            (job.description or "")[:1200],
        )))
        detected = detect_employment_type(haystack)
        actual = EMPLOYMENT_BY_KEY[detected].label if detected in EMPLOYMENT_BY_KEY else (
            drop_placeholder(job.employment_type) or "другой тип"
        )
        wanted = (filters.employment_label if filters else None) or "выбранный тип"
        return f"в вакансии: {actual}; фильтр: {wanted}"
    if reason == "experience":
        text = f"{job.title} {job.description}"
        req_min, req_max = parse_job_required_experience(text)
        if req_min is not None and req_max is not None and req_min != req_max:
            required = f"{req_min:g}–{req_max:g} лет"
        elif req_min is not None:
            required = f"от {req_min:g} лет"
        else:
            required = "опыт выше фильтра"
        yours = filters.experience_label if filters else None
        return f"вакансия требует {required}" + (f"; у вас {yours}" if yours else "")
    if reason == "salary":
        found = drop_placeholder(job.salary_range) or "сумма ниже фильтра"
        wanted = (filters.salary_label if filters else None) or "заданный диапазон"
        return f"в вакансии: {found}; фильтр: {wanted}"
    if reason == "freshness":
        posted = job.posted_date.strftime("%d.%m.%Y") if job.posted_date else "дата старше фильтра"
        wanted = (filters.freshness_label if filters else None) or "выбранный срок"
        return f"опубликована {posted}; фильтр: {wanted}"
    if reason == "relevance":
        score = job.match_score
        if score is not None:
            return f"оценка {score:.0f} из 100 — ниже порога показа"
        return "слабо совпадает с запросом"
    return DROP_REASON_LABELS.get(reason, reason)


def job_matches_freshness(job: JobInfo, filters: SearchFilters | None) -> bool:
    """Свежесть: отбрасываем только вакансии с известной устаревшей датой."""
    if not filters or not filters.posted_within_days or job.posted_date is None:
        return True
    cutoff = datetime.now() - timedelta(days=filters.posted_within_days + 1)
    return job.posted_date >= cutoff


# LLM иногда транслитерирует валюту — возвращаем исходное написание
_CURRENCY_FIXES = (
    (re.compile(r"\bзл(?:отых)?\b", re.I), "zł"),
    (re.compile(r"\bzlotych\b", re.I), "zł"),
    (re.compile(r"\bевро\b", re.I), "EUR"),
)


def sanitize_job_fields(job: JobInfo) -> JobInfo:
    """
    Убирает искажения, которые LLM вносит при извлечении.

    Показать противоречивую вилку («232333 – 23917») хуже, чем не показать
    зарплату вовсе, поэтому явно испорченные значения удаляются.
    """
    raw = (job.salary_range or "").strip()
    if not raw:
        return job

    fixed = raw
    for pattern, replacement in _CURRENCY_FIXES:
        fixed = pattern.sub(replacement, fixed)

    match = _SALARY_PATTERN.search(fixed)
    corrupted = False

    if match:
        # Порядок как в тексте: у нормальной вилки левая граница меньше правой
        first = _to_number(match.group("low"), match.group("k1"))
        second = (
            _to_number(match.group("high"), match.group("k2"))
            if match.group("high") else None
        )
        if first is not None and second is not None and first > second:
            corrupted = True

        monthly = re.search(r"mies|month|mth|мес", fixed, re.I) is not None
        for value in (first, second):
            if value is None:
                continue
            if value > 2_000_000 or (monthly and value > 150_000):
                corrupted = True

    job.salary_range = None if corrupted else fixed
    if corrupted:
        logger.debug("Зарплата отброшена как испорченная: %r (%s)", raw, job.title[:50])
    return job


def apply_search_filters(
    jobs: List[JobInfo],
    filters: SearchFilters | None,
    profile: ResumeProfile | None = None,
    *,
    strict_region: bool = True,
) -> FilterOutcome:
    """
    Применяет все выбранные фильтры до LLM-оценки.

    Регион проверяется строго: вакансия из другого места — и вакансия,
    для которой регион подтвердить не удалось, — не попадают в ответ.
    """
    report = FilterReport(total=len(jobs))
    if not jobs:
        return FilterOutcome([], report)

    region = filters.resolved_regions if filters else ()
    allow_remote = bool(filters and filters.allow_remote)
    kept: List[JobInfo] = []
    rejected: List[DroppedJob] = []

    def reject(job: JobInfo, reason: str) -> None:
        report.drop(reason)
        rejected.append(DroppedJob(
            job=job,
            reason=reason,
            detail=explain_drop(reason, job, filters),
        ))

    for job in jobs:
        sanitize_job_fields(job)

        if region:
            verdict = match_job_regions(job, region, allow_remote=allow_remote)
            if verdict == RegionMatch.MISMATCH:
                reject(job, "region")
                logger.debug(
                    "Регион не совпал (%s ≠ %s): %s",
                    job.job_location or "—",
                    filters.region_label if filters else "—",
                    job.title[:60],
                )
                continue
            if verdict == RegionMatch.UNKNOWN and strict_region:
                reject(job, "region_unknown")
                logger.debug("Регион не подтверждён: %s", job.title[:60])
                continue

        if not job_matches_work_format(job, filters):
            reject(job, "work_format")
            continue
        if not job_matches_employment(job, filters):
            reject(job, "employment")
            continue
        if not job_matches_experience(job, filters, profile):
            reject(job, "experience")
            continue
        if not job_matches_salary(job, filters):
            reject(job, "salary")
            continue
        if not job_matches_freshness(job, filters):
            reject(job, "freshness")
            continue

        kept.append(job)

    report.kept = len(kept)
    if report.dropped_total:
        logger.info(
            "Фильтры: %d → %d вакансий (%s)",
            report.total, report.kept, report.summary(),
        )
    return FilterOutcome(kept, report, rejected)
