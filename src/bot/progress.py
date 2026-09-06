"""Отображение прогресса поиска в Telegram."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.utils.telegram_html import h

PHASE_LABELS = {
    "validating": "Проверяю ссылки",
    "start_site": "Открываю сайт",
    "apify": "Ищу через Apify",
    "apify_fallback": "Обхожу блокировку через Apify",
    "playwright": "Собираю вакансии",
    "filtering": "Проверяю регион и фильтры",
    "scoring": "Оцениваю релевантность",
    "done": "Готово",
    "cancelled": "Останавливаюсь",
}

NODE_PHASE_MAP = {
    "job_link_extractor": "playwright",
    "job_info_extractor": "playwright",
    "route_decision": "playwright",
    "start_site": "start_site",
    "apify": "apify",
    "apify_fallback": "apify_fallback",
    "playwright": "playwright",
    "filtering": "filtering",
    "scoring": "scoring",
}


def render_bar(ratio: float, width: int = 14) -> str:
    ratio = max(0.0, min(1.0, ratio))
    filled = round(width * ratio)
    return f"{'█' * filled}{'░' * (width - filled)}"


@dataclass
class SearchProgressTracker:
    total_sites: int = 1
    current_site_index: int = 0
    current_site_name: str = ""
    jobs_found: int = 0
    pages_visited: int = 0
    max_jobs_target: int = 5
    phase: str = "validating"
    status_message: str = "Начинаю…"
    cancelled: bool = False
    region: Optional[str] = None
    filters_line: Optional[str] = None
    step_count: int = 0
    max_steps: int = 40

    def site_progress(self) -> float:
        if self.total_sites <= 0:
            return 0.0

        site_fraction = 1.0 / self.total_sites
        completed = min(self.current_site_index, self.total_sites) * site_fraction

        inner = 0.0
        per_site_jobs = max(1, self.max_jobs_target // max(self.total_sites, 1))
        if self.jobs_found > 0:
            inner = min(self.jobs_found / per_site_jobs, 1.0) * site_fraction
        elif self.max_steps > 0 and self.step_count > 0:
            inner = min(self.step_count / self.max_steps, 0.95) * site_fraction

        if self.phase in {"filtering", "scoring"}:
            return min(0.95, completed + site_fraction * 0.9)
        if self.phase == "done":
            return 1.0
        return min(0.98, completed + inner)

    def percent(self) -> int:
        if self.phase == "done":
            return 100
        return max(5, min(99, int(self.site_progress() * 100)))

    def update_from_node(self, node: str, state: dict, site_name: str = "") -> None:
        mapped = NODE_PHASE_MAP.get(node)
        if mapped:
            self.phase = mapped

        self.status_message = state.get("status_message") or self.status_message
        self.step_count = state.get("step_count", self.step_count)

        pages = state.get("links_visited", set())
        if isinstance(pages, set):
            self.pages_visited = max(self.pages_visited, len(pages))

        if site_name:
            self.current_site_name = site_name

    def set_site(self, index: int, name: str) -> None:
        self.current_site_index = index
        self.current_site_name = name
        self.step_count = 0

    def to_html(self) -> str:
        percent = self.percent()
        phase = PHASE_LABELS.get(self.phase, self.phase)

        lines = [
            f"<b>🔍 Ищу вакансии</b> · {percent}%",
            f"<code>{render_bar(percent / 100.0)}</code>",
            "",
            f"⚙️ {h(phase)}",
        ]

        if self.current_site_name:
            position = (
                f" ({min(self.current_site_index + 1, self.total_sites)}/{self.total_sites})"
                if self.total_sites > 1 else ""
            )
            lines.append(f"🌐 {h(self.current_site_name)}{position}")

        lines.append(f"✅ Собрано: <b>{self.jobs_found}</b> · страниц: {self.pages_visited}")

        if self.filters_line:
            lines += ["", f"<i>{h(self.filters_line)}</i>"]

        if self.cancelled:
            lines += ["", "<i>⏹ Останавливаюсь, сохраняю найденное…</i>"]

        return "\n".join(lines)
