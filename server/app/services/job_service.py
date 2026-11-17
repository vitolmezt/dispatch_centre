# -*- coding: utf-8 -*-
"""任务管理业务服务。

承载 4 个写操作接口的业务编排（校验 → 冲突检查 → 调用 Scheduler → 审计）：

  create_job    POST   /api/jobs
  update_job    PUT    /api/jobs/{name}
  delete_job    DELETE /api/jobs/{name}
  set_enabled   POST   /api/jobs/{name}/enable

不在本模块：
  GET  /api/jobs        纯查询（仅组装分页参数）→ API 直接调 Scheduler
  POST /api/jobs/{name}/trigger  运行控制 → 暂由 API 直接调 Scheduler（run_service 暂缓）

本层抛 HTTPException 之外的业务异常时，由 API 层统一映射为 HTTP 状态码；
为保持既有契约完全不变，这里直接抛与原来相同的 HTTPException。
"""
from __future__ import annotations

from fastapi import HTTPException

from .. import audit
from ..models import Job
from ..scheduler import Scheduler


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


class JobService:
    """任务写操作。构造时注入 Scheduler 门面，不自行持有任何状态。"""

    def __init__(self, scheduler: Scheduler):
        self._sched = scheduler

    # ------------------------------------------------------------------
    # 创建
    # ------------------------------------------------------------------
    def create_job(self, payload: dict) -> dict:
        s = self._sched
        job = Job.from_dict(payload)
        errs = job.validate()
        if errs:
            raise _bad_request("；".join(errs))
        if job.name in s.jobs:
            raise HTTPException(status_code=409, detail=f"任务名已存在: {job.name}")
        s.add_job(job)
        audit.audit("job.create", obj=job.name, detail=f"type={job.type}")
        return {"ok": True, "name": job.name}

    # ------------------------------------------------------------------
    # 更新（含改名）
    # ------------------------------------------------------------------
    def update_job(self, name: str, payload: dict) -> dict:
        s = self._sched
        old = s.jobs.get(name)
        if old is None:
            raise HTTPException(status_code=404, detail=f"任务不存在: {name}")

        data = dict(payload)
        new_name = str(data.get("name") or "").strip() or name
        data["name"] = new_name
        if new_name != name and new_name in s.jobs:
            raise HTTPException(status_code=409, detail=f"任务名已存在: {new_name}")

        job = Job.from_dict(data)
        errs = job.validate()
        if errs:
            raise _bad_request("；".join(errs))

        s.update_job(job, old_name=name)
        detail = f"type={job.type}" + (f" renamed_from={name}" if new_name != name else "")
        audit.audit("job.update", obj=new_name, detail=detail)
        return {"ok": True, "name": job.name, "renamed": new_name != name}

    # ------------------------------------------------------------------
    # 删除
    # ------------------------------------------------------------------
    def delete_job(self, name: str) -> dict:
        s = self._sched
        if name not in s.jobs:
            raise HTTPException(status_code=404, detail=f"任务不存在: {name}")
        s.delete_job(name)
        audit.audit("job.delete", obj=name)
        return {"ok": True, "name": name}

    # ------------------------------------------------------------------
    # 启用 / 停用
    # ------------------------------------------------------------------
    def set_enabled(self, name: str, enabled: bool) -> dict:
        s = self._sched
        job = s.jobs.get(name)
        if job is None:
            raise HTTPException(status_code=404, detail=f"任务不存在: {name}")
        try:
            s.set_job_enabled(name, enabled)
        except ValueError as e:
            # Scheduler 对「运行中不可停用」抛 ValueError → 409
            raise HTTPException(status_code=409, detail=str(e))
        audit.audit("job.enable", obj=name, detail=f"enabled={enabled}")
        return {"ok": True, "name": name, "enabled": enabled}
