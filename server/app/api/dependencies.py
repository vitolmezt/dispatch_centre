# -*- coding: utf-8 -*-
"""API 层公共依赖。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from fastapi import HTTPException, Request

from ..scheduler import Scheduler

if TYPE_CHECKING:
    from ..services.group_service import GroupService
    from ..services.job_service import JobService

_scheduler: Optional[Scheduler] = None


def set_scheduler(scheduler: Scheduler) -> None:
    global _scheduler
    _scheduler = scheduler


def get_scheduler() -> Scheduler:
    if _scheduler is None:
        raise HTTPException(status_code=503, detail="调度服务尚未初始化")
    return _scheduler


def peek_scheduler() -> Optional[Scheduler]:
    return _scheduler


def get_job_service() -> "JobService":
    from ..services.job_service import JobService
    return JobService(get_scheduler())


def get_group_service() -> "GroupService":
    from ..services.group_service import GroupService
    return GroupService(get_scheduler())


def _bearer_token(request: Request) -> str:
    h = request.headers.get("Authorization", "")
    b = "Bearer "
    if h.startswith(b):
        return h[len(b):].strip()
    return ""
