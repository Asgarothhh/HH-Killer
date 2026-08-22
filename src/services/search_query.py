"""Сборка поискового запроса с опциональными фильтрами."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from src.models.models import parse_experience_years
from src.utils.location_utils import normalize_location_for_url


@dataclass
class SearchFilters:
    city: Optional[str] = None
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    currency: str = "PLN"
    experience_years: Optional[float] = None
    experience_job_min: Optional[float] = None
    experience_job_max: Optional[float] = None

    @property
    def salary_label(self) -> Optional[str]:
        if self.salary_min and self.salary_max:
            return f"{self.salary_min:,}–{self.salary_max:,} {self.currency}".replace(",", " ")
        if self.salary_min:
            return f"от {self.salary_min:,} {self.currency}".replace(",", " ")
        if self.salary_max:
            return f"до {self.salary_max:,} {self.currency}".replace(",", " ")
        return None

    @property
    def experience_label(self) -> Optional[str]:
        if self.experience_years is not None:
            base = f"{self.experience_years:g} лет"
            if self.experience_job_min is not None or self.experience_job_max is not None:
                jmin = self.experience_job_min
                jmax = self.experience_job_max
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

    def location_for_apify(self) -> str:
        if self.city:
            return normalize_location_for_url(self.city)
        return "Polska"

    def location_for_url(self) -> str:
        return self.location_for_apify()


def parse_salary(text: str) -> tuple[Optional[int], Optional[int], str]:
    """Парсит '8000-12000', '10k PLN', 'от 9000'."""
    raw = text.strip()
    currency = "PLN"
    if "eur" in raw.lower() or "€" in raw:
        currency = "EUR"
    elif "usd" in raw.lower() or "$" in raw:
        currency = "USD"

    nums = []
    for match in re.finditer(r"(\d+(?:[.,]\d+)?)\s*(k|K|tys)?", raw):
        val = float(match.group(1).replace(",", "."))
        if match.group(2):
            val *= 1000
        nums.append(int(val))

    if len(nums) >= 2:
        return min(nums[0], nums[1]), max(nums[0], nums[1]), currency
    if len(nums) == 1:
        lower = raw.lower()
        if any(w in lower for w in ("от", "from", "min", "+")):
            return nums[0], None, currency
        if any(w in lower for w in ("до", "to", "max")):
            return None, nums[0], currency
        return nums[0], nums[0], currency
    return None, None, currency


def parse_experience(text: str) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Парсит опыт: «6», «6+», «3-5», «от 3 лет», «вакансии 2-4».
    Возвращает (user_years, job_min, job_max).
    """
    raw = text.strip()
    lower = raw.lower()

    user_years: Optional[float] = None
    job_min: Optional[float] = None
    job_max: Optional[float] = None

    # «вакансии 2-4» / «jobs 3-5 years»
    job_range = re.search(
        r"(?:ваканси|job|position|уровень|level)[^\d]*(\d+(?:[.,]\d+)?)\s*[-–—]\s*(\d+(?:[.,]\d+)?)",
        lower,
    )
    if job_range:
        job_min = float(job_range.group(1).replace(",", "."))
        job_max = float(job_range.group(2).replace(",", "."))

    nums = re.findall(r"(\d+(?:[.,]\d+)?)", raw)
    floats = [float(n.replace(",", ".")) for n in nums]

    if not job_range and len(floats) >= 2 and re.search(r"[-–—]", raw):
        job_min = min(floats[0], floats[1])
        job_max = max(floats[0], floats[1])
        floats = floats[2:]

    if floats:
        if any(w in lower for w in ("мой", "мне", "у меня", "i have", "my experience", "опыт:")):
            user_years = floats[0]
        elif job_min is None and len(floats) == 1:
            if "+" in raw or any(w in lower for w in ("от", "from", "min")):
                user_years = floats[0]
            elif any(w in lower for w in ("до", "to", "max")):
                job_max = floats[0]
            else:
                user_years = floats[0]
        elif job_min is None:
            user_years = floats[0]

    # Fallback через общий парсер
    if user_years is None and not job_min:
        parsed = parse_experience_years(raw)
        if parsed is not None:
            user_years = parsed

    return user_years, job_min, job_max


def build_role_query(preference: str) -> str:
    """Запрос для URL (без города — город передаётся отдельным параметром)."""
    return preference.strip()


def build_matching_context(preference: str, filters: SearchFilters | None = None) -> str:
    """Контекст для LLM-оценки и извлечения вакансий."""
    parts = [preference.strip()]
    if not filters:
        return parts[0]

    if filters.city:
        parts.append(f"Локация: только {filters.city.strip()}")

    if filters.experience_label:
        parts.append(f"Опыт: {filters.experience_label}")

    if filters.salary_label:
        parts.append(f"Зарплата: {filters.salary_label}")

    return ". ".join(p for p in parts if p)


def build_enriched_query(preference: str, filters: SearchFilters | None = None) -> str:
    """Обратная совместимость — полный контекст для matching."""
    return build_matching_context(preference, filters)
