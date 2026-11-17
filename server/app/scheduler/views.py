# -*- coding: utf-8 -*-
"""查询视图：任务 / 运行 / 历史 / 分组 / 服务组。

从 scheduler.py 迁出，逻辑逐行保持一致。

全部为**只读查询**，不修改任何状态。
数据来源统一经门面：
  jobs / running / service_groups / store —— 均由 Scheduler 持有，本模块不复制。
加锁亦使用门面的 self._sched._lock。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..models import ServiceGroup, job_to_api


class Views:
    """各类列表/详情视图的组装。"""

    def __init__(self, sched) -> None:
        self._sched = sched

    # ------------------------------------------------------------------
    # 任务视图
    # ------------------------------------------------------------------
    def jobs_view(self, *, sort: Optional[str] = None, order: str = "desc") -> List[dict]:
        s = self._sched
        with s._lock:
            items = []
            for job in s.jobs.values():
                running_count = sum(1 for r in s.running.values() if r.job_name == job.name)
                last = s.store.get_runs(limit=1, job_name=job.name)
                last_run = last[0] if last else None
                items.append(job_to_api(job, running_count, last_run, running_count > 0))
        return self._sort_jobs_api(items, sort, order)

    def _sort_jobs_api(self, items: List[dict], sort: Optional[str], order: str) -> List[dict]:
        field = sort if sort in ("created_at", "name") else "created_at"
        reverse = order != "asc"
        if field == "name":
            items.sort(key=lambda d: (d.get("name") or ""), reverse=reverse)
        else:
            items.sort(key=lambda d: (d.get("created_at") or d.get("updated_at") or ""),
                       reverse=reverse)
        return items

    def jobs_search(self, *, job_type: Optional[str] = None, enabled: Optional[str] = None,
                    q: Optional[str] = None, sort: Optional[str] = None, order: str = "desc",
                    offset: int = 0, limit: int = 50) -> dict:
        s = self._sched
        with s._lock:
            filtered = list(s.jobs.values())
        if job_type:
            filtered = [j for j in filtered if j.type.upper() == job_type.upper()]
        if enabled is not None and enabled != "":
            want = str(enabled).upper() in ("1", "YES", "ON", "TRUE")
            filtered = [j for j in filtered if j.enabled == want]
        if q:
            lq = q.lower()
            filtered = [j for j in filtered if lq in j.name.lower()]
        total = len(filtered)
        out = []
        for job in filtered:
            running_count = sum(1 for r in s.running.values() if r.job_name == job.name)
            last = s.store.get_runs(limit=1, job_name=job.name)
            last_run = last[0] if last else None
            out.append(job_to_api(job, running_count, last_run, running_count > 0))
        out = self._sort_jobs_api(out, sort, order)
        return {"total": total, "items": out[offset:offset + limit]}

    # ------------------------------------------------------------------
    # 分组视图
    # ------------------------------------------------------------------
    def groups_view(self) -> List[dict]:
        s = self._sched
        out = []
        for key, g in s.groups.items():
            count = sum(1 for j in s.jobs.values() if j.group == key)
            out.append({"key": key, **g, "count": count})
        return out

    # ------------------------------------------------------------------
    # 服务组视图
    # ------------------------------------------------------------------
    def service_groups_view(self) -> List[dict]:
        s = self._sched
        with s._lock:
            groups = list(s.service_groups.values())
        return [self.service_group_view(g) for g in groups]

    def service_group_view(self, group: ServiceGroup) -> dict:
        s = self._sched
        with s._lock:
            members = []
            for m in sorted(group.members, key=lambda x: int(x.get("order", 0))):
                jn = str(m.get("job_name") or "")
                job = s.jobs.get(jn)
                run = next((r for r in s.running.values()
                            if r.job_name == jn and r.status == "RUNNING"), None)
                members.append({
                    "job_name": jn,
                    "label": m.get("label") or jn,
                    "order": int(m.get("order", 0)),
                    "exists": job is not None,
                    "run_id": run.id if run else None,
                    "status": run.status if run else "STOPPED",
                    "pid": run.pid if run else None,
                    "start": run.start if run else None,
                    "last": self._group_member_last(jn),
                })
        return {
            "name": group.name,
            "description": group.description,
            "created_at": group.created_at,
            "members": members,
            "running": any(m["status"] == "RUNNING" for m in members),
        }

    def _group_member_last(self, job_name: str) -> Optional[dict]:
        last = self._sched.store.get_runs(limit=1, job_name=job_name)
        return (last[0].to_dict(include_log=False).get("status") if last else None)

    # ------------------------------------------------------------------
    # 运行中 / 历史
    # ------------------------------------------------------------------
    def get_running(self, run_id: str):
        s = self._sched
        with s._lock:
            return s.running.get(run_id)

    @staticmethod
    def _sort_key(obj, field: str):
        v = getattr(obj, field, None)
        if v is None:
            return ""
        return v

    def running_view(self, *, job_type: Optional[str] = None, q: Optional[str] = None,
                     status: Optional[str] = None,
                     sort: Optional[str] = None, order: str = "desc",
                     offset: int = 0, limit: int = 100) -> dict:
        s = self._sched
        runs = list(s.store.running_runs())
        if status:
            status = status.strip().upper()
            runs = [r for r in runs if r.status.upper() == status]
        if job_type:
            runs = [r for r in runs if r.job_type.upper() == job_type.upper()]
        if q:
            lq = q.lower()
            runs = [r for r in runs if lq in r.job_name.lower()]
        field = sort if sort in ("start", "name_created", "name") else "start"
        attr = {"start": "start", "name": "job_name"}.get(field, "start")
        runs.sort(key=lambda r: self._sort_key(r, attr), reverse=(order != "asc"))
        total = len(runs)
        sliced = runs[offset:offset + limit]
        counts: Dict[str, int] = {}
        for r in runs:
            counts[r.status] = counts.get(r.status, 0) + 1
        return {
            "total": total,
            "items": [r.to_dict(include_log=True) for r in sliced],
            "alive_total": s.store.running_process_count(),
            "counts": counts,
        }

    def history_view(self, status: Optional[str] = None, job_name: Optional[str] = None,
                     job_type: Optional[str] = None, q: Optional[str] = None,
                     sort: Optional[str] = None, order: str = "desc",
                     offset: int = 0, limit: int = 50) -> dict:
        s = self._sched
        total = s.store.count_runs(status=status, job_name=job_name,
                                   job_type=job_type, name_like=q)
        runs = s.store.query_runs(status=status, job_name=job_name, job_type=job_type,
                                  name_like=q, sort=sort, order=order,
                                  offset=offset, limit=limit)
        return {"total": total, "items": [r.to_dict(include_log=True) for r in runs]}
