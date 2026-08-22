"""Единая фабрика LLM-клиента для OpenRouter."""

from __future__ import annotations

import os
from typing import Optional

import httpx
from dotenv import load_dotenv

from src.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_MODEL = "google/gemini-2.5-flash-lite"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterAuthError(RuntimeError):
    """Невалидный или отсутствующий ключ OpenRouter."""


def get_openrouter_api_key() -> str:
    load_dotenv()
    key = (os.getenv("OPENROUTER_API_KEY") or "").strip().strip('"').strip("'")
    if not key:
        raise OpenRouterAuthError(
            "OPENROUTER_API_KEY не найден. Добавьте ключ в .env — "
            "https://openrouter.ai/keys"
        )
    if key.startswith("your_") or key == "your_openrouter_key":
        raise OpenRouterAuthError(
            "OPENROUTER_API_KEY не настроен (placeholder в .env). "
            "Замените на реальный ключ: https://openrouter.ai/keys"
        )
    return key


def get_openrouter_model() -> str:
    load_dotenv()
    return os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL).strip()


def create_chat_model(model: Optional[str] = None, temperature: float = 0):
    """Создаёт ChatOpenAI, настроенный на OpenRouter."""
    from langchain_openai import ChatOpenAI

    api_key = get_openrouter_api_key()
    model_name = model or get_openrouter_model()

    return ChatOpenAI(
        model=model_name,
        temperature=temperature,
        api_key=api_key,
        base_url=OPENROUTER_BASE_URL,
        default_headers={
            "HTTP-Referer": os.getenv("OPENROUTER_HTTP_REFERER", "https://github.com/HH-Killer"),
            "X-Title": os.getenv("OPENROUTER_APP_NAME", "HH-Killer"),
        },
    )


def validate_openrouter_key() -> str:
    """Проверяет ключ OpenRouter через API. Возвращает маскированный ключ."""
    api_key = get_openrouter_api_key()

    try:
        response = httpx.get(
            f"{OPENROUTER_BASE_URL}/auth/key",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15.0,
        )
    except httpx.HTTPError as error:
        logger.warning("Не удалось проверить OpenRouter (сеть): %s", error)
        return "*" * 10 + api_key[-4:]

    if response.status_code == 401:
        raise OpenRouterAuthError(
            "OPENROUTER_API_KEY недействителен (401 User not found). "
            "Создайте новый ключ: https://openrouter.ai/keys"
        )

    if response.status_code != 200:
        logger.warning("OpenRouter auth check: HTTP %s — %s", response.status_code, response.text[:200])
    else:
        data = response.json().get("data", {})
        label = data.get("label", "—")
        logger.info("OpenRouter ключ OK (label: %s)", label)

    logger.info("OpenRouter model: %s", get_openrouter_model())
    return "*" * 10 + api_key[-4:]
