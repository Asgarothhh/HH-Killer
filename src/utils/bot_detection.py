"""Детекция страниц с anti-bot защитой (Cloudflare, CAPTCHA, DataDome)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class BlockKind(str, Enum):
    NONE = "none"
    CHALLENGE = "challenge"   # JS-проверка, часто проходит сама
    CAPTCHA = "captcha"       # нужен прокси/куки — сами не решим
    DENIED = "denied"         # 403/бан
    EMPTY = "empty"           # контента нет, признаков защиты тоже


@dataclass
class BlockCheckResult:
    is_blocked: bool
    reason: str = ""
    confidence: float = 0.0
    kind: BlockKind = BlockKind.NONE


_CHALLENGE_PATTERNS = (
    r"just a moment",
    r"checking your browser",
    r"verifying you are human",
    r"cf-browser-verification",
    r"challenge-platform",
    r"cf_chl_opt",
    r"_cf_chl",
    r"turnstile",
    r"enable javascript and cookies",
    r"weryfikacja przegl[aą]darki",
)

_CAPTCHA_PATTERNS = (
    r"\bcaptcha\b",
    r"g-recaptcha",
    r"hcaptcha",
    r"recaptcha",
    r"verify you are human",
    r"are you a robot",
    r"robot.?check",
    r"czy jeste[sś] robotem",
    r"press and hold",
    r"datadome",
    r"perimeterx",
    r"px-captcha",
)

_DENIED_PATTERNS = (
    r"access denied",
    r"403 forbidden",
    r"attention required",
    r"you have been blocked",
    r"unusual traffic",
    r"ddos protection",
    r"request blocked",
    r"dost[eę]p zablokowany",
    r"доступ запрещ[её]н",
)

# Маркеры, которые сами по себе ничего не значат: их достаточно много на
# обычных страницах (например, Cloudflare как CDN или скрипт капчи в футере).
_WEAK_PATTERNS = (
    r"cloudflare",
    r"ray id",
    r"akamai",
    r"distil",
    r"bot detection",
    r"automated access",
)

_MIN_CONTENT_LENGTH = 400


def _search_any(patterns, text: str) -> str | None:
    for pattern in patterns:
        if re.search(pattern, text, re.I):
            return pattern
    return None


def check_page_blocked(
    title: str,
    body_text: str,
    html: str = "",
    link_count: int = 0,
) -> BlockCheckResult:
    """Определяет, заблокирована ли страница, и чем именно."""
    title_l = (title or "").lower()
    text_l = (body_text or "").lower()
    content_length = len(text_l.strip())
    # HTML проверяем только на явные challenge-маркеры: в разметке крупных
    # порталов слова вида «captcha» встречаются в неактивных скриптах.
    combined = f"{title_l}\n{text_l}"
    markup = (html or "").lower()

    challenge = _search_any(_CHALLENGE_PATTERNS, combined) or _search_any(
        _CHALLENGE_PATTERNS, markup
    )
    if challenge and content_length < 2000:
        return BlockCheckResult(True, f"JS-челлендж: {challenge}", 0.9, BlockKind.CHALLENGE)

    denied = _search_any(_DENIED_PATTERNS, combined)
    if denied:
        return BlockCheckResult(True, f"Доступ запрещён: {denied}", 0.95, BlockKind.DENIED)

    captcha = _search_any(_CAPTCHA_PATTERNS, combined)
    if captcha and content_length < 3000:
        return BlockCheckResult(True, f"CAPTCHA: {captcha}", 0.9, BlockKind.CAPTCHA)

    weak = _search_any(_WEAK_PATTERNS, combined)
    if weak and content_length < _MIN_CONTENT_LENGTH:
        return BlockCheckResult(
            True, f"Признак защиты и мало контента: {weak}", 0.7, BlockKind.CHALLENGE,
        )

    if content_length < 120 and link_count < 3:
        if _search_any(_CAPTCHA_PATTERNS + _CHALLENGE_PATTERNS, markup):
            return BlockCheckResult(
                True, "Пустая страница с признаками защиты", 0.8, BlockKind.CHALLENGE,
            )

    if content_length < 80 and link_count == 0:
        return BlockCheckResult(True, "Нет контента и ссылок", 0.6, BlockKind.EMPTY)

    return BlockCheckResult(False)


def is_content_usable(text: str, min_length: int = _MIN_CONTENT_LENGTH) -> bool:
    return len((text or "").strip()) >= min_length
