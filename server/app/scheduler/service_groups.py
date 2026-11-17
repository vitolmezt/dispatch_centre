# -*- coding: utf-8 -*-
"""服务组执行协调：增删改 + 整组启停。"""
from __future__ import annotations

import logging

from ..models import ServiceGroup

logger = logging.getLogger("scheduler")


class ServiceGroupCoordinator:
    """服务组的持久化与整组启停编排。"""

    def __init__(self, sched) -> None:
        self._sched = sched

    # ------------------------------------------------------------------
    # 增删改
    # ------------------------------------------------------------------
    def add(self, group: ServiceGroup) -> None:
        s = self._sched
        with s._lock:
            s.service_groups[group.name] = group
            s.store.upsert_service_group(group)
        logger.info(f"service_group created: {group.name} ({len(group.members)} members)")

    def update(self, group: ServiceGroup) -> None:
        s = self._sched
        with s._lock:
            s.service_groups[group.name] = group
            s.store.upsert_service_group(group)
        logger.info(f"service_group updated: {group.name}")

    def delete(self, name: str) -> None:
        s = self._sched
        with s._lock:
            s.service_groups.pop(name, None)
            s.store.delete_service_group(name)
        logger.info(f"service_group deleted: {name}")

    # ------------------------------------------------------------------
    # 启动整组
    # ------------------------------------------------------------------
    def start(self, name: str) -> dict:
        s = self._sched
        with s._lock:
            group = s.service_groups.get(name)
        if group is None:
            return {"ok": False, "reason": "not_found"}

        started = []
        errors = []
        for m in sorted(group.members, key=lambda x: int(x.get("order", 0))):
            jn = str(m.get("job_name") or "")
            with s._lock:
                job = s.jobs.get(jn)
                if job is None:
                    errors.append(f"{jn}: 任务不存在")
                    continue
                if any(r.job_name == jn and r.status == "RUNNING"
                       for r in s.running.values()):
                    run = next(r for r in s.running.values()
                               if r.job_name == jn and r.status == "RUNNING")
                    started.append({"job_name": jn, "run_id": run.id,
                                    "status": "already_running"})
                    continue
            run = s._trigger_job(jn, "api")
            started.append({"job_name": jn, "run_id": run.id if run else None,
                            "status": "started" if run else "failed"})
            if run is None:
                errors.append(f"{jn}: 启动失败")
        return {"ok": not errors, "name": name, "started": started, "errors": errors}

    # ------------------------------------------------------------------
    # 停止整组
    # ------------------------------------------------------------------
    def stop(self, name: str) -> dict:
        s = self._sched
        with s._lock:
            group = s.service_groups.get(name)
        if group is None:
            return {"ok": False, "reason": "not_found"}

        stopped = []
        errors = []
        for m in group.members:
            jn = str(m.get("job_name") or "")
            with s._lock:
                run = next((r for r in s.running.values()
                            if r.job_name == jn and r.status == "RUNNING"), None)
            if run is None:
                continue
            res = s.stop(run.id)
            if res.get("ok"):
                stopped.append({"job_name": jn, "run_id": run.id,
                                "stopped": res.get("stop"),
                                "reason": res.get("reason")})
            else:
                errors.append(f"{jn}: 停止失败 ({res.get('reason')})")
        return {"ok": not errors, "name": name, "stopped": stopped, "errors": errors}
