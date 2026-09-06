import operator
import re
from datetime import datetime
from typing import Any, List, Set, Optional
from typing_extensions import TypedDict, Annotated
from pydantic import BaseModel, Field, field_validator


class JobInfo(BaseModel):
    title: str = Field(description="Название или должность вакансии")
    description: str = Field(description="Описание обязанностей и требований вакансии")
    application_info: str = Field(description="Информация о способе подачи заявки")
    company: str = Field(description="Название компании, разместившей вакансию")
    posted_date: Optional[datetime] = Field(
        default=None,
        description="Дата и время публикации вакансии",
    )
    source_url: Optional[str] = Field(
        default=None,
        description="Ссылка на исходную страницу вакансии",
    )
    match_score: Optional[float] = Field(
        default=None,
        description="Оценка релевантности вакансии (0–100)",
    )
    match_reason: Optional[str] = Field(
        default=None,
        description="Краткое объяснение релевантности",
    )
    job_location: Optional[str] = Field(
        default=None,
        description="Место работы, указанное в вакансии",
    )
    employment_type: Optional[str] = Field(
        default=None,
        description="Тип занятости: полный день, неполный день, контракт и т. д.",
    )
    salary_range: Optional[str] = Field(
        default=None,
        description="Информация о заработной плате, если указана",
    )
    key_matches: List[str] = Field(
        default_factory=list,
        description="Совпадающие навыки/опыт (из JobMatchResult)",
    )
    match_gaps: List[str] = Field(
        default_factory=list,
        description="Пробелы или несоответствия (из JobMatchResult)",
    )
    ai_summary: Optional[str] = Field(
        default=None,
        description="Краткая сводка ИИ-агента по вакансии для пользователя",
    )

    def to_extraction(self) -> "JobExtraction":
        """Приводит вакансию к схеме ответа JobExtraction."""
        return JobExtraction(
            job_title=self.title,
            company_name=drop_placeholder(self.company) or "",
            job_description=self.description,
            application_method=self.source_url or self.application_info,
            posted_date=self.posted_date.strftime("%d.%m.%Y") if self.posted_date else None,
            location=drop_placeholder(self.job_location),
            employment_type=drop_placeholder(self.employment_type),
            salary_range=drop_placeholder(self.salary_range),
        )


# Заглушки, которые LLM ставит вместо отсутствующих данных
_PLACEHOLDERS = {
    "", "-", "—", "–", "n/a", "n\\a", "na", "none", "null", "nil", "unknown",
    "не указано", "не указана", "не указан", "неизвестно", "нет данных",
    "nie podano", "nie określono", "brak", "brak danych", "not specified",
    "not provided", "not available", "no data",
}


def drop_placeholder(value: Optional[str]) -> Optional[str]:
    """Возвращает None, если в поле стоит заглушка вместо реальных данных."""
    text = str(value or "").strip()
    if text.strip(".,;:!").lower() in _PLACEHOLDERS:
        return None
    return text or None


def merge_sets(existing: Set[str], new: Set[str]) -> Set[str]:
    return existing | new


def parse_experience_years(value: Any) -> Optional[float]:
    """Преобразует опыт из LLM в число: '6+', '5-7', '10 lat' → float."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None

    text = str(value).strip().lower()
    if not text or text in {"—", "-", "n/a", "none", "null"}:
        return None

    range_match = re.search(r"(\d+(?:[.,]\d+)?)\s*[-–—]\s*(\d+(?:[.,]\d+)?)", text)
    if range_match:
        low = float(range_match.group(1).replace(",", "."))
        high = float(range_match.group(2).replace(",", "."))
        return round((low + high) / 2, 1)

    plus_match = re.search(r"(\d+(?:[.,]\d+)?)\s*\+", text)
    if plus_match:
        return float(plus_match.group(1).replace(",", "."))

    num_match = re.search(r"(\d+(?:[.,]\d+)?)", text)
    if num_match:
        return float(num_match.group(1).replace(",", "."))

    return None


class ResumeProfile(BaseModel):
    """Структурированный профиль из резюме пользователя."""
    full_name: Optional[str] = Field(default=None, description="Имя кандидата")
    desired_role: str = Field(description="Желаемая должность")
    skills: List[str] = Field(default_factory=list, description="Ключевые навыки")
    experience_years: Optional[float] = Field(
        default=None,
        description="Опыт в годах — только число (например 6 или 6.5), без '+' и текста",
    )
    experience_summary: str = Field(default="", description="Краткое описание опыта")
    education: Optional[str] = Field(default=None, description="Образование")
    location: Optional[str] = Field(default=None, description="Предпочитаемая локация")
    salary_expectation: Optional[str] = Field(default=None, description="Ожидания по зарплате")
    summary: str = Field(default="", description="Краткое резюме кандидата")
    raw_text: Optional[str] = Field(default=None, description="Исходный текст резюме")
    seniority_level: Optional[str] = Field(
        default=None,
        description="Уровень: junior, mid, senior, lead и т.д.",
    )
    languages: List[str] = Field(default_factory=list, description="Языки и уровень")
    industries: List[str] = Field(default_factory=list, description="Отрасли и домены")
    work_history: List[str] = Field(
        default_factory=list,
        description="Краткий список последних мест работы (компания — роль)",
    )

    @field_validator("experience_years", mode="before")
    @classmethod
    def coerce_experience_years(cls, value: Any) -> Optional[float]:
        return parse_experience_years(value)

    @field_validator("skills", mode="before")
    @classmethod
    def coerce_skills(cls, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [s.strip() for s in re.split(r"[,;|/]", value) if s.strip()]
        if isinstance(value, list):
            return [str(s).strip() for s in value if str(s).strip()]
        return []

    @field_validator("languages", "industries", "work_history", mode="before")
    @classmethod
    def coerce_string_lists(cls, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [s.strip() for s in re.split(r"[,;|\n]", value) if s.strip()]
        if isinstance(value, list):
            return [str(s).strip() for s in value if str(s).strip()]
        return []


class JobMatchResult(BaseModel):
    """Результат сопоставления вакансии с профилем."""
    match_score: float = Field(ge=0, le=100, description="Оценка релевантности 0–100")
    match_reason: str = Field(description="Краткое объяснение оценки")
    summary: str = Field(
        default="",
        description="Сводка для кандидата: 1–2 предложения о вакансии и её соответствии",
    )
    region_confirmed: bool = Field(
        default=True,
        description="Подтверждает ли текст вакансии требуемый регион",
    )
    key_matches: List[str] = Field(default_factory=list, description="Совпадающие навыки/опыт")
    gaps: List[str] = Field(default_factory=list, description="Пробелы или несоответствия")


class AgentState(TypedDict):
    # ── статические входные данные ──────────────────────────
    website: str
    user_job_preference: str
    max_job: int
    resume_profile: Optional[ResumeProfile]
    input_mode: str  # "preference" | "resume"

    # ── ограничение частоты запросов и обработка ошибок ─────
    delay_between_requests: float
    max_retries: int
    max_errors: int

    # ── динамические данные ─────────────────
    links_to_visit: List[str]
    links_visited: Annotated[Set[str], merge_sets]
    jobs_found: Annotated[List[JobInfo], operator.add]

    # ── поля отслеживания состояния ─────────────────────────
    current_page_url: Optional[str]
    error_count: int
    retry_count: int
    last_request_time: Optional[datetime]
    status_message: str
    step_count: int
    

class JobExtraction(BaseModel):
    """Структура для извлечения сведений о вакансии."""
    job_title: str = Field(description="Название или должность вакансии")
    company_name: str = Field(description="Название компании, разместившей вакансию")
    job_description: str = Field(
        description="Краткое описание обязанностей и требований",
    )
    application_method: Optional[str] = Field(
        default=None,
        description="Способ подачи заявки: ссылка, электронная почта или инструкция",
    )
    posted_date: Optional[str] = Field(
        default=None,
        description="Дата публикации вакансии",
    )
    location: Optional[str] = Field(default=None, description="Место работы")
    employment_type: Optional[str] = Field(
        default=None,
        description="Тип занятости: полный день, неполный день, контракт и т. д.",
    )
    salary_range: Optional[str] = Field(
        default=None,
        description="Информация о заработной плате, если она указана",
    )


class LinksCategorization(BaseModel):
    """Структура для категоризации извлечённых ссылок."""
    job_detail_links: List[str] = Field(
        default_factory=list,
        description="Ссылки на страницы с подробностями отдельных вакансий",
    )
    job_listing_pages: List[str] = Field(
        default_factory=list,
        description="Ссылки на страницы со списками нескольких вакансий",
    )
    navigation_links: List[str] = Field(
        default_factory=list,
        description="Ссылки для перехода по страницам или к дополнительным вакансиям",
    )