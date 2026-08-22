"""Безопасная работа с Telegram HTML и сообщениями."""

from __future__ import annotations

import html
import logging
from typing import Optional

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

logger = logging.getLogger(__name__)


def h(text: Optional[str]) -> str:
    """Экранирует текст для parse_mode=HTML."""
    if not text:
        return ""
    return html.escape(str(text), quote=False)


def h_attr(text: Optional[str]) -> str:
    """Экранирует значение HTML-атрибута (href и т.д.)."""
    if not text:
        return ""
    return html.escape(str(text), quote=True)


async def safe_callback_answer(
    callback: CallbackQuery,
    text: str | None = None,
    *,
    show_alert: bool = False,
) -> bool:
    """answer() без падения на просроченный callback (лимит Telegram ~30 сек)."""
    try:
        await callback.answer(text=text, show_alert=show_alert)
        return True
    except TelegramBadRequest as error:
        err = str(error).lower()
        if any(
            phrase in err
            for phrase in (
                "query is too old",
                "query id is invalid",
                "response timeout expired",
            )
        ):
            logger.debug("Callback query expired, skip answer: %s", error)
            return False
        raise


async def safe_edit_text(message: Message, text: str, **kwargs) -> Optional[Message]:
    """edit_text без падения на 'message is not modified'."""
    try:
        return await message.edit_text(text, **kwargs)
    except TelegramBadRequest as error:
        err = str(error).lower()
        if "message is not modified" in err:
            return message
        if "there is no text in the message to edit" in err:
            return await message.answer(text, **kwargs)
        raise


async def safe_callback_edit(callback: CallbackQuery, text: str, **kwargs) -> None:
    """Безопасное редактирование сообщения из callback."""
    if not callback.message:
        await safe_callback_answer(callback, "Сообщение недоступно", show_alert=True)
        return
    await safe_edit_text(callback.message, text, **kwargs)


async def download_document_bytes(bot, document) -> bytes:
    """Скачивает документ из Telegram (aiogram 3.x)."""
    from io import BytesIO

    buffer = BytesIO()
    await bot.download(document, destination=buffer)
    return buffer.getvalue()
