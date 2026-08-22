from dotenv import load_dotenv

from src.nodes.nodes import job_info_extractor, job_link_extractor
from src.models.models import AgentState, ResumeProfile
from src.utils.logger import get_logger
from src.utils.utils import is_job_detail_url

logger = get_logger(__name__)

load_dotenv()


def route_decision_node(state: AgentState) -> dict:
    """Увеличивает счётчик шагов и передаёт состояние маршрутизатору."""
    step_count = state.get("step_count", 0) + 1
    return {"step_count": step_count}


def decide_next_action(state: AgentState) -> str:
    """Выбирает следующий узел на основе текущего состояния обработки."""
    jobs_found = state.get("jobs_found", [])
    max_job = state.get("max_job", 5)
    links_to_visit = state.get("links_to_visit", [])
    error_count = state.get("error_count", 0)
    max_errors = state.get("max_errors", 10)
    step_count = state.get("step_count", 0)
    max_steps = max_job * 8

    jobs_count = len(jobs_found)
    is_complete = (
        jobs_count >= max_job
        or len(links_to_visit) == 0
        or error_count >= max_errors
        or step_count >= max_steps
    )

    if is_complete:
        reason = []
        if jobs_count >= max_job:
            reason.append(f"лимит вакансий ({jobs_count}/{max_job})")
        if len(links_to_visit) == 0:
            reason.append("очередь пуста")
        if error_count >= max_errors:
            reason.append(f"ошибки ({error_count})")
        logger.info("Обработка завершена: %s", "; ".join(reason))
        return "complete"

    if links_to_visit:
        temp_queue = list(links_to_visit)[:5]
        if any(is_job_detail_url(u) for u in temp_queue):
            return "extract_job_info"
        return "find_more_links"

    return "complete"


def create_job_scraper_graph():
    """Создаёт граф LangGraph для поиска и сбора вакансий."""
    from langgraph.constants import END
    from langgraph.graph import StateGraph

    workflow = StateGraph(AgentState)

    workflow.add_node("job_link_extractor", job_link_extractor)
    workflow.add_node("job_info_extractor", job_info_extractor)
    workflow.add_node("route_decision", route_decision_node)

    workflow.set_entry_point("job_link_extractor")

    workflow.add_edge("job_link_extractor", "route_decision")
    workflow.add_edge("job_info_extractor", "route_decision")

    workflow.add_conditional_edges(
        "route_decision",
        decide_next_action,
        {
            "extract_job_info": "job_info_extractor",
            "find_more_links": "job_link_extractor",
            "complete": END,
        },
    )

    return workflow.compile()


def build_initial_state(
    website: str,
    user_job_preference: str,
    max_jobs: int = 5,
    resume_profile: ResumeProfile | None = None,
    input_mode: str = "preference",
) -> AgentState:
    return AgentState(
        website=website,
        user_job_preference=user_job_preference,
        max_job=max_jobs,
        resume_profile=resume_profile,
        input_mode=input_mode,
        delay_between_requests=1.0,
        max_retries=3,
        max_errors=10,
        links_to_visit=[website],
        links_visited=set(),
        jobs_found=[],
        current_page_url=None,
        error_count=0,
        retry_count=0,
        last_request_time=None,
        status_message="Инициализация",
        step_count=0,
    )


async def stream_job_scraper(
    website: str,
    user_job_preference: str,
    max_jobs: int = 5,
    resume_profile: ResumeProfile | None = None,
    input_mode: str = "preference",
    on_progress=None,
    cancel_event=None,
):
    """Запускает поиск вакансий с опциональным callback прогресса."""
    initial_state = build_initial_state(
        website, user_job_preference, max_jobs, resume_profile, input_mode
    )
    graph = create_job_scraper_graph()
    final_state = dict(initial_state)

    try:
        async for event in graph.astream(initial_state):
            if cancel_event is not None and cancel_event.is_set():
                logger.info("Поиск на %s отменён пользователем", website[:50])
                break

            node_name = list(event.keys())[0] if event else "unknown"
            current_state = list(event.values())[0] if event else None

            if current_state:
                for key, value in current_state.items():
                    if key == "jobs_found" and isinstance(value, list):
                        final_state.setdefault("jobs_found", []).extend(value)
                    elif key == "links_visited" and isinstance(value, set):
                        final_state.setdefault("links_visited", set()).update(value)
                    else:
                        final_state[key] = value

                if on_progress:
                    await on_progress(node_name, final_state)
    except Exception as error:
        logger.error("Ошибка выполнения графа: %s", error)
        return final_state.get("jobs_found", [])

    return final_state.get("jobs_found", [])
