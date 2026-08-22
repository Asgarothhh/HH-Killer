import asyncio
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

from src.bot.handlers import router as handlers_router
from src.bot.middleware.errors import router as errors_router
from src.utils.browser_pool import close_browser_pool
from src.utils.logger import get_logger, setup_applevel_logger
from src.utils.utils import validate_environment

logger = get_logger(__name__)


async def on_shutdown() -> None:
    await close_browser_pool()
    logger.info("Бот остановлен")


async def main() -> None:
    load_dotenv()
    setup_applevel_logger(file_name="bot.log")
    validate_environment()

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN не найден. Добавьте его в .env")

    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(errors_router)
    dp.include_router(handlers_router)
    dp.shutdown.register(on_shutdown)

    logger.info("Запуск Telegram-бота HH-Killer")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C")
    except Exception as error:
        logger.error("Критическая ошибка: %s", error)
        sys.exit(1)
