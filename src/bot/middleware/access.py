"""Whitelist Telegram-пользователей по ALLOWED_USER_IDS."""

from __future__ import annotations

import os
from typing import Any, Awaitable, Callable, Dict, Set

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from src.utils.logger import get_logger
from src.utils.telegram_html import h

logger = get_logger(__name__)

DENIED_TEXT = (
    "⛔ <b>Нет доступа</b>\n\n"
    "Ваш Telegram ID: <code>{user_id}</code>\n"
    "Передайте его администратору, чтобы вас добавили в список."
)


def load_allowed_user_ids() -> Set[int]:
    """Читает обязательный список ID из ALLOWED_USER_IDS."""
    raw = (os.getenv("ALLOWED_USER_IDS") or "").strip()
    if not raw:
        raise ValueError(
            "ALLOWED_USER_IDS не задан. Укажите Telegram user ID через запятую, "
            "например: ALLOWED_USER_IDS=123456789,987654321"
        )

    ids: Set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError as exc:
            raise ValueError(
                f"Некорректный ID в ALLOWED_USER_IDS: {part!r}"
            ) from exc

    if not ids:
        raise ValueError("ALLOWED_USER_IDS пуст")
    return ids


class AccessMiddleware(BaseMiddleware):
    """Пропускает только пользователей из whitelist."""

    def __init__(self, allowed_ids: Set[int]) -> None:
        self.allowed_ids = allowed_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        if user.id in self.allowed_ids:
            return await handler(event, data)

        username = f"@{user.username}" if user.username else "—"
        logger.warning("Отклонён пользователь %s (%s)", user.id, username)

        text = DENIED_TEXT.format(user_id=h(str(user.id)))
        try:
            if isinstance(event, Message):
                await event.answer(text, parse_mode="HTML")
            elif isinstance(event, CallbackQuery):
                await event.answer("Нет доступа", show_alert=True)
                if event.message:
                    await event.message.answer(text, parse_mode="HTML")
        except Exception as notify_error:
            logger.error("Не удалось ответить отказанному пользователю: %s", notify_error)

        return None
