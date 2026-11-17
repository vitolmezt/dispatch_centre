# -*- coding: utf-8 -*-
"""API 层公共依赖。

Scheduler 单例的持有者被移到这里，目的是让 api/* 各路由模块可以直接导入
get_scheduler()，而不必反向导入 main.py（避免循环导入）。

生命周期：
  main.py 的 startup 钩子调用 set_scheduler(Scheduler(...))
  main.py 的 shutdown 钩子调用 peek_scheduler() 后执行 shutdown()
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from fastapi import HTTPException, Request

from ..scheduler import Scheduler

if TYPE_CHECKING:
    from ..services.group_service import GroupService
    from ..services.job_service import JobService

_scheduler: Optional[Scheduler] = None


def set_scheduler(scheduler: Scheduler) -> None:
    """由 main.py 的 startup 钩子调用，注入全局 Scheduler 实例。"""
    global _scheduler
    _scheduler = scheduler


def get_scheduler() -> Scheduler:
    """获取全局 Scheduler；未初始化时返回 503。"""
    if _scheduler is None:
        raise HTTPException(status_code=503, detail="调度服务尚未初始化")
    return _scheduler


def peek_scheduler() -> Optional[Scheduler]:
    """只读探测：未初始化时返回 None，不抛异常（供 shutdown 使用）。"""
    return _scheduler


def get_job_service() -> "JobService":
    """任务写操作业务服务（每次请求按需构造，无状态，仅持门面回指）。"""
    from ..services.job_service import JobService
    return JobService(get_scheduler())


def get_group_service() -> "GroupService":
    """服务组业务服务（同上）。"""
    from ..services.group_service import GroupService
    return GroupService(get_scheduler())


def _bearer_token(request: Request) -> str:
    """从 Authorization 头取出 Bearer token（无则空串）。"""
    h = request.headers.get("Authorization", "")
    b = "Bearer "
    if h.startswith(b):
        return h[len(b):].strip()
    return ""
