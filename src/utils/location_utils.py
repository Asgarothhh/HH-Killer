"""Нормализация городов и проверка локации вакансий."""

from __future__ import annotations

import re
import unicodedata
from typing import List, Optional, Set

# Каноническое имя → варианты написания (нижний регистр)
CITY_ALIASES: dict[str, tuple[str, ...]] = {
    "warszawa": ("warszawa", "warsaw", "варшава", "warszawa, mazowieckie", "mazowieckie"),
    "krakow": ("kraków", "krakow", "kraków", "краков", "cracow"),
    "wroclaw": ("wrocław", "wroclaw", "вроцлав"),
    "gdansk": ("gdańsk", "gdansk", "гданьск"),
    "poznan": ("poznań", "poznan", "познань"),
    "lodz": ("łódź", "lodz", "лодзь"),
    "katowice": ("katowice", "катовице"),
    "szczecin": ("szczecin", "щецин"),
    "bydgoszcz": ("bydgoszcz", "бидгощ"),
    "lublin": ("lublin", "люблин"),
    "bialystok": ("białystok", "bialystok", "белосток"),
}

REMOTE_KEYWORDS = (
    "remote", "zdalna", "zdalnie", "zdalny", "hybrid", "hybrydowa", "hybrydowo",
    "home office", "work from home", "удалён", "удален", "удалёнка", "удаленка",
    "anywhere", "worldwide", "fully remote", "praca zdalna",
)


def _normalize_text(text: str) -> str:
    lowered = text.strip().lower()
    normalized = unicodedata.normalize("NFKD", lowered)
    return "".join(c for c in normalized if not unicodedata.combining(c))


def is_remote_query(city: str) -> bool:
    norm = _normalize_text(city)
    return any(kw in norm for kw in REMOTE_KEYWORDS)


def resolve_canonical_city(city: str) -> Optional[str]:
    """Возвращает каноническое имя города или None."""
    parts = [_normalize_text(p.strip()) for p in city.split(",") if p.strip()]
    for part in parts:
        for canonical, variants in CITY_ALIASES.items():
            if part == canonical or part in variants:
                return canonical

    norm = _normalize_text(city)
    for canonical, variants in CITY_ALIASES.items():
        if norm == canonical or norm in variants:
            return canonical
        if any(v in norm for v in variants):
            return canonical
    return None


def normalize_location_for_url(city: str) -> str:
    """Имя города для параметров URL (польская/латиница)."""
    if not city or is_remote_query(city):
        return "Polska"

    primary = city.split(",")[0].strip()
    canonical = resolve_canonical_city(primary) or resolve_canonical_city(city)
    display_names = {
        "warszawa": "Warszawa",
        "krakow": "Kraków",
        "wroclaw": "Wrocław",
        "gdansk": "Gdańsk",
        "poznan": "Poznań",
        "lodz": "Łódź",
        "katowice": "Katowice",
        "szczecin": "Szczecin",
        "bydgoszcz": "Bydgoszcz",
        "lublin": "Lublin",
        "bialystok": "Białystok",
    }
    if canonical and canonical in display_names:
        return display_names[canonical]

    return primary.strip() or city.strip()


def city_variants(city: str) -> Set[str]:
    """Все варианты написания города для поиска в тексте."""
    norm = _normalize_text(city)
    variants: Set[str] = {norm, city.strip().lower()}

    canonical = resolve_canonical_city(city)
    if canonical:
        variants.add(canonical)
        variants.update(_normalize_text(v) for v in CITY_ALIASES[canonical])

    # Добавляем слова из ввода пользователя
    for part in re.split(r"[,;/\s]+", norm):
        if len(part) >= 3:
            variants.add(part)

    return {v for v in variants if v}


def job_text_blob(job) -> str:
    parts = [
        getattr(job, "title", "") or "",
        getattr(job, "description", "") or "",
        getattr(job, "job_location", "") or "",
        getattr(job, "company", "") or "",
    ]
    return _normalize_text(" ".join(parts))


def job_matches_location(job, city: str) -> bool:
    """Проверяет, что вакансия соответствует указанному городу."""
    if not city:
        return True

    if is_remote_query(city):
        text = job_text_blob(job)
        return any(_normalize_text(kw) in text for kw in REMOTE_KEYWORDS)

    text = job_text_blob(job)
    variants = city_variants(city)

    if any(v in text for v in variants):
        return True

    # Если локация не указана в вакансии — не отбрасываем жёстко (штраф в scorer)
    location_field = getattr(job, "job_location", None) or ""
    if not location_field.strip():
        return True

    return False
