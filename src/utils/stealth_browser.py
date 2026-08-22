"""Stealth-настройки Playwright для обхода базовой bot-защиты."""

from __future__ import annotations

import os
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

from src.utils.logger import get_logger

logger = get_logger(__name__)

# Актуальные Chrome UA (Windows)
USER_AGENTS: Tuple[str, ...] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)

STEALTH_INIT_SCRIPT = """
(() => {
  Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  Object.defineProperty(navigator, 'languages', { get: () => ['pl-PL', 'pl', 'en-US', 'en'] });
  Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
  window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
  const originalQuery = window.navigator.permissions.query;
  window.navigator.permissions.query = (parameters) =>
    parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : originalQuery(parameters);
})();
"""


def get_stealth_config() -> Dict[str, Any]:
    load_dotenv()
    return {
        "headless": os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() == "true",
        "use_chrome_channel": os.getenv("PLAYWRIGHT_USE_CHROME", "true").lower() == "true",
        "proxy": os.getenv("PLAYWRIGHT_PROXY") or os.getenv("PROXY_URL") or None,
        "storage_state": os.getenv("PLAYWRIGHT_STORAGE_STATE") or None,
        "timezone": os.getenv("PLAYWRIGHT_TIMEZONE", "Europe/Warsaw"),
        "locale": os.getenv("PLAYWRIGHT_LOCALE", "pl-PL"),
        "slow_mo_ms": int(os.getenv("PLAYWRIGHT_SLOW_MO", "0")),
    }


def pick_user_agent() -> str:
    load_dotenv()
    custom = os.getenv("PLAYWRIGHT_USER_AGENT")
    if custom:
        return custom.strip()
    return random.choice(USER_AGENTS)


def get_launch_args() -> List[str]:
    return [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--no-sandbox",
        "--disable-infobars",
        "--window-size=1920,1080",
        "--lang=pl-PL,pl,en-US,en",
    ]


def get_proxy_config() -> Optional[Dict[str, str]]:
    proxy = get_stealth_config()["proxy"]
    if not proxy:
        return None
    return {"server": proxy}


def load_storage_state() -> Optional[str]:
    path = get_stealth_config()["storage_state"]
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        logger.warning("Storage state не найден: %s", path)
        return None
    return str(p.resolve())


async def human_delay(min_ms: int = 800, max_ms: int = 2200) -> None:
    import asyncio
    await asyncio.sleep(random.uniform(min_ms, max_ms) / 1000)


async def human_scroll(page, steps: int = 3) -> None:
    """Имитирует прокрутку пользователя."""
    for i in range(steps):
        ratio = (i + 1) / (steps + 1)
        await page.evaluate(
            "(r) => window.scrollTo({ top: document.body.scrollHeight * r, behavior: 'smooth' })",
            ratio,
        )
        await human_delay(400, 900)


async def apply_stealth_to_page(page) -> None:
    await page.add_init_script(STEALTH_INIT_SCRIPT)


def build_context_options(url: Optional[str] = None) -> Dict[str, Any]:
    cfg = get_stealth_config()
    options: Dict[str, Any] = {
        "user_agent": pick_user_agent(),
        "locale": cfg["locale"],
        "timezone_id": cfg["timezone"],
        "viewport": {"width": 1920, "height": 1080},
        "screen": {"width": 1920, "height": 1080},
        "device_scale_factor": 1,
        "is_mobile": False,
        "has_touch": False,
        "color_scheme": "light",
        "extra_http_headers": {
            "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        },
    }
    storage = load_storage_state()
    if storage:
        options["storage_state"] = storage
    proxy = get_proxy_config()
    if proxy:
        options["proxy"] = proxy
        logger.info("Playwright proxy: %s", proxy["server"])
    return options
