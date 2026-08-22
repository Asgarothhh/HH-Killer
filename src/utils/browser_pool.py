"""Пул Playwright с stealth-режимом и обходом bot-защиты."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from src.utils.bot_detection import BlockCheckResult, check_page_blocked, is_content_usable
from src.utils.logger import get_logger
from src.utils.stealth_browser import (
    apply_stealth_to_page,
    build_context_options,
    get_launch_args,
    get_stealth_config,
    human_delay,
    human_scroll,
)

logger = get_logger(__name__)

_browser = None
_playwright = None
_lock = asyncio.Lock()

WAIT_STRATEGIES = (
    {"wait_until": "domcontentloaded", "timeout": 30000},
    {"wait_until": "load", "timeout": 45000},
    {"wait_until": "networkidle", "timeout": 60000},
)


@dataclass
class PageFetchResult:
    url: str
    title: str
    text: str
    html: str
    links: List[Dict[str, str]] = field(default_factory=list)
    blocked: BlockCheckResult = field(default_factory=lambda: BlockCheckResult(False))
    strategy: str = "stealth"


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
                "ignore_default_args": ["--enable-automation"],
            }
            if cfg["slow_mo_ms"]:
                launch_kwargs["slow_mo"] = cfg["slow_mo_ms"]

            if cfg["use_chrome_channel"]:
                try:
                    _browser = await _playwright.chromium.launch(
                        channel="chrome",
                        **launch_kwargs,
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


async def new_stealth_page(url: Optional[str] = None):
    browser = await get_browser()
    context = await browser.new_context(**build_context_options(url))
    page = await context.new_page()
    await apply_stealth_to_page(page)
    return page, context


async def _extract_page_data(page) -> Tuple[str, str, str, List[Dict[str, str]]]:
    title = await page.title()
    data = await page.evaluate("""
        () => {
            const main = document.querySelector(
                'main, [class*="job"], [class*="vacancy"], [class*="offer"], [class*="oferta"], article, #content, .content'
            ) || document.body;
            const links = Array.from(document.querySelectorAll('a[href]')).map(a => ({
                href: a.href,
                text: (a.textContent || '').trim().slice(0, 120),
            })).filter(l => l.href && l.href.startsWith('http'));
            return {
                text: (main.innerText || '').slice(0, 20000),
                html: document.documentElement.outerHTML.slice(0, 50000),
                links,
            };
        }
    """)
    return title, data["text"], data["html"], data["links"]


async def fetch_page_stealth(
    url: str,
    *,
    scroll: bool = True,
    retry_on_block: bool = True,
) -> PageFetchResult:
    """Загружает страницу с stealth и повторными попытками при блокировке."""
    last_result: Optional[PageFetchResult] = None
    strategies = WAIT_STRATEGIES if retry_on_block else WAIT_STRATEGIES[:1]

    for idx, strategy in enumerate(strategies):
        page = None
        context = None
        strategy_name = f"stealth-{strategy['wait_until']}"
        try:
            page, context = await new_stealth_page(url)
            await human_delay(300, 700)
            await page.goto(url, **strategy)
            await human_delay(1000, 2000)

            if scroll:
                await human_scroll(page, steps=3 if idx > 0 else 2)
                await human_delay(500, 1200)

            title, text, html, links = await _extract_page_data(page)
            blocked = check_page_blocked(title, text, html, len(links))

            result = PageFetchResult(
                url=url,
                title=title,
                text=text,
                html=html,
                links=links,
                blocked=blocked,
                strategy=strategy_name,
            )
            last_result = result

            if not blocked.is_blocked and is_content_usable(text):
                logger.info("Страница загружена (%s): %s", strategy_name, url[:60])
                return result

            if blocked.is_blocked:
                logger.warning(
                    "Bot-защита (%s, %s): %s — retry %d/%d",
                    strategy_name, blocked.reason, url[:50], idx + 1, len(strategies),
                )
                if idx < len(strategies) - 1:
                    await human_delay(2000, 4000)
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
                raise
        finally:
            if context:
                await context.close()

    return last_result or PageFetchResult(
        url=url, title="", text="", html="",
        blocked=BlockCheckResult(True, "Не удалось загрузить"),
    )


async def new_page(user_agent: Optional[str] = None):
    """Обратная совместимость — создаёт stealth-страницу."""
    return await new_stealth_page()


async def close_browser_pool():
    global _browser, _playwright

    async with _lock:
        if _browser:
            await _browser.close()
            _browser = None
        if _playwright:
            await _playwright.stop()
            _playwright = None
        logger.info("Playwright-пул закрыт")
