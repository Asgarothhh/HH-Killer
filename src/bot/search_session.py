"""Активные поисковые сессии, отмена и слот одновременных поисков."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from src.models.models import JobInfo
from src.services.job_filters import DroppedJob


@dataclass
class SearchSession:
    user_id: int
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    partial_jobs: List[JobInfo] = field(default_factory=list)

    @property
    def is_cancelled(self) -> bool:
        return self.cancel_event.is_set()


_sessions: Dict[int, SearchSession] = {}
_last_dropped: Dict[int, List[DroppedJob]] = {}
_dropped_shown: Dict[int, bool] = {}

_search_lock = asyncio.Lock()
_active_searches = 0


def max_concurrent_searches() -> int:
    try:
        return max(1, int(os.getenv("SEARCH_MAX_CONCURRENT", "1")))
    except ValueError:
        return 1


async def try_acquire_search_slot() -> bool:
    """Занимает слот глобальной очереди поисков. False — все слоты заняты."""
    global _active_searches
    async with _search_lock:
        if _active_searches >= max_concurrent_searches():
            return False
        _active_searches += 1
        return True


async def release_search_slot() -> None:
    global _active_searches
    async with _search_lock:
        _active_searches = max(0, _active_searches - 1)


def start_session(user_id: int) -> SearchSession:
    session = SearchSession(user_id=user_id)
    _sessions[user_id] = session
    clear_dropped_jobs(user_id)
    return session


def get_session(user_id: int) -> Optional[SearchSession]:
    return _sessions.get(user_id)


def cancel_session(user_id: int) -> bool:
    session = _sessions.get(user_id)
    if not session:
        return False
    session.cancel_event.set()
    return True


def end_session(user_id: int) -> None:
    _sessions.pop(user_id, None)


def add_partial_job(user_id: int, job: JobInfo) -> None:
    session = _sessions.get(user_id)
    if not session:
        return
    key = job.source_url or f"{job.title}:{job.company}"
    existing = {j.source_url or f"{j.title}:{j.company}" for j in session.partial_jobs}
    if key not in existing:
        session.partial_jobs.append(job)


def save_dropped_jobs(user_id: int, items: List[DroppedJob]) -> None:
    _last_dropped[user_id] = list(items)
    _dropped_shown[user_id] = False


def get_dropped_jobs(user_id: int) -> List[DroppedJob]:
    return list(_last_dropped.get(user_id, []))


def mark_dropped_shown(user_id: int) -> bool:
    """Возвращает True, если отброшенные ещё не отправлялись."""
    if user_id not in _last_dropped:
        return False
    if _dropped_shown.get(user_id):
        return False
    _dropped_shown[user_id] = True
    return True


def clear_dropped_jobs(user_id: int) -> None:
    _last_dropped.pop(user_id, None)
    _dropped_shown.pop(user_id, None)
