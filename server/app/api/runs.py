# -*- coding: utf-8 -*-
"""执行记录与运行控制路由"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from .. import audit
from .. import auth
from .dependencies import _bearer_token, get_scheduler

router = APIRouter()


@router.get("/api/running", dependencies=[Depends(auth.auth_required)])
def list_running(type: Optional[str] = Query(None, alias="type"),
                 q: Optional[str] = Query(None),
                 status: Optional[str] = Query(None),
                 sort: Optional[str] = Query(None),
                 order: Optional[str] = Query("desc"),
                 page: int = Query(1, ge=1),
                 page_size: int = Query(100, ge=1, le=1000)):
    result = get_scheduler().running_view(job_type=type, q=q, status=status,
                                          sort=sort, order=order,
                                          offset=(page - 1) * page_size, limit=page_size)
    return {"total": result["total"], "page": page, "page_size": page_size,
            "items": result["items"], "alive_total": result["alive_total"],
            "counts": result["counts"]}


@router.get("/api/history", dependencies=[Depends(auth.auth_required)])
def history(status: Optional[str] = None, job: Optional[str] = None,
            type: Optional[str] = Query(None, alias="type"),
            q: Optional[str] = None,
            sort: Optional[str] = Query(None),
            order: Optional[str] = Query("desc"),
            page: int = Query(1, ge=1),
            page_size: int = Query(50, ge=1, le=500),
            all: bool = Query(False)):
    if all:
        result = get_scheduler().history_view(status=status, job_name=job,
                                              job_type=type, q=q, sort=sort, order=order,
                                              limit=200000)
        return {"total": result["total"], "page": 1, "page_size": result["total"], "items": result["items"]}
    return get_scheduler().history_view(status=status, job_name=job, job_type=type, q=q,
                                        sort=sort, order=order,
                                        offset=(page - 1) * page_size, limit=page_size)


@router.delete("/api/history", dependencies=[Depends(auth.auth_required)])
def delete_history(payload: dict = Body(...)):
    s = get_scheduler()
    if payload.get("all"):
        rows = s.store.query_runs(limit=200000)
        n = s.store.delete_runs([r.id for r in rows])
    else:
        ids = payload.get("ids") or []
        if not isinstance(ids, list) or not ids:
            raise HTTPException(status_code=400, detail="ids 不能为空")
        n = s.store.delete_runs([str(i) for i in ids])
    audit.audit("history.delete", obj="history",
                detail=f"count={n} all={'all' if payload.get('all') else 'partial'}")
    return {"ok": True, "deleted": n}


@router.get("/api/runs/{run_id}", dependencies=[Depends(auth.auth_required)])
def run_detail(run_id: str):
    s = get_scheduler()
    mem = s.get_running(run_id)
    if mem is not None:
        return mem.to_dict(include_log=True)
    run = s.store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run 不存在")
    return run.to_dict(include_log=True)


@router.post("/api/runs/{run_id}/abort", dependencies=[Depends(auth.auth_required)])
def abort_run(run_id: str, request: Request):
    ok = get_scheduler().abort(run_id)
    if not ok:
        raise HTTPException(status_code=404, detail="找不到可中止的运行实例")
    audit.audit("run.abort", operator=_bearer_token(request)[:6], obj=run_id)
    return {"ok": True}


@router.post("/api/runs/{run_id}/stop", dependencies=[Depends(auth.auth_required)])
def stop_run(run_id: str, request: Request):
    result = get_scheduler().stop(run_id)
    if not result.get("ok"):
        reason = str(result.get("reason") or "")
        if reason == "run_not_found":
            raise HTTPException(status_code=404, detail="找不到该运行记录")
        raise HTTPException(status_code=409, detail=reason or "停止失败")
    audit.audit("run.stop", operator=_bearer_token(request)[:6], obj=run_id,
                detail=result.get("reason", ""))
    return result
