"""Пул Playwright со stealth-режимом, троттлингом и обходом bot-защиты."""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from src.utils.bot_detection import (
    BlockCheckResult,
    BlockKind,
    check_page_blocked,
    is_content_usable,
)
from src.utils.logger import get_logger
from src.utils.stealth_browser import (
    apply_stealth_to_page,
    block_heavy_resources,
    build_context_options,
    dismiss_consent_banners,
    get_launch_args,
    get_stealth_config,
    human_delay,
    human_mouse_move,
    human_scroll,
    pick_profile,
)

logger = get_logger(__name__)

_browser = None
_playwright = None
_lock = asyncio.Lock()

WAIT_STRATEGIES = (
    {"wait_until": "domcontentloaded", "timeout": 35000},
    {"wait_until": "load", "timeout": 45000},
    {"wait_until": "networkidle", "timeout": 60000},
)

# Сколько ждать автоматического прохождения JS-челленджа (Cloudflare и т.п.)
CHALLENGE_WAIT_SECONDS = 12.0

# Домен, отдавший блокировку, отправляется в «остывание» на это время
DOMAIN_COOLDOWN_SECONDS = 90.0


@dataclass
class DomainState:
    """Троттлинг и circuit breaker для одного домена."""
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_request: float = 0.0
    blocked_until: float = 0.0
    consecutive_blocks: int = 0


_domains: Dict[str, DomainState] = {}


@dataclass
class PageFetchResult:
    url: str
    title: str
    text: str
    html: str
    links: List[Dict[str, str]] = field(default_factory=list)
    blocked: BlockCheckResult = field(default_factory=lambda: BlockCheckResult(False))
    strategy: str = "stealth"


def _domain_of(url: str) -> str:
    return (urlparse(url).hostname or url).lower()


def _domain_state(url: str) -> DomainState:
    domain = _domain_of(url)
    state = _domains.get(domain)
    if state is None:
        state = DomainState()
        _domains[domain] = state
    return state


def domain_is_cooling_down(url: str) -> bool:
    state = _domains.get(_domain_of(url))
    return bool(state and state.blocked_until > time.monotonic())


async def _throttle(url: str) -> None:
    """Держит паузу между запросами к одному домену."""
    state = _domain_state(url)
    interval = get_stealth_config()["min_request_interval"]

    now = time.monotonic()
    if state.blocked_until > now:
        wait = state.blocked_until - now
        logger.info("Домен %s остывает ещё %.0f сек", _domain_of(url), wait)
        await asyncio.sleep(min(wait, 15.0))

    elapsed = time.monotonic() - state.last_request
    if state.last_request and elapsed < interval:
        await asyncio.sleep(interval - elapsed + random.uniform(0, 0.8))
    state.last_request = time.monotonic()


def _register_block(url: str) -> None:
    state = _domain_state(url)
    state.consecutive_blocks += 1
    if state.consecutive_blocks >= 2:
        state.blocked_until = time.monotonic() + DOMAIN_COOLDOWN_SECONDS
        logger.warning(
            "Домен %s заблокировал %d раза подряд — пауза %.0f сек",
            _domain_of(url), state.consecutive_blocks, DOMAIN_COOLDOWN_SECONDS,
        )


def _register_success(url: str) -> None:
    state = _domain_state(url)
    state.consecutive_blocks = 0
    state.blocked_until = 0.0


async def get_browser():
    global _browser, _playwright

    async with _lock:
        if _browser is None or not _browser.is_connected():
            from playwright.async_api import async_playwright

            cfg = get_stealth_config()
            _playwright = await async_playwright().start()

            launch_kwargs = {
                "headless": cfg["headless"],
                "args": get_launch_args(),
                "ignore_default_args": ["--enable-automation", "--disable-extensions"],
            }
            if cfg["slow_mo_ms"]:
                launch_kwargs["slow_mo"] = cfg["slow_mo_ms"]

            if cfg["use_chrome_channel"]:
                try:
                    _browser = await _playwright.chromium.launch(
                        channel="chrome", **launch_kwargs,
                    )
                    logger.info("Playwright: Chrome (stealth) запущен")
                except Exception as error:
                    logger.warning("Chrome channel недоступен (%s), fallback на Chromium", error)
                    _browser = await _playwright.chromium.launch(**launch_kwargs)
                    logger.info("Playwright: Chromium (stealth) запущен")
            else:
                _browser = await _playwright.chromium.launch(**launch_kwargs)
                logger.info("Playwright: Chromium (stealth) запущен")

    return _browser


async def new_stealth_page(url: Optional[str] = None, attempt: int = 0):
    """Новый контекст с согласованным отпечатком браузера."""
    browser = await get_browser()
    profile = pick_profile(attempt)
    context = await browser.new_context(
        **build_context_options(url, profile=profile, attempt=attempt)
    )
    if get_stealth_config()["block_assets"]:
        await block_heavy_resources(context)
    page = await context.new_page()
    await apply_stealth_to_page(page, profile)
    return page, context


async def _extract_page_data(page) -> Tuple[str, str, str, List[Dict[str, str]]]:
    title = await page.title()
    data = await page.evaluate("""
        () => {
            // Берём самый содержательный контейнер: первый подходящий селектор
            // часто оказывается мелким блоком-обёрткой.
            const selectors = [
                'main', 'article', '[role="main"]', '#content', '.content',
                '[class*="offer"]', '[class*="oferta"]', '[class*="vacancy"]',
                '[class*="job"]', '[class*="description"]',
            ];
            const bodyText = (document.body && document.body.innerText || '').trim();
            let best = '';
            for (const selector of selectors) {
                for (const node of document.querySelectorAll(selector)) {
                    const text = (node.innerText || '').trim();
                    if (text.length > best.length) best = text;
                }
            }
            const text = best.length >= bodyText.length * 0.4 ? best : bodyText;

            const links = Array.from(document.querySelectorAll('a[href]')).map(a => ({
                href: a.href,
                text: (a.textContent || '').trim().slice(0, 120),
            })).filter(l => l.href && l.href.startsWith('http'));

            return {
                text: text.slice(0, 20000),
                html: document.documentElement.outerHTML.slice(0, 50000),
                links,
            };
        }
    """)
    return title, data["text"], data["html"], data["links"]


async def _wait_out_challenge(page, url: str) -> bool:
    """
    Ждёт, пока JS-челлендж пройдёт сам.

    Cloudflare/Turnstile в невидимом режиме часто пропускают браузер через
    несколько секунд — достаточно не закрывать страницу слишком быстро.
    """
    deadline = time.monotonic() + CHALLENGE_WAIT_SECONDS
    while time.monotonic() < deadline:
        await human_delay(1200, 2200)
        try:
            title = await page.title()
            body = await page.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception:
            return False

        verdict = check_page_blocked(title, body)
        if not verdict.is_blocked and is_content_usable(body):
            logger.info("Челлендж пройден автоматически: %s", url[:60])
            return True
    return False


async def fetch_page_stealth(
    url: str,
    *,
    scroll: bool = True,
    retry_on_block: bool = True,
) -> PageFetchResult:
    """Загружает страницу со сменой отпечатка и стратегии при блокировке."""
    last_result: Optional[PageFetchResult] = None
    strategies = WAIT_STRATEGIES if retry_on_block else WAIT_STRATEGIES[:1]

    for idx, strategy in enumerate(strategies):
        page = None
        context = None
        strategy_name = f"stealth-{strategy['wait_until']}"
        await _throttle(url)

        try:
            page, context = await new_stealth_page(url, attempt=idx)
            await human_delay(300, 700)
            await page.goto(url, **strategy)
            await human_delay(900, 1800)

            await dismiss_consent_banners(page)

            title = await page.title()
            body_preview = await page.evaluate(
                "() => document.body ? document.body.innerText.slice(0, 4000) : ''"
            )
            early = check_page_blocked(title, body_preview)
            if early.kind == BlockKind.CHALLENGE:
                if await _wait_out_challenge(page, url):
                    await dismiss_consent_banners(page)

            if scroll:
                await human_mouse_move(page, moves=2)
                await human_scroll(page, steps=3 if idx > 0 else 2)
                await human_delay(500, 1200)

            title, text, html, links = await _extract_page_data(page)
            blocked = check_page_blocked(title, text, html, len(links))

            result = PageFetchResult(
                url=url, title=title, text=text, html=html,
                links=links, blocked=blocked, strategy=strategy_name,
            )
            last_result = result

            if not blocked.is_blocked and is_content_usable(text):
                _register_success(url)
                logger.info("Страница загружена (%s): %s", strategy_name, url[:60])
                return result

            if blocked.is_blocked:
                _register_block(url)
                logger.warning(
                    "Bot-защита (%s, %s): %s — попытка %d/%d",
                    strategy_name, blocked.reason, url[:50], idx + 1, len(strategies),
                )
                if idx < len(strategies) - 1:
                    await human_delay(2500, 5000)
                    continue

            return result

        except Exception as error:
            err_text = str(error).lower()
            if "has been closed" in err_text or "target page, context or browser" in err_text:
                global _browser, _playwright
                async with _lock:
                    _browser = None
                    _playwright = None
                logger.warning("Playwright-сессия сброшена после обрыва браузера")
            logger.error("Ошибка загрузки %s (%s): %s", url[:50], strategy_name, error)
            if idx == len(strategies) - 1:
                if last_result is not None:
                    return last_result
                raise
        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass

    return last_result or PageFetchResult(
        url=url, title="", text="", html="",
        blocked=BlockCheckResult(True, "Не удалось загрузить"),
    )


async def close_browser_pool():
    global _browser, _playwright

    async with _lock:
        if _browser:
            await _browser.close()
            _browser = None
        if _playwright:
            await _playwright.stop()
            _playwright = None
        _domains.clear()
        logger.info("Playwright-пул закрыт")
