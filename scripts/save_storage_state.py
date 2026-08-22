"""Сохраняет Playwright storage state (cookies) после ручного логина на job-сайте.

Использование:
  python scripts/save_storage_state.py https://www.pracuj.pl storage/pracuj.json

Затем в .env:
  PLAYWRIGHT_STORAGE_STATE=storage/pracuj.json
"""

import asyncio
import os
import sys
from pathlib import Path

# Видимый браузер для ручного логина
os.environ.setdefault("PLAYWRIGHT_HEADLESS", "false")

from dotenv import load_dotenv

load_dotenv()


async def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python scripts/save_storage_state.py <url> <output.json>")
        sys.exit(1)

    url = sys.argv[1]
    output = Path(sys.argv[2])
    output.parent.mkdir(parents=True, exist_ok=True)

    from src.utils.browser_pool import close_browser_pool, new_stealth_page

    print(f"Откройте сайт и войдите в аккаунт (если нужно): {url}")
    print("После загрузки страницы нажмите Enter в терминале…")

    page, context = await new_stealth_page(url)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        input(">>> Enter когда страница загружена и вы авторизованы… ")
        await context.storage_state(path=str(output))
        print(f"✅ Storage state сохранён: {output.resolve()}")
    finally:
        await context.close()
        await close_browser_pool()


if __name__ == "__main__":
    asyncio.run(main())
