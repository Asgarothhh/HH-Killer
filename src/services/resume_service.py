"""Сервис анализа резюме пользователя."""

import asyncio
import io
from pathlib import Path

from src.models.models import ResumeProfile
from src.utils.llm import OpenRouterAuthError, create_chat_model
from src.utils.logger import get_logger

logger = get_logger(__name__)

RESUME_ANALYSIS_PROMPT = """
Ты — HR-аналитик. Проанализируй резюме кандидата и извлеки структурированный профиль для поиска вакансий.

Текст резюме:
{text}

Инструкции:
1. desired_role — целевая должность (на русском или английском, как в резюме).
2. skills — до 20 ключевых навыков (технологии, инструменты, soft skills).
3. experience_years — общий стаж ТОЛЬКО числом (например 6 или 6.5), без «+» и текста.
4. experience_summary — 2–4 предложения о карьере и ключевых достижениях.
5. seniority_level — junior / mid / senior / lead / principal (по стажу и ролям).
6. work_history — до 5 записей «Компания — должность (годы)».
7. languages — языки с уровнем, например «English B2», «Polski A2».
8. industries — отрасли: fintech, e-commerce, healthcare и т.д.
9. location — предпочитаемый город/страна работы, если указано.
10. salary_expectation — ожидания по зарплате, если есть.
11. summary — краткий профиль кандидата в 1–2 предложения для поискового запроса.

Если данных нет — оставь поле пустым или null, не выдумывай.
"""


async def extract_text_from_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            parts.append(text)
    return "\n".join(parts)


async def extract_text_from_docx(data: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(data))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


async def extract_text_from_file(filename: str, data: bytes) -> str:
    """Извлекает текст из PDF, DOCX или plain text."""
    ext = Path(filename).suffix.lower()

    if ext == ".pdf":
        return await extract_text_from_pdf(data)
    if ext in {".docx", ".doc"}:
        return await extract_text_from_docx(data)
    if ext in {".txt", ".md"}:
        return data.decode("utf-8", errors="replace")

    raise ValueError(f"Неподдерживаемый формат файла: {ext}. Используйте PDF, DOCX или TXT.")


async def analyze_resume_text(text: str) -> ResumeProfile:
    """Структурирует текст резюме через LLM."""
    llm = create_chat_model()
    structured_llm = llm.with_structured_output(ResumeProfile)

    prompt = RESUME_ANALYSIS_PROMPT.format(text=text[:14000])
    result: ResumeProfile = await asyncio.to_thread(structured_llm.invoke, prompt)
    result = result.model_copy(update={"raw_text": text[:14000]})
    logger.info(
        "Резюме проанализировано: %s, опыт %s лет, %d навыков",
        result.desired_role,
        result.experience_years,
        len(result.skills),
    )
    return result


async def analyze_resume_file(filename: str, data: bytes) -> ResumeProfile:
    """Извлекает текст из файла и анализирует резюме."""
    text = await extract_text_from_file(filename, data)
    if not text.strip():
        raise ValueError("Не удалось извлечь текст из файла. Проверьте формат.")
    return await analyze_resume_text(text)


def profile_to_search_query(profile: ResumeProfile) -> str:
    """Формирует поисковый запрос из профиля резюме."""
    parts = [profile.desired_role]
    if profile.seniority_level:
        parts.append(profile.seniority_level)
    if profile.skills:
        parts.append(", ".join(profile.skills[:8]))
    if profile.industries:
        parts.append(", ".join(profile.industries[:3]))
    if profile.summary:
        parts.append(profile.summary[:180])
    return " ".join(p for p in parts if p)


def format_llm_error(error: Exception) -> str:
    """Понятное сообщение об ошибке LLM для пользователя."""
    if isinstance(error, OpenRouterAuthError):
        return str(error)

    text = str(error).lower()
    if "401" in text or "user not found" in text:
        return (
            "❌ Ключ OpenRouter недействителен (401).\n\n"
            "1. Откройте https://openrouter.ai/keys\n"
            "2. Создайте новый API key\n"
            "3. Вставьте в .env: OPENROUTER_API_KEY=sk-or-v1-...\n"
            "4. Перезапустите бота"
        )
    if "402" in text or "insufficient" in text or "credit" in text:
        return (
            "❌ Недостаточно кредитов OpenRouter.\n"
            "Пополните баланс: https://openrouter.ai/credits "
            "или задайте бесплатную модель: OPENROUTER_MODEL=google/gemini-2.0-flash-exp:free"
        )
    return f"❌ Ошибка AI: {error}"
