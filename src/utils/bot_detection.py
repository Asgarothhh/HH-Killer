"""Детекция страниц с anti-bot защитой (Cloudflare, CAPTCHA и т.д.)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class BlockCheckResult:
    is_blocked: bool
    reason: str = ""
    confidence: float = 0.0


_BLOCK_TITLE_PATTERNS = (
    r"just a moment",
    r"attention required",
    r"access denied",
    r"403 forbidden",
    r"robot.?check",
    r"verify you are human",
    r"checking your browser",
    r"please wait",
    r"ddos protection",
    r"security check",
    r"captcha",
    r"blocked",
    r"unusual traffic",
)

_BLOCK_BODY_PATTERNS = (
    r"cloudflare",
    r"cf-browser-verification",
    r"challenge-platform",
    r"g-recaptcha",
    r"hcaptcha",
    r"datadome",
    r"perimeterx",
    r"distil",
    r"akamai",
    r"bot detection",
    r"automated access",
    r"enable javascript and cookies",
    r"ray id",
    r"turnstile",
    r"are you a robot",
    r"czy jeste[sś] robotem",
    r"weryfikacja",
    r"ochrona przed botami",
)

_MIN_CONTENT_LENGTH = 400


def check_page_blocked(
    title: str,
    body_text: str,
    html: str = "",
    link_count: int = 0,
) -> BlockCheckResult:
    """Определяет, заблокирована ли страница anti-bot системой."""
    title_l = (title or "").lower()
    text_l = (body_text or "").lower()
    html_l = (html or "").lower()
    combined = f"{title_l}\n{text_l}\n{html_l}"

    for pattern in _BLOCK_TITLE_PATTERNS:
        if re.search(pattern, title_l, re.I):
            return BlockCheckResult(True, f"Заголовок: {pattern}", 0.9)

    body_hits = sum(1 for p in _BLOCK_BODY_PATTERNS if re.search(p, combined, re.I))
    if body_hits >= 2:
        return BlockCheckResult(True, "Anti-bot маркеры в HTML", 0.85)
    if body_hits == 1 and len(text_l.strip()) < _MIN_CONTENT_LENGTH:
        return BlockCheckResult(True, "Anti-bot + мало контента", 0.75)

    if len(text_l.strip()) < 120 and link_count < 3:
        if any(k in combined for k in ("captcha", "cloudflare", "challenge", "verify")):
            return BlockCheckResult(True, "Пустая страница с признаками защиты", 0.8)

    if len(text_l.strip()) < 80 and link_count == 0:
        return BlockCheckResult(True, "Нет контента и ссылок", 0.6)

    return BlockCheckResult(False)


def is_content_usable(text: str, min_length: int = _MIN_CONTENT_LENGTH) -> bool:
    return len((text or "").strip()) >= min_length
