import asyncio

from src.graph import build_initial_state, create_job_scraper_graph, stream_job_scraper
from src.utils.logger import get_logger, setup_applevel_logger
from src.utils.utils import validate_environment

logger = get_logger(__name__)


async def run_job_scraper(
    website: str,
    user_job_preference: str,
    max_jobs: int = 5,
    stream: bool = True,
):
    """Запускает сбор вакансий с потоковой передачей состояния или без неё."""
    if stream:
        return await stream_job_scraper(website, user_job_preference, max_jobs)

    initial_state = build_initial_state(website, user_job_preference, max_jobs)
    graph = create_job_scraper_graph()

    logger.info("Запуск поиска вакансий: %s", website)

    try:
        final_state = await graph.ainvoke(initial_state)
        return final_state.get("jobs_found", [])
    except Exception as error:
        logger.error("Ошибка выполнения сбора вакансий: %s", error)
        return []


if __name__ == "__main__":
    try:
        setup_applevel_logger(file_name="job_scraper.log")
        validate_environment()
        jobs = asyncio.run(run_job_scraper(
            website="https://hh.ru/",
            user_job_preference=(
                "Удалённая работа Python-разработчиком "
                "в области машинного обучения или анализа данных"
            ),
            max_jobs=5,
        ))
        for i, job in enumerate(jobs, 1):
            print(f"{i}. {job.title} @ {job.company} — {job.source_url}")
    except Exception as error:
        logger.error("Ошибка запуска приложения: %s", error)