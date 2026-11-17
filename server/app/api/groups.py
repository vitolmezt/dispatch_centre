# -*- coding: utf-8 -*-
"""服务组路由（7 个端点）。

GET    /api/service-groups             服务组列表
POST   /api/service-groups             新建服务组
PUT    /api/service-groups/{name}      更新服务组
DELETE /api/service-groups/{name}      删除服务组
GET    /api/service-groups/{name}      服务组详情
POST   /api/service-groups/{name}/start  启动整组
POST   /api/service-groups/{name}/stop   停止整组
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import audit
from .. import auth
from ..models import ServiceGroup
from .dependencies import get_scheduler

router = APIRouter()


def _job_err(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


@router.get("/api/service-groups", dependencies=[Depends(auth.auth_required)])
def list_service_groups():
    return get_scheduler().service_groups_view()


@router.post("/api/service-groups", dependencies=[Depends(auth.auth_required)])
def create_service_group(payload: dict = Body(...)):
    s = get_scheduler()
    group = ServiceGroup.from_dict(payload)
    errs = group.validate()
    if errs:
        raise _job_err("；".join(errs))
    if group.name in s.service_groups:
        raise HTTPException(status_code=409, detail=f"服务组已存在: {group.name}")
    s.add_service_group(group)
    audit.audit("service_group.create", obj=group.name, detail=f"members={len(group.members)}")
    return {"ok": True, "name": group.name}


@router.put("/api/service-groups/{name}", dependencies=[Depends(auth.auth_required)])
def update_service_group(name: str, payload: dict = Body(...)):
    s = get_scheduler()
    if name not in s.service_groups:
        raise HTTPException(status_code=404, detail=f"服务组不存在: {name}")
    data = dict(payload)
    data["name"] = name
    group = ServiceGroup.from_dict(data)
    errs = group.validate()
    if errs:
        raise _job_err("；".join(errs))
    s.update_service_group(group)
    audit.audit("service_group.update", obj=name, detail=f"members={len(group.members)}")
    return {"ok": True, "name": name}


@router.delete("/api/service-groups/{name}", dependencies=[Depends(auth.auth_required)])
def delete_service_group(name: str):
    s = get_scheduler()
    if name not in s.service_groups:
        raise HTTPException(status_code=404, detail=f"服务组不存在: {name}")
    s.delete_service_group(name)
    audit.audit("service_group.delete", obj=name)
    return {"ok": True, "name": name}


@router.get("/api/service-groups/{name}", dependencies=[Depends(auth.auth_required)])
def service_group_detail(name: str):
    s = get_scheduler()
    group = s.service_groups.get(name)
    if group is None:
        raise HTTPException(status_code=404, detail=f"服务组不存在: {name}")
    return s._service_group_view(group)


@router.post("/api/service-groups/{name}/start", dependencies=[Depends(auth.auth_required)])
def start_service_group(name: str):
    res = get_scheduler().start_service_group(name)
    if not res.get("ok"):
        raise HTTPException(status_code=404 if res.get("reason") == "not_found" else 400,
                            detail=res.get("errors") and "；".join(res["errors"]) or "启动失败")
    audit.audit("service_group.start", obj=name)
    return res


@router.post("/api/service-groups/{name}/stop", dependencies=[Depends(auth.auth_required)])
def stop_service_group(name: str):
    res = get_scheduler().stop_service_group(name)
    if not res.get("ok"):
        raise HTTPException(status_code=404 if res.get("reason") == "not_found" else 400,
                            detail=res.get("errors") and "；".join(res["errors"]) or "停止失败")
    audit.audit("service_group.stop", obj=name)
    return res
