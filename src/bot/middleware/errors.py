"""Глобальный обработчик ошибок Telegram-бота."""

import traceback

from aiogram import Router
from aiogram.types import CallbackQuery, ErrorEvent, Message

from src.utils.logger import get_logger
from src.utils.telegram_html import h, safe_callback_answer, safe_edit_text

logger = get_logger(__name__)

router = Router()


@router.errors()
async def global_error_handler(event: ErrorEvent) -> bool:
    """Ловит необработанные исключения, логирует полный traceback."""
    exc = event.exception
    logger.error(
        "Необработанная ошибка: %s\n%s",
        exc,
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
    )

    update = event.update
    user_message = (
        "⚠️ <b>Произошла ошибка</b>\n\n"
        f"{h(str(exc)[:500])}\n\n"
        "Попробуйте /start или обратитесь к администратору."
    )

    try:
        if update.callback_query:
            cb: CallbackQuery = update.callback_query
            await safe_callback_answer(cb, "Ошибка — см. сообщение", show_alert=True)
            if cb.message:
                await safe_edit_text(cb.message, user_message, parse_mode="HTML")
        elif update.message:
            msg: Message = update.message
            await msg.answer(user_message, parse_mode="HTML")
    except Exception as notify_error:
        logger.error("Не удалось уведомить пользователя: %s", notify_error)

    return True
