from aiogram import Router

from src.bot.handlers import search, start

router = Router()
router.include_router(start.router)
router.include_router(search.router)
