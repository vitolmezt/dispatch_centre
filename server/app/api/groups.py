# -*- coding: utf-8 -*-
"""服务组路由（7 个端点）。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Body, Depends

from .. import auth
from .dependencies import get_group_service, get_scheduler

if TYPE_CHECKING:
    from ..services.group_service import GroupService

router = APIRouter()


@router.get("/api/service-groups", dependencies=[Depends(auth.auth_required)])
def list_service_groups():
    return get_scheduler().service_groups_view()


@router.post("/api/service-groups", dependencies=[Depends(auth.auth_required)])
def create_service_group(payload: dict = Body(...),
                         svc: "GroupService" = Depends(get_group_service)):
    return svc.create_group(payload)


@router.put("/api/service-groups/{name}", dependencies=[Depends(auth.auth_required)])
def update_service_group(name: str, payload: dict = Body(...),
                         svc: "GroupService" = Depends(get_group_service)):
    return svc.update_group(name, payload)


@router.delete("/api/service-groups/{name}", dependencies=[Depends(auth.auth_required)])
def delete_service_group(name: str, svc: "GroupService" = Depends(get_group_service)):
    return svc.delete_group(name)


@router.get("/api/service-groups/{name}", dependencies=[Depends(auth.auth_required)])
def service_group_detail(name: str, svc: "GroupService" = Depends(get_group_service)):
    return svc.get_detail(name)


@router.post("/api/service-groups/{name}/start", dependencies=[Depends(auth.auth_required)])
def start_service_group(name: str, svc: "GroupService" = Depends(get_group_service)):
    return svc.start_group(name)


@router.post("/api/service-groups/{name}/stop", dependencies=[Depends(auth.auth_required)])
def stop_service_group(name: str, svc: "GroupService" = Depends(get_group_service)):
    return svc.stop_group(name)
