"""Форматирование прогресса поиска для Telegram."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.utils.telegram_html import h


PHASE_LABELS = {
    "validating": "🔍 Проверка ссылок",
    "start_site": "🌐 Новый сайт",
    "apify": "🤖 Apify",
    "apify_fallback": "🤖 Apify fallback",
    "playwright": "🕵️ Stealth Playwright",
    "scoring": "🎯 Оценка релевантности",
    "done": "✅ Готово",
    "cancelled": "⏹ Остановлено",
}

# LangGraph node names → UI phase
NODE_PHASE_MAP = {
    "job_link_extractor": "playwright",
    "job_info_extractor": "playwright",
    "route_decision": "playwright",
    "start_site": "start_site",
    "apify": "apify",
    "apify_fallback": "apify_fallback",
    "playwright": "playwright",
    "scoring": "scoring",
}


def render_bar(ratio: float, width: int = 16) -> str:
    ratio = max(0.0, min(1.0, ratio))
    filled = round(width * ratio)
    empty = width - filled
    return f"{'█' * filled}{'░' * empty}"


@dataclass
class SearchProgressTracker:
    total_sites: int = 1
    current_site_index: int = 0
    current_site_name: str = ""
    jobs_found: int = 0
    pages_visited: int = 0
    max_jobs_target: int = 5
    phase: str = "validating"
    status_message: str = "Инициализация…"
    current_url: str = ""
    cancelled: bool = False
    city: Optional[str] = None
    salary_label: Optional[str] = None
    experience_label: Optional[str] = None
    step_count: int = 0
    max_steps: int = 40

    def site_progress(self) -> float:
        if self.total_sites <= 0:
            return 0.0

        site_fraction = 1.0 / self.total_sites
        completed_sites = min(self.current_site_index, self.total_sites) * site_fraction

        inner = 0.0
        per_site_jobs = max(1, self.max_jobs_target // max(self.total_sites, 1))
        if self.jobs_found > 0:
            inner = min(self.jobs_found / per_site_jobs, 1.0) * site_fraction
        elif self.max_steps > 0 and self.step_count > 0:
            inner = min(self.step_count / self.max_steps, 0.95) * site_fraction

        if self.phase == "scoring":
            return min(0.95, completed_sites + site_fraction * 0.9)
        if self.phase == "done":
            return 1.0

        return min(0.98, completed_sites + inner)

    def percent(self) -> int:
        if self.phase == "done":
            return 100
        if self.cancelled:
            return max(1, int(self.site_progress() * 100))
        return max(5, min(99, int(self.site_progress() * 100)))

    def update_from_node(self, node: str, state: dict, site_name: str = "") -> None:
        mapped = NODE_PHASE_MAP.get(node)
        if mapped:
            self.phase = mapped

        self.status_message = state.get("status_message") or self.status_message
        self.current_url = (state.get("current_page_url") or state.get("website") or "")[:80]
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
        pct = self.percent()
        bar = render_bar(pct / 100.0)
        phase = PHASE_LABELS.get(self.phase, self.phase)

        lines = [
            "<b>🔍 Поиск вакансий</b>",
            "",
            f"<code>{bar}</code>  <b>{pct}%</b>",
            "",
        ]

        if self.total_sites > 1:
            site_num = min(self.current_site_index + 1, self.total_sites)
            lines.append(f"📊 Сайт <b>{site_num}/{self.total_sites}</b> · {h(self.current_site_name or '—')}")
        elif self.current_site_name:
            lines.append(f"📊 {h(self.current_site_name)}")

        lines.extend([
            f"✅ Найдено: <b>{self.jobs_found}</b> вакансий",
            f"📄 Страниц обработано: <b>{self.pages_visited}</b>",
            f"⚙️ {h(phase)}: <i>{h(self.status_message[:100])}</i>",
        ])

        if self.current_url:
            lines.append(f"🔗 {h(self.current_url)}")

        filters = []
        if self.city:
            filters.append(f"📍 {h(self.city)}")
        if self.experience_label:
            filters.append(f"💼 {h(self.experience_label)}")
        if self.salary_label:
            filters.append(f"💰 {h(self.salary_label)}")
        if filters:
            lines.extend(["", " · ".join(filters)])

        if self.cancelled:
            lines.extend(["", "<i>⏹ Останавливаем… сохраняем найденное</i>"])

        return "\n".join(lines)
