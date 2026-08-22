"""Узел сопоставления вакансий с профилем пользователя."""

import asyncio
from typing import List

from src.models.models import AgentState, JobInfo, JobMatchResult, ResumeProfile
from src.services.job_filters import parse_job_required_experience
from src.services.search_query import SearchFilters
from src.utils.llm import create_chat_model
from src.utils.logger import get_logger
from src.utils.location_utils import job_matches_location

logger = get_logger(__name__)


def _filters_context(filters: SearchFilters | None) -> str:
    if not filters:
        return ""
    parts = []
    if filters.city:
        parts.append(f"- Требуемая локация: {filters.city} (удалёнка только если указано Remote)")
    if filters.experience_years is not None:
        parts.append(f"- Опыт кандидата: {filters.experience_years:g} лет")
    if filters.experience_job_min is not None or filters.experience_job_max is not None:
        jmin = filters.experience_job_min
        jmax = filters.experience_job_max
        if jmin is not None and jmax is not None:
            parts.append(f"- Желаемый уровень вакансии: {jmin:g}–{jmax:g} лет опыта")
        elif jmin is not None:
            parts.append(f"- Желаемый уровень вакансии: от {jmin:g} лет")
        elif jmax is not None:
            parts.append(f"- Желаемый уровень вакансии: до {jmax:g} лет")
    if filters.salary_label:
        parts.append(f"- Зарплатные ожидания: {filters.salary_label}")
    return "\n".join(parts)


async def _score_job(
    job: JobInfo,
    preference: str,
    profile: ResumeProfile | None,
    filters: SearchFilters | None = None,
) -> JobInfo:
    llm = create_chat_model()
    structured_llm = llm.with_structured_output(JobMatchResult)

    profile_text = ""
    if profile:
        langs = ", ".join(profile.languages[:5]) if profile.languages else "—"
        history = "; ".join(profile.work_history[:4]) if profile.work_history else profile.experience_summary
        profile_text = f"""
        Профиль кандидата:
        - Должность: {profile.desired_role}
        - Уровень: {profile.seniority_level or "не указан"}
        - Навыки: {", ".join(profile.skills[:15])}
        - Опыт: {profile.experience_years or "не указан"} лет — {history}
        - Локация: {profile.location or "не указана"}
        - Языки: {langs}
        - Отрасли: {", ".join(profile.industries[:5]) or "—"}
        """

    req_min, req_max = parse_job_required_experience(job.description)
    req_text = ""
    if req_min is not None:
        req_text = f"- Требуемый опыт в вакансии: {req_min:g}" + (f"–{req_max:g}" if req_max else "+") + " лет"

    filters_text = _filters_context(filters)

    prompt = f"""
    Оцени релевантность вакансии для кандидата по шкале 0–100.

    Запрос пользователя: {preference}
    {filters_text}
    {profile_text}
    {req_text}

    Вакансия:
    - Название: {job.title}
    - Компания: {job.company}
    - Локация: {job.job_location or "не указана"}
    - Описание: {job.description[:2500]}

    Правила оценки:
    1. Несовпадение города (если указан фильтр) — score < 30.
    2. Если требуемый опыт в вакансии значительно выше опыта кандидата — score < 40.
    3. Полное несоответствие должности/уровня (например manager vs support без опыта) — score < 20.
    4. Совпадение навыков и должности — главный фактор для высокого score.
    5. Удалённая работа засчитывается только если пользователь искал Remote/удалёнку.
    6. key_matches — конкретные совпадения; gaps — конкретные несоответствия.
    """

    result = await asyncio.to_thread(structured_llm.invoke, prompt)
    score = result.match_score

    # Дополнительные штрафы поверх LLM
    if filters and filters.city and not job_matches_location(job, filters.city):
        score = min(score, 25.0)

    if filters and filters.experience_years is not None and req_min is not None:
        if req_min > filters.experience_years + 2:
            score = min(score, 35.0)

    return job.model_copy(
        update={
            "match_score": score,
            "match_reason": result.match_reason,
            "key_matches": result.key_matches,
            "match_gaps": result.gaps,
        }
    )


async def job_matcher(state: AgentState) -> dict:
    """Оценивает релевантность найденных вакансий."""
    jobs = list(state.get("jobs_found", []))
    preference = state.get("user_job_preference", "")
    profile = state.get("resume_profile")

    if not jobs:
        return {"status_message": "Нет вакансий для оценки"}

    logger.info("Оценка релевантности %d вакансий", len(jobs))

    scored = await asyncio.gather(
        *[_score_job(job, preference, profile) for job in jobs]
    )
    scored_sorted = sorted(scored, key=lambda j: j.match_score or 0, reverse=True)

    return {
        "jobs_found": scored_sorted,
        "status_message": f"Оценено вакансий: {len(scored_sorted)}",
    }
