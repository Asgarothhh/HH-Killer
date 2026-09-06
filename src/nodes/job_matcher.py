"""Оценка релевантности вакансий и сводка ИИ-агента."""

import asyncio
from typing import List

from src.models.models import AgentState, JobInfo, JobMatchResult, ResumeProfile
from src.services.job_filters import parse_job_required_experience
from src.services.search_query import SearchFilters
from src.utils.llm import create_chat_model
from src.utils.location_utils import RegionMatch, match_job_regions
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Не больше N одновременных запросов к LLM — иначе провайдер отдаёт 429
MAX_PARALLEL_SCORING = 4


def _filters_context(filters: SearchFilters | None) -> str:
    if not filters:
        return ""

    parts = [f"- Регион (обязателен, любой из): {filters.region_label}"]
    if filters.format_label:
        parts.append(f"- Формат работы: {filters.format_label}")
    if filters.employment_label:
        parts.append(f"- Тип занятости: {filters.employment_label}")
    if filters.experience_years is not None:
        parts.append(f"- Опыт кандидата: {filters.experience_years:g} лет")
    if filters.experience_job_min is not None or filters.experience_job_max is not None:
        jmin, jmax = filters.experience_job_min, filters.experience_job_max
        if jmin is not None and jmax is not None:
            parts.append(f"- Желаемый уровень вакансии: {jmin:g}–{jmax:g} лет опыта")
        elif jmin is not None:
            parts.append(f"- Желаемый уровень вакансии: от {jmin:g} лет")
        elif jmax is not None:
            parts.append(f"- Желаемый уровень вакансии: до {jmax:g} лет")
    if filters.salary_label:
        parts.append(f"- Зарплатные ожидания: {filters.salary_label}")
    return "\n".join(parts)


def _profile_context(profile: ResumeProfile | None) -> str:
    if not profile:
        return ""
    langs = ", ".join(profile.languages[:5]) if profile.languages else "—"
    history = (
        "; ".join(profile.work_history[:4])
        if profile.work_history
        else profile.experience_summary
    )
    return f"""
    Профиль кандидата:
    - Должность: {profile.desired_role}
    - Уровень: {profile.seniority_level or "не указан"}
    - Навыки: {", ".join(profile.skills[:15])}
    - Опыт: {profile.experience_years or "не указан"} лет — {history}
    - Локация: {profile.location or "не указана"}
    - Языки: {langs}
    - Отрасли: {", ".join(profile.industries[:5]) or "—"}
    """


async def _score_job(
    job: JobInfo,
    preference: str,
    profile: ResumeProfile | None,
    filters: SearchFilters | None = None,
) -> JobInfo:
    """Оценивает вакансию и готовит короткую сводку для карточки."""
    llm = create_chat_model()
    structured_llm = llm.with_structured_output(JobMatchResult)

    req_min, req_max = parse_job_required_experience(job.description or "")
    req_text = ""
    if req_min is not None:
        suffix = f"–{req_max:g}" if req_max else "+"
        req_text = f"- Требуемый опыт в вакансии: {req_min:g}{suffix} лет"

    region_label = filters.region_label if filters else "не задан"

    prompt = f"""
    Оцени релевантность вакансии для кандидата по шкале 0–100 и напиши сводку.

    Запрос пользователя: {preference}
    {_filters_context(filters)}
    {_profile_context(profile)}
    {req_text}

    Вакансия:
    - Название: {job.title}
    - Компания: {job.company}
    - Локация: {job.job_location or "не указана"}
    - Занятость: {job.employment_type or "не указана"}
    - Зарплата: {job.salary_range or "не указана"}
    - Описание: {(job.description or "")[:2500]}

    Правила оценки:
    1. Регион «{region_label}» обязателен: вакансия должна быть хотя бы в одном
       из этих мест. Если она в другом городе/стране — score < 20 и region_confirmed = false.
    2. Требуемый опыт значительно выше опыта кандидата — score < 40.
    3. Полное несоответствие должности или уровня — score < 20.
    4. Совпадение должности и навыков — главный фактор высокого score.
    5. Удалённая работа засчитывается только если пользователь выбрал удалёнку/гибрид.

    summary — сводка для кандидата на русском: 1–2 предложения (максимум 280 символов)
    о том, что за вакансия и почему она подходит или чем рискованна.
    Пиши по существу, без вводных фраз и без повторения названия компании.

    region_confirmed — подтверждает ли текст вакансии хотя бы один регион из «{region_label}».
    key_matches — конкретные совпадения; gaps — конкретные несоответствия.
    """

    try:
        result: JobMatchResult = await asyncio.to_thread(structured_llm.invoke, prompt)
    except Exception as error:
        logger.warning("Не удалось оценить вакансию «%s»: %s", job.title[:50], error)
        return job.model_copy(update={"match_score": 0.0, "match_reason": "Оценка недоступна"})

    score = result.match_score
    regions = filters.resolved_regions if filters else ()
    allow_remote = bool(filters and filters.allow_remote)

    if regions:
        verdict = match_job_regions(job, regions, allow_remote=allow_remote)
        if verdict != RegionMatch.MATCH or not result.region_confirmed:
            score = min(score, 15.0)

    if filters and filters.experience_years is not None and req_min is not None:
        if req_min > filters.experience_years + 2:
            score = min(score, 35.0)

    summary = (result.summary or result.match_reason or "").strip()
    if len(summary) > 400:
        summary = summary[:397].rstrip() + "…"

    return job.model_copy(
        update={
            "match_score": score,
            "match_reason": result.match_reason,
            "ai_summary": summary,
            "key_matches": result.key_matches,
            "match_gaps": result.gaps,
        }
    )


async def score_jobs_with_summary(
    jobs: List[JobInfo],
    preference: str,
    profile: ResumeProfile | None = None,
    filters: SearchFilters | None = None,
) -> List[JobInfo]:
    """Оценивает вакансии пачками и сортирует по релевантности."""
    if not jobs:
        return []

    semaphore = asyncio.Semaphore(MAX_PARALLEL_SCORING)

    async def _guarded(job: JobInfo) -> JobInfo:
        async with semaphore:
            return await _score_job(job, preference, profile, filters)

    scored = await asyncio.gather(*(_guarded(job) for job in jobs))
    return sorted(scored, key=lambda j: j.match_score or 0, reverse=True)


async def job_matcher(state: AgentState) -> dict:
    """Узел LangGraph: оценка найденных вакансий."""
    jobs = list(state.get("jobs_found", []))
    if not jobs:
        return {"status_message": "Нет вакансий для оценки"}

    logger.info("Оценка релевантности %d вакансий", len(jobs))
    scored = await score_jobs_with_summary(
        jobs,
        state.get("user_job_preference", ""),
        state.get("resume_profile"),
    )
    return {
        "jobs_found": scored,
        "status_message": f"Оценено вакансий: {len(scored)}",
    }
