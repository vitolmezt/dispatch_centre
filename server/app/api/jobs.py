# -*- coding: utf-8 -*-
"""任务管理路由（8 个端点）。

GET    /api/jobs                    任务列表（分页 / 类型 / 启用 / 名称搜索 / 排序）
POST   /api/jobs                    新增任务
PUT    /api/jobs/{name}             更新任务（含改名）
DELETE /api/jobs/{name}             删除任务
POST   /api/jobs/{name}/enable      启用 / 停用
POST   /api/jobs/{name}/trigger     手动触发（阶段1B 后迁至 run_service）
GET    /api/job-schema              各类型的参数表单结构
GET    /api/groups                  任务分组元信息
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from .. import audit
from .. import auth
from .. import constants
from ..models import Job, JOB_PARAM_SCHEMA
from .dependencies import get_scheduler

router = APIRouter()


def _job_err(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


@router.get("/api/jobs", dependencies=[Depends(auth.auth_required)])
def list_jobs(type: Optional[str] = Query(None, alias="type"),
              enabled: Optional[str] = Query(None),
              q: Optional[str] = Query(None),
              sort: Optional[str] = Query(None),     # created_at | name，默认 created_at
              order: Optional[str] = Query("desc"),  # asc | desc
              page: int = Query(1, ge=1),
              page_size: int = Query(50, ge=1, le=500),
              all: bool = Query(False)):
    s = get_scheduler()
    if all:
        items = s.jobs_view(sort=sort, order=order)
        return {"total": len(items), "page": 1, "page_size": len(items), "items": items}
    offset = (page - 1) * page_size
    result = s.jobs_search(job_type=type, enabled=enabled, q=q, sort=sort, order=order,
                           offset=offset, limit=page_size)
    return {"total": result["total"], "page": page, "page_size": page_size, "items": result["items"]}


@router.post("/api/jobs", dependencies=[Depends(auth.auth_required)])
def create_job(payload: dict = Body(...)):
    s = get_scheduler()
    job = Job.from_dict(payload)
    errs = job.validate()
    if errs:
        raise _job_err("；".join(errs))
    if job.name in s.jobs:
        raise HTTPException(status_code=409, detail=f"任务名已存在: {job.name}")
    s.add_job(job)
    audit.audit("job.create", obj=job.name, detail=f"type={job.type}")
    return {"ok": True, "name": job.name}


@router.put("/api/jobs/{name}", dependencies=[Depends(auth.auth_required)])
def update_job(name: str, payload: dict = Body(...)):
    s = get_scheduler()
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
        raise _job_err("；".join(errs))
    s.update_job(job, old_name=name)
    detail = f"type={job.type}" + (f" renamed_from={name}" if new_name != name else "")
    audit.audit("job.update", obj=new_name, detail=detail)
    return {"ok": True, "name": job.name, "renamed": new_name != name}


@router.delete("/api/jobs/{name}", dependencies=[Depends(auth.auth_required)])
def delete_job(name: str):
    s = get_scheduler()
    if name not in s.jobs:
        raise HTTPException(status_code=404, detail=f"任务不存在: {name}")
    s.delete_job(name)
    audit.audit("job.delete", obj=name)
    return {"ok": True, "name": name}


@router.post("/api/jobs/{name}/enable", dependencies=[Depends(auth.auth_required)])
def toggle_job(name: str, payload: dict = Body(...)):
    s = get_scheduler()
    job = s.jobs.get(name)
    if job is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {name}")
    enabled = bool(payload.get("enabled", True))
    try:
        s.set_job_enabled(name, enabled)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    audit.audit("job.enable", obj=name, detail=f"enabled={enabled}")
    return {"ok": True, "name": name, "enabled": enabled}


@router.post("/api/jobs/{name}/trigger", dependencies=[Depends(auth.auth_required)])
def trigger_job(name: str):
    result = get_scheduler().trigger(name)
    if result is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if result == "blocked":
        raise HTTPException(status_code=409, detail="该任务正在执行中，请等待其结束后再触发")
    if result == "disabled":
        raise HTTPException(status_code=403, detail="该任务已停用，无法触发")
    audit.audit("job.trigger", obj=name, detail=f"run={result.id}")
    return {"ok": True, "run_id": result.id, "trigger": result.trigger}


@router.get("/api/job-schema", dependencies=[Depends(auth.auth_required)])
def job_schema():
    return {"types": constants.JOB_TYPE, "schemas": JOB_PARAM_SCHEMA}


@router.get("/api/groups", dependencies=[Depends(auth.auth_required)])
def list_groups():
    return get_scheduler().groups_view()
