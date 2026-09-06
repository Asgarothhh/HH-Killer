"""Stealth-настройки Playwright для обхода bot-защиты."""

from __future__ import annotations

import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class BrowserProfile:
    """Согласованный набор UA, Client Hints и параметров платформы.

    Рассогласование UA и navigator.platform / Sec-CH-UA — самый частый
    признак автоматизации, поэтому все значения берутся из одного профиля.
    """
    user_agent: str
    platform: str
    sec_ch_ua: str
    sec_ch_ua_platform: str
    viewport: Tuple[int, int]
    device_scale_factor: float = 1.0
    hardware_concurrency: int = 8
    device_memory: int = 8
    vendor: str = "Google Inc."
    webgl_vendor: str = "Google Inc. (NVIDIA)"
    webgl_renderer: str = (
        "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"
    )


BROWSER_PROFILES: Tuple[BrowserProfile, ...] = (
    BrowserProfile(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        platform="Win32",
        sec_ch_ua='"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        sec_ch_ua_platform='"Windows"',
        viewport=(1920, 1080),
    ),
    BrowserProfile(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        ),
        platform="Win32",
        sec_ch_ua='"Google Chrome";v="130", "Chromium";v="130", "Not?A_Brand";v="99"',
        sec_ch_ua_platform='"Windows"',
        viewport=(1680, 1050),
        hardware_concurrency=12,
        device_memory=16,
    ),
    BrowserProfile(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0"
        ),
        platform="Win32",
        sec_ch_ua='"Microsoft Edge";v="129", "Chromium";v="129", "Not=A?Brand";v="8"',
        sec_ch_ua_platform='"Windows"',
        viewport=(1536, 864),
        hardware_concurrency=8,
        webgl_vendor="Google Inc. (Intel)",
        webgl_renderer=(
            "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)"
        ),
    ),
    BrowserProfile(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        platform="MacIntel",
        sec_ch_ua='"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        sec_ch_ua_platform='"macOS"',
        viewport=(1728, 1117),
        device_scale_factor=2.0,
        hardware_concurrency=10,
        vendor="Apple Computer, Inc.",
        webgl_vendor="Google Inc. (Apple)",
        webgl_renderer="ANGLE (Apple, Apple M2, OpenGL 4.1)",
    ),
)

# Кнопки согласия на cookies — основной блокер контента на польских порталах
CONSENT_SELECTORS: Tuple[str, ...] = (
    "button#onetrust-accept-btn-handler",
    "button[id*='accept-all' i]",
    "button[data-testid*='accept' i]",
    "button[aria-label*='Akceptuj' i]",
    "button[aria-label*='Accept' i]",
    "#didomi-notice-agree-button",
    ".fc-cta-consent",
    ".cookie-consent__button",
    "button.sd-cmp-JHKW7",
    "text=/^(Akceptuj|Zaakceptuj|Zgadzam si|Akceptuję)/i",
    "text=/^(Accept all|Accept cookies|I agree|Agree)/i",
    "text=/^(Принять|Согласен|Понятно)/i",
)

# Ресурсы, которые не нужны для извлечения текста и ссылок
BLOCKED_RESOURCE_TYPES = frozenset({"image", "media", "font"})

BLOCKED_URL_FRAGMENTS: Tuple[str, ...] = (
    "google-analytics.com", "googletagmanager.com", "doubleclick.net",
    "facebook.net", "hotjar.com", "clarity.ms", "yandex.ru/metrika",
    "criteo.com", "adsystem.com", "smartlook.com", "fullstory.com",
)


def _stealth_script(profile: BrowserProfile, languages: List[str]) -> str:
    """Init-скрипт, скрывающий признаки Playwright и согласующий fingerprint."""
    return f"""
(() => {{
  const patch = (obj, prop, value) => {{
    try {{ Object.defineProperty(obj, prop, {{ get: () => value, configurable: true }}); }}
    catch (e) {{ /* свойство защищено — не критично */ }}
  }};

  patch(navigator, 'webdriver', undefined);
  patch(navigator, 'languages', {languages!r});
  patch(navigator, 'platform', {profile.platform!r});
  patch(navigator, 'vendor', {profile.vendor!r});
  patch(navigator, 'hardwareConcurrency', {profile.hardware_concurrency});
  patch(navigator, 'deviceMemory', {profile.device_memory});
  patch(navigator, 'maxTouchPoints', 0);

  const makePlugin = (name, filename, desc) => ({{
    name, filename, description: desc, length: 1,
  }});
  const plugins = [
    makePlugin('PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
    makePlugin('Chrome PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
    makePlugin('Chromium PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
    makePlugin('WebKit built-in PDF', 'internal-pdf-viewer', 'Portable Document Format'),
  ];
  patch(navigator, 'plugins', plugins);
  patch(navigator, 'mimeTypes', [{{ type: 'application/pdf', suffixes: 'pdf' }}]);

  window.chrome = window.chrome || {{}};
  window.chrome.runtime = window.chrome.runtime || {{}};
  window.chrome.loadTimes = () => ({{ requestTime: Date.now() / 1000 }});
  window.chrome.csi = () => ({{ startE: Date.now() }});
  window.chrome.app = {{ isInstalled: false, InstallState: {{}}, RunningState: {{}} }};

  if (navigator.permissions && navigator.permissions.query) {{
    const original = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = (params) =>
      params && params.name === 'notifications'
        ? Promise.resolve({{ state: Notification.permission, onchange: null }})
        : original(params);
  }}

  const getParameter = WebGLRenderingContext.prototype.getParameter;
  const spoofWebGL = function (parameter) {{
    if (parameter === 37445) return {profile.webgl_vendor!r};
    if (parameter === 37446) return {profile.webgl_renderer!r};
    return getParameter.call(this, parameter);
  }};
  WebGLRenderingContext.prototype.getParameter = spoofWebGL;
  if (window.WebGL2RenderingContext) {{
    WebGL2RenderingContext.prototype.getParameter = spoofWebGL;
  }}

  // Playwright оставляет след в toString у пропатченных функций
  const nativeToString = Function.prototype.toString;
  const patched = new Set([spoofWebGL]);
  if (navigator.permissions && navigator.permissions.query) {{
    patched.add(navigator.permissions.query);
  }}
  Function.prototype.toString = function () {{
    if (patched.has(this)) return 'function () {{ [native code] }}';
    return nativeToString.call(this);
  }};

  patch(screen, 'availWidth', {profile.viewport[0]});
  patch(screen, 'availHeight', {profile.viewport[1] - 40});
  patch(screen, 'colorDepth', 24);
  patch(screen, 'pixelDepth', 24);

  // Скрываем iframe-детект через contentWindow.chrome
  try {{
    const descriptor = Object.getOwnPropertyDescriptor(
      HTMLIFrameElement.prototype, 'contentWindow'
    );
    if (descriptor && descriptor.get) {{
      const originalGet = descriptor.get;
      Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {{
        get() {{
          const win = originalGet.call(this);
          try {{ if (win && !win.chrome) win.chrome = window.chrome; }} catch (e) {{}}
          return win;
        }},
      }});
    }}
  }} catch (e) {{ /* политика iframe — не критично */ }}
}})();
"""


def get_stealth_config() -> Dict[str, Any]:
    load_dotenv()
    return {
        "headless": os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() == "true",
        "use_chrome_channel": os.getenv("PLAYWRIGHT_USE_CHROME", "true").lower() == "true",
        "proxy": os.getenv("PLAYWRIGHT_PROXY") or os.getenv("PROXY_URL") or None,
        "proxies": [
            p.strip()
            for p in (os.getenv("PLAYWRIGHT_PROXIES") or "").split(",")
            if p.strip()
        ],
        "storage_state": os.getenv("PLAYWRIGHT_STORAGE_STATE") or None,
        "timezone": os.getenv("PLAYWRIGHT_TIMEZONE", "Europe/Warsaw"),
        "locale": os.getenv("PLAYWRIGHT_LOCALE", "pl-PL"),
        "slow_mo_ms": int(os.getenv("PLAYWRIGHT_SLOW_MO", "0") or 0),
        "block_assets": os.getenv("PLAYWRIGHT_BLOCK_ASSETS", "true").lower() == "true",
        "min_request_interval": float(os.getenv("SCRAPER_DOMAIN_INTERVAL", "2.5") or 2.5),
    }


def pick_profile(attempt: int = 0) -> BrowserProfile:
    """Профиль браузера; на повторных попытках — другой, чтобы сменить отпечаток."""
    load_dotenv()
    custom_ua = os.getenv("PLAYWRIGHT_USER_AGENT")
    base = random.choice(BROWSER_PROFILES) if attempt == 0 else BROWSER_PROFILES[
        attempt % len(BROWSER_PROFILES)
    ]
    if custom_ua:
        return BrowserProfile(
            user_agent=custom_ua.strip(),
            platform=base.platform,
            sec_ch_ua=base.sec_ch_ua,
            sec_ch_ua_platform=base.sec_ch_ua_platform,
            viewport=base.viewport,
        )
    return base


def pick_user_agent() -> str:
    return pick_profile().user_agent


def get_launch_args() -> List[str]:
    return [
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process,AutomationControlled",
        "--disable-dev-shm-usage",
        "--no-sandbox",
        "--disable-infobars",
        "--disable-notifications",
        "--no-first-run",
        "--no-default-browser-check",
        "--password-store=basic",
        "--window-size=1920,1080",
        "--lang=pl-PL,pl,en-US,en",
    ]


def get_proxy_config(attempt: int = 0) -> Optional[Dict[str, str]]:
    """Прокси с ротацией по списку PLAYWRIGHT_PROXIES."""
    cfg = get_stealth_config()
    pool: List[str] = list(cfg["proxies"])
    if cfg["proxy"] and cfg["proxy"] not in pool:
        pool.insert(0, cfg["proxy"])
    if not pool:
        return None
    return {"server": pool[attempt % len(pool)]}


def load_storage_state() -> Optional[str]:
    path = get_stealth_config()["storage_state"]
    if not path:
        return None
    candidate = Path(path)
    if not candidate.exists():
        logger.warning("Storage state не найден: %s", path)
        return None
    return str(candidate.resolve())


async def human_delay(min_ms: int = 800, max_ms: int = 2200) -> None:
    import asyncio

    await asyncio.sleep(random.uniform(min_ms, max_ms) / 1000)


async def human_scroll(page, steps: int = 3) -> None:
    """Прокрутка с паузами и небольшим откатом назад, как у человека."""
    for i in range(steps):
        ratio = (i + 1) / (steps + 1)
        try:
            await page.evaluate(
                "(r) => window.scrollTo({ top: document.body.scrollHeight * r,"
                " behavior: 'smooth' })",
                ratio,
            )
        except Exception:
            return
        await human_delay(350, 850)

    if random.random() < 0.5:
        try:
            await page.mouse.wheel(0, -random.randint(150, 400))
        except Exception:
            pass
        await human_delay(200, 500)


async def human_mouse_move(page, moves: int = 3) -> None:
    """Несколько случайных движений мыши — часть поведенческих проверок."""
    width, height = 1920, 1080
    try:
        for _ in range(moves):
            await page.mouse.move(
                random.randint(50, width - 50),
                random.randint(50, height - 50),
                steps=random.randint(5, 15),
            )
            await human_delay(80, 260)
    except Exception:
        pass


async def dismiss_consent_banners(page) -> bool:
    """Закрывает cookie/RODO-баннеры, которые прячут вакансии за оверлеем."""
    for selector in CONSENT_SELECTORS:
        try:
            locator = page.locator(selector).first
            if await locator.count() == 0:
                continue
            if not await locator.is_visible(timeout=800):
                continue
            await locator.click(timeout=2500, no_wait_after=True)
            logger.info("Закрыт баннер согласия: %s", selector)
            await human_delay(400, 900)
            return True
        except Exception:
            continue
    return False


async def apply_stealth_to_page(page, profile: Optional[BrowserProfile] = None) -> None:
    cfg = get_stealth_config()
    active = profile or BROWSER_PROFILES[0]
    languages = _languages_for_locale(cfg["locale"])
    await page.add_init_script(_stealth_script(active, languages))


async def block_heavy_resources(context) -> None:
    """Отключает картинки, шрифты и трекеры — быстрее и меньше следов."""
    async def _route(route):
        request = route.request
        if request.resource_type in BLOCKED_RESOURCE_TYPES:
            await route.abort()
            return
        if any(fragment in request.url for fragment in BLOCKED_URL_FRAGMENTS):
            await route.abort()
            return
        await route.continue_()

    try:
        await context.route("**/*", _route)
    except Exception as error:
        logger.debug("Не удалось включить блокировку ресурсов: %s", error)


def _languages_for_locale(locale: str) -> List[str]:
    primary = (locale or "pl-PL").replace("_", "-")
    short = primary.split("-")[0]
    languages = [primary, short]
    for extra in ("en-US", "en"):
        if extra not in languages:
            languages.append(extra)
    return languages


def build_context_options(
    url: Optional[str] = None,
    *,
    profile: Optional[BrowserProfile] = None,
    attempt: int = 0,
) -> Dict[str, Any]:
    cfg = get_stealth_config()
    active = profile or pick_profile(attempt)
    languages = _languages_for_locale(cfg["locale"])
    accept_language = ",".join(
        lang if i == 0 else f"{lang};q={max(0.9 - i * 0.1, 0.6):.1f}"
        for i, lang in enumerate(languages)
    )

    options: Dict[str, Any] = {
        "user_agent": active.user_agent,
        "locale": cfg["locale"],
        "timezone_id": cfg["timezone"],
        "viewport": {"width": active.viewport[0], "height": active.viewport[1]},
        "screen": {"width": active.viewport[0], "height": active.viewport[1]},
        "device_scale_factor": active.device_scale_factor,
        "is_mobile": False,
        "has_touch": False,
        "color_scheme": "light",
        "java_script_enabled": True,
        "ignore_https_errors": True,
        "extra_http_headers": {
            "Accept-Language": accept_language,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-Ch-Ua": active.sec_ch_ua,
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": active.sec_ch_ua_platform,
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "max-age=0",
            "DNT": "1",
        },
    }

    referer = _search_engine_referer(url)
    if referer:
        options["extra_http_headers"]["Referer"] = referer

    storage = load_storage_state()
    if storage:
        options["storage_state"] = storage

    proxy = get_proxy_config(attempt)
    if proxy:
        options["proxy"] = proxy
        logger.info("Playwright proxy: %s", proxy["server"])

    return options


def _search_engine_referer(url: Optional[str]) -> Optional[str]:
    """Переход «из поисковика» выглядит естественнее прямого захода."""
    if not url:
        return None
    return random.choice((
        "https://www.google.com/",
        "https://www.google.pl/",
        "https://duckduckgo.com/",
    ))
