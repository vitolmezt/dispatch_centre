# -*- coding: utf-8 -*-
"""任务管理路由"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from .. import audit
from .. import auth
from .. import constants
from ..models import JOB_PARAM_SCHEMA
from .dependencies import get_job_service, get_scheduler

if TYPE_CHECKING:
    from ..services.job_service import JobService

router = APIRouter()


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
def create_job(payload: dict = Body(...),
               svc: "JobService" = Depends(get_job_service)):
    return svc.create_job(payload)


@router.put("/api/jobs/{name}", dependencies=[Depends(auth.auth_required)])
def update_job(name: str, payload: dict = Body(...),
               svc: "JobService" = Depends(get_job_service)):
    return svc.update_job(name, payload)


@router.delete("/api/jobs/{name}", dependencies=[Depends(auth.auth_required)])
def delete_job(name: str, svc: "JobService" = Depends(get_job_service)):
    return svc.delete_job(name)


@router.post("/api/jobs/{name}/enable", dependencies=[Depends(auth.auth_required)])
def toggle_job(name: str, payload: dict = Body(...),
               svc: "JobService" = Depends(get_job_service)):
    enabled = bool(payload.get("enabled", True))
    return svc.set_enabled(name, enabled)


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
