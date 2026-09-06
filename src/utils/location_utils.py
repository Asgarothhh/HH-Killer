"""Регионы: разбор пользовательского ввода и строгая проверка локации вакансий."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from typing import FrozenSet, Iterable, Optional, Sequence, Tuple
from urllib.parse import unquote, urlparse


class RegionMatch(str, Enum):
    """Результат сверки вакансии с запрошенным регионом."""
    MATCH = "match"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


class WorkFormat(str, Enum):
    ANY = "any"
    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"


@dataclass(frozen=True)
class RegionEntry:
    key: str
    display: str
    country: str
    kind: str  # "city" | "area" | "country"
    aliases: Tuple[str, ...] = ()


# Каталог регионов. display — написание для URL-параметров job-сайтов.
REGION_CATALOG: Tuple[RegionEntry, ...] = (
    # ── Польша: города ──────────────────────────────────────────────
    RegionEntry("warszawa", "Warszawa", "PL", "city",
                ("warsaw", "варшава", "warszawa", "warszawie", "waw")),
    RegionEntry("krakow", "Kraków", "PL", "city",
                ("krakow", "cracow", "краков", "krakowie")),
    RegionEntry("wroclaw", "Wrocław", "PL", "city",
                ("wroclaw", "breslau", "вроцлав", "wroclawiu")),
    RegionEntry("gdansk", "Gdańsk", "PL", "city",
                ("gdansk", "danzig", "гданьск", "trojmiasto", "tricity")),
    RegionEntry("gdynia", "Gdynia", "PL", "city", ("gdynia", "гдыня")),
    RegionEntry("sopot", "Sopot", "PL", "city", ("sopot", "сопот")),
    RegionEntry("poznan", "Poznań", "PL", "city", ("poznan", "познань", "poznaniu")),
    RegionEntry("lodz", "Łódź", "PL", "city", ("lodz", "лодзь", "lodzi")),
    RegionEntry("katowice", "Katowice", "PL", "city",
                ("katowice", "катовице", "katowicach", "gliwice", "sosnowiec")),
    RegionEntry("szczecin", "Szczecin", "PL", "city", ("szczecin", "щецин")),
    RegionEntry("bydgoszcz", "Bydgoszcz", "PL", "city", ("bydgoszcz", "бидгощ")),
    RegionEntry("lublin", "Lublin", "PL", "city", ("lublin", "люблин", "lublinie")),
    RegionEntry("bialystok", "Białystok", "PL", "city", ("bialystok", "белосток")),
    RegionEntry("rzeszow", "Rzeszów", "PL", "city", ("rzeszow", "жешув")),
    RegionEntry("torun", "Toruń", "PL", "city", ("torun", "торунь")),
    RegionEntry("kielce", "Kielce", "PL", "city", ("kielce", "кельце")),
    RegionEntry("olsztyn", "Olsztyn", "PL", "city", ("olsztyn", "ольштын")),
    RegionEntry("opole", "Opole", "PL", "city", ("opole", "ополе")),
    RegionEntry("zielona-gora", "Zielona Góra", "PL", "city",
                ("zielona gora", "зелёна-гура")),

    # ── Польша: воеводства ──────────────────────────────────────────
    RegionEntry("mazowieckie", "Mazowieckie", "PL", "area",
                ("mazowieckie", "mazowsze", "мазовецкое")),
    RegionEntry("malopolskie", "Małopolskie", "PL", "area",
                ("malopolskie", "малопольское")),
    RegionEntry("dolnoslaskie", "Dolnośląskie", "PL", "area",
                ("dolnoslaskie", "нижнесилезское")),
    RegionEntry("pomorskie", "Pomorskie", "PL", "area", ("pomorskie", "поморское")),
    RegionEntry("slaskie", "Śląskie", "PL", "area", ("slaskie", "силезское")),
    RegionEntry("wielkopolskie", "Wielkopolskie", "PL", "area",
                ("wielkopolskie", "великопольское")),

    # ── Страны ──────────────────────────────────────────────────────
    RegionEntry("polska", "Polska", "PL", "country",
                ("polska", "poland", "польша", "polsce", "pl", "cala polska")),
    RegionEntry("germany", "Deutschland", "DE", "country",
                ("germany", "deutschland", "niemcy", "германия", "de")),
    RegionEntry("czechia", "Česko", "CZ", "country",
                ("czechia", "czechy", "czech republic", "чехия", "cesko")),
    RegionEntry("ukraine", "Україна", "UA", "country",
                ("ukraine", "ukraina", "украина", "україна", "ua")),
    RegionEntry("lithuania", "Lietuva", "LT", "country",
                ("lithuania", "litwa", "литва", "lietuva")),
    RegionEntry("netherlands", "Nederland", "NL", "country",
                ("netherlands", "holandia", "нидерланды", "holland", "nederland")),
    RegionEntry("uk", "United Kingdom", "GB", "country",
                ("united kingdom", "uk", "great britain", "wielka brytania",
                 "великобритания", "england")),
    RegionEntry("usa", "United States", "US", "country",
                ("united states", "usa", "us", "сша", "stany zjednoczone")),
    RegionEntry("russia", "Россия", "RU", "country",
                ("russia", "россия", "рф", "rosja", "ru")),
    RegionEntry("kazakhstan", "Қазақстан", "KZ", "country",
                ("kazakhstan", "казахстан", "kazachstan", "kz")),
    RegionEntry("belarus", "Беларусь", "BY", "country",
                ("belarus", "беларусь", "белоруссия", "bialorus", "by")),

    # ── Прочие крупные города ───────────────────────────────────────
    RegionEntry("berlin", "Berlin", "DE", "city", ("berlin", "берлин")),
    RegionEntry("munich", "München", "DE", "city", ("munich", "munchen", "мюнхен")),
    RegionEntry("prague", "Praha", "CZ", "city", ("prague", "praha", "прага", "praga")),
    RegionEntry("vilnius", "Vilnius", "LT", "city", ("vilnius", "вильнюс", "wilno")),
    RegionEntry("kyiv", "Київ", "UA", "city", ("kyiv", "kiev", "киев", "київ")),
    RegionEntry("lviv", "Львів", "UA", "city", ("lviv", "lwow", "львов", "львів")),
    RegionEntry("minsk", "Минск", "BY", "city", ("minsk", "минск", "miensk")),
    RegionEntry("brest", "Брест", "BY", "city", ("brest", "брест", "brzesc", "brześć")),
    RegionEntry("gomel", "Гомель", "BY", "city", ("gomel", "homel", "гомель")),
    RegionEntry("grodno", "Гродно", "BY", "city", ("grodno", "hrodna", "гродно")),
    RegionEntry("vitebsk", "Витебск", "BY", "city", ("vitebsk", "витебск")),
    RegionEntry("mogilev", "Могилёв", "BY", "city",
                ("mogilev", "mogilyov", "могилев", "могилёв")),
    RegionEntry("moscow", "Москва", "RU", "city", ("moscow", "москва", "мск", "moskwa")),
    RegionEntry("spb", "Санкт-Петербург", "RU", "city",
                ("saint petersburg", "st petersburg", "санкт-петербург", "спб", "питер")),
    RegionEntry("almaty", "Алматы", "KZ", "city", ("almaty", "алматы", "алма-ата")),
    RegionEntry("astana", "Астана", "KZ", "city", ("astana", "астана", "нур-султан")),
    RegionEntry("amsterdam", "Amsterdam", "NL", "city", ("amsterdam", "амстердам")),
    RegionEntry("london", "London", "GB", "city", ("london", "лондон")),
)

REMOTE_KEYWORDS: Tuple[str, ...] = (
    "remote", "fully remote", "remote first", "praca zdalna", "zdalna", "zdalnie",
    "zdalny", "home office", "work from home", "wfh", "anywhere", "worldwide",
    "удаленно", "удаленная", "удаленка", "дистанционно", "из дома",
)

HYBRID_KEYWORDS: Tuple[str, ...] = (
    "hybrid", "hybryda", "hybrydowa", "hybrydowo", "praca hybrydowa", "гибрид",
    "гибридный", "частично удаленно",
)

ONSITE_KEYWORDS: Tuple[str, ...] = (
    "on-site", "onsite", "stacjonarna", "stacjonarnie", "w biurze", "в офисе",
    "office based",
)

# Слова-заполнители, которые нельзя использовать как признак региона
_LOCATION_NOISE: FrozenSet[str] = frozenset({
    "praca", "job", "jobs", "oferta", "oferty", "work", "vacancy", "вакансия",
    "location", "lokalizacja", "miejsce", "city", "miasto", "город", "regionie",
    "region", "area", "obszar", "wiele", "various", "multiple", "different",
})

# Сколько городов можно выбрать одновременно
MAX_REGIONS = 5

# «Брест, Минск и Москва» / «Brest / Minsk / Moscow»
_REGION_SPLIT = re.compile(
    r"\s*(?:,|;|/|\||\n|\+| и | and | или | or )\s*",
    re.I,
)


def _fold(text: str) -> str:
    """Нижний регистр + удаление диакритики (ą→a, ł→l, ż→z)."""
    lowered = (text or "").strip().lower().replace("ł", "l").replace("Ł", "l")
    decomposed = unicodedata.normalize("NFKD", lowered)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped)


def fold_text(text: str) -> str:
    """Публичная нормализация: нижний регистр без диакритики."""
    return _fold(text)


def _word_pattern(tokens: Iterable[str]) -> Optional[re.Pattern]:
    parts = sorted({re.escape(t) for t in tokens if t}, key=len, reverse=True)
    if not parts:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)", re.UNICODE)


@lru_cache(maxsize=1)
def _catalog_patterns() -> Tuple[Tuple[RegionEntry, re.Pattern], ...]:
    result = []
    for entry in REGION_CATALOG:
        tokens = {_fold(entry.key.replace("-", " ")), _fold(entry.display)}
        tokens.update(_fold(a) for a in entry.aliases)
        # Односимвольные/двухсимвольные коды стран (pl, de) исключаем из
        # текстового поиска — слишком много ложных срабатываний.
        tokens = {t for t in tokens if len(t) >= 3}
        pattern = _word_pattern(tokens)
        if pattern:
            result.append((entry, pattern))
    return tuple(result)


_REMOTE_PATTERN = _word_pattern(_fold(k) for k in REMOTE_KEYWORDS)
_HYBRID_PATTERN = _word_pattern(_fold(k) for k in HYBRID_KEYWORDS)
_ONSITE_PATTERN = _word_pattern(_fold(k) for k in ONSITE_KEYWORDS)


@dataclass(frozen=True)
class ResolvedRegion:
    """Разобранный регион поиска."""
    query: str
    display: str
    key: str
    kind: str
    country: Optional[str] = None
    aliases: FrozenSet[str] = field(default_factory=frozenset)

    @property
    def is_known(self) -> bool:
        return self.kind != "custom"

    @property
    def label(self) -> str:
        return self.display or self.query

    def matcher(self) -> Optional[re.Pattern]:
        return _word_pattern(self.aliases)


def is_remote_text(text: str) -> bool:
    return bool(_REMOTE_PATTERN and _REMOTE_PATTERN.search(_fold(text)))


def is_hybrid_text(text: str) -> bool:
    return bool(_HYBRID_PATTERN and _HYBRID_PATTERN.search(_fold(text)))


def is_onsite_text(text: str) -> bool:
    return bool(_ONSITE_PATTERN and _ONSITE_PATTERN.search(_fold(text)))


def find_region_entry(text: str) -> Optional[RegionEntry]:
    """Находит регион из каталога по любому написанию."""
    folded = _fold(text)
    if not folded:
        return None

    for entry, pattern in _catalog_patterns():
        if pattern.search(folded):
            return entry
    return None


def find_region_entries(text: str) -> Tuple[RegionEntry, ...]:
    """Все регионы каталога, упомянутые в тексте."""
    folded = _fold(text)
    if not folded:
        return ()
    return tuple(entry for entry, pattern in _catalog_patterns() if pattern.search(folded))


@lru_cache(maxsize=256)
def resolve_region(text: str) -> Optional[ResolvedRegion]:
    """
    Разбирает пользовательский ввод региона.

    Возвращает `None` для пустого ввода и для «remote» без указания места —
    формат работы задаётся отдельным фильтром, а не регионом.
    """
    raw = (text or "").strip()
    if not raw:
        return None

    entry = find_region_entry(raw)

    if entry is None and is_remote_text(raw):
        return None

    if entry is not None:
        aliases = {_fold(entry.key.replace("-", " ")), _fold(entry.display)}
        aliases.update(_fold(a) for a in entry.aliases)
        aliases = {a for a in aliases if len(a) >= 3}
        return ResolvedRegion(
            query=raw,
            display=entry.display,
            key=entry.key,
            kind=entry.kind,
            country=entry.country,
            aliases=frozenset(aliases),
        )

    # Неизвестный регион: используем ввод пользователя как есть.
    primary = raw.split(",")[0].strip() or raw
    tokens = {
        _fold(primary),
        _fold(raw),
        *(t for t in re.split(r"[\s/;]+", _fold(primary)) if len(t) >= 4),
    }
    tokens = {t for t in tokens if len(t) >= 3 and t not in _LOCATION_NOISE}
    return ResolvedRegion(
        query=raw,
        display=primary,
        key=_fold(primary).replace(" ", "-"),
        kind="custom",
        country=None,
        aliases=frozenset(tokens),
    )


def region_display(text: Optional[str], default: str = "Polska") -> str:
    """Написание региона для URL-параметров job-сайтов."""
    region = resolve_region(text or "")
    return region.display if region else default


def region_from_url(url: str) -> Optional[RegionEntry]:
    """Определяет регион по URL поиска (?wp=Warszawa, /praca/warszawa/)."""
    if not url:
        return None
    parsed = urlparse(url)
    haystack = unquote(f"{parsed.path} {parsed.query}").replace("-", " ").replace("+", " ")
    return find_region_entry(haystack)


def _job_field(job, name: str) -> str:
    return str(getattr(job, name, "") or "")


def job_location_text(job) -> str:
    """Поля вакансии, в которых осмысленно искать локацию."""
    return " ".join((
        _job_field(job, "job_location"),
        _job_field(job, "title"),
        _job_field(job, "employment_type"),
        _job_field(job, "description")[:1500],
    ))


def job_work_format(job) -> WorkFormat:
    """Формат работы, заявленный в вакансии."""
    text = " ".join((
        _job_field(job, "job_location"),
        _job_field(job, "employment_type"),
        _job_field(job, "title"),
        _job_field(job, "description")[:2000],
    ))
    if is_remote_text(text) and not is_hybrid_text(text):
        return WorkFormat.REMOTE
    if is_hybrid_text(text):
        return WorkFormat.HYBRID
    if is_onsite_text(text):
        return WorkFormat.ONSITE
    return WorkFormat.ANY


def match_job_region(
    job,
    region: Optional[ResolvedRegion],
    *,
    allow_remote: bool = False,
) -> RegionMatch:
    """
    Сверяет вакансию с запрошенным регионом.

    MATCH    — регион подтверждён (или вакансия удалённая, если это разрешено);
    MISMATCH — вакансия явно из другого места;
    UNKNOWN  — локацию определить не удалось.
    """
    if region is None:
        return RegionMatch.MATCH

    matcher = region.matcher()
    if matcher is None:
        return RegionMatch.UNKNOWN

    def _covers(text: str) -> bool:
        folded = _fold(text)
        if not folded:
            return False
        if matcher.search(folded):
            return True
        # Для странового запроса подходит любой город этой страны.
        if region.kind == "country" and region.country:
            return any(
                entry.country == region.country and pattern.search(folded)
                for entry, pattern in _catalog_patterns()
            )
        return False

    remote_ok = allow_remote and job_work_format(job) == WorkFormat.REMOTE
    location_field = _job_field(job, "job_location").strip()

    # Поле локации — главный источник истины.
    if location_field:
        if _covers(location_field):
            return RegionMatch.MATCH
        if remote_ok:
            return RegionMatch.MATCH
        folded_field = _fold(location_field)
        if find_region_entries(folded_field):
            return RegionMatch.MISMATCH
        if folded_field in _LOCATION_NOISE or len(re.sub(r"\W", "", folded_field)) < 3:
            return RegionMatch.UNKNOWN
        return RegionMatch.MISMATCH

    # Локация не заполнена — ищем регион в тексте вакансии.
    if _covers(job_location_text(job)):
        return RegionMatch.MATCH
    if remote_ok:
        return RegionMatch.MATCH

    hint = region_from_url(_job_field(job, "source_url"))
    if hint:
        if hint.key == region.key or (
            region.kind == "country" and hint.country == region.country
        ):
            return RegionMatch.MATCH
        return RegionMatch.MISMATCH

    return RegionMatch.UNKNOWN


def match_job_regions(
    job,
    regions: Sequence[ResolvedRegion],
    *,
    allow_remote: bool = False,
) -> RegionMatch:
    """
    Вакансия подходит, если совпал хотя бы один выбранный регион.

    MATCH    — есть совпадение (или удалёнка, если разрешена);
    MISMATCH — ни один регион не подошёл, и локация определена;
    UNKNOWN  — локацию подтвердить не удалось.
    """
    if not regions:
        return RegionMatch.MATCH

    verdicts = [
        match_job_region(job, region, allow_remote=allow_remote)
        for region in regions
    ]
    if any(verdict == RegionMatch.MATCH for verdict in verdicts):
        return RegionMatch.MATCH
    if all(verdict == RegionMatch.MISMATCH for verdict in verdicts):
        return RegionMatch.MISMATCH
    return RegionMatch.UNKNOWN


def region_identity(text: str) -> str:
    """Ключ для сравнения двух написаний одного города."""
    resolved = resolve_region(text)
    if resolved:
        return resolved.key
    return _fold(text)


def canonicalize_region(text: str) -> str:
    """Каноническое имя региона для URL и кнопок."""
    resolved = resolve_region(text)
    return resolved.display if resolved else (text or "").strip()


def parse_region_list(text: str, *, limit: int = MAX_REGIONS) -> Tuple[str, ...]:
    """
    Разбирает один или несколько регионов из свободного ввода.

    «Брест, Минск и Москва», «Brest / Minsk», «Warszawa».
    """
    raw = (text or "").strip()
    if not raw:
        return ()

    parts = [part.strip() for part in _REGION_SPLIT.split(raw) if part and part.strip()]
    if len(parts) <= 1:
        entries = find_region_entries(raw)
        if len(entries) >= 2:
            parts = [entry.display for entry in entries]
        elif not parts:
            parts = [raw]

    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        resolved = resolve_region(part)
        if resolved is None:
            continue
        if resolved.key in seen:
            continue
        seen.add(resolved.key)
        result.append(resolved.display)
        if len(result) >= limit:
            break
    return tuple(result)


def merge_regions(*groups: Iterable[str], limit: int = MAX_REGIONS) -> Tuple[str, ...]:
    """Склеивает списки регионов без дублей (Минск и Minsk — один город)."""
    result: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            for name in parse_region_list(item, limit=limit):
                key = region_identity(name)
                if not key or key in seen:
                    continue
                seen.add(key)
                result.append(canonicalize_region(name))
                if len(result) >= limit:
                    return tuple(result)
    return tuple(result)


def toggle_region(
    current: Iterable[str],
    candidate: str,
    *,
    limit: int = MAX_REGIONS,
) -> Tuple[str, ...]:
    """Добавляет город или убирает его, если он уже выбран."""
    selected = merge_regions(current, limit=limit)
    key = region_identity(candidate)
    if not key:
        return selected
    if any(region_identity(item) == key for item in selected):
        return tuple(item for item in selected if region_identity(item) != key)
    if len(selected) >= limit:
        return selected
    return merge_regions(selected, (candidate,), limit=limit)
