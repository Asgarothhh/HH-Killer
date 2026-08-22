"""Фильтрация вакансий по локации, опыту и зарплате."""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from src.models.models import JobInfo, ResumeProfile
from src.services.search_query import SearchFilters
from src.utils.location_utils import is_remote_query, job_matches_location
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
        r"(?:of\s+)?(?:experience|doświadczenia|exp\.?)",
        re.I,
    ),
    re.compile(
        r"(\d+(?:[.,]\d+)?)\s*[-–—]\s*(\d+(?:[.,]\d+)?)\s*"
        r"(?:years?|lat|roku|let)",
        re.I,
    ),
    re.compile(r"(\d+(?:[.,]\d+)?)\s*\+", re.I),
)


def parse_job_required_experience(text: str) -> Tuple[Optional[float], Optional[float]]:
    """
    Извлекает требуемый опыт из описания вакансии.
    Возвращает (min_years, max_years); max может быть None для «3+».
    """
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
        val = float(groups[0].replace(",", "."))
        if "+" in match.group(0) or "minimum" in match.group(0).lower():
            return val, None
        return val, val

    return None, None


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
    """Фильтр по опыту: кандидат vs требования вакансии."""
    candidate_years = _candidate_experience_years(filters, profile)
    if candidate_years is None and not filters:
        return True

    text = f"{job.title} {job.description}"
    req_min, req_max = parse_job_required_experience(text)

    # Фильтр «ищу вакансии уровня X–Y лет»
    if filters:
        if filters.experience_job_min is not None and req_min is not None:
            if req_min < filters.experience_job_min - 0.5:
                return False
        if filters.experience_job_max is not None:
            effective_max = req_max if req_max is not None else req_min
            if effective_max is not None and effective_max > filters.experience_job_max + 0.5:
                return False

    # Кандидат с Y лет — отсекаем вакансии, требующие значительно больше
    if candidate_years is not None and req_min is not None:
        tolerance = 1.5
        if req_min > candidate_years + tolerance:
            return False

    return True


def apply_search_filters(
    jobs: List[JobInfo],
    filters: SearchFilters | None,
    profile: ResumeProfile | None = None,
) -> List[JobInfo]:
    """Жёсткая фильтрация перед scoring."""
    if not jobs:
        return []

    result: List[JobInfo] = []
    for job in jobs:
        if filters and filters.city and not job_matches_location(job, filters.city):
            logger.debug("Отфильтровано по локации: %s", job.title[:50])
            continue
        if not job_matches_experience(job, filters, profile):
            logger.debug("Отфильтровано по опыту: %s", job.title[:50])
            continue
        result.append(job)

    if filters and filters.city and len(result) < len(jobs):
        logger.info(
            "Фильтр локации «%s»: %d → %d вакансий",
            filters.city, len(jobs), len(result),
        )

    return result
