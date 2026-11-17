# -*- coding: utf-8 -*-
"""系统信息路由（4 个端点）。

GET /api/constants   公开 —— 枚举字典（状态 / 类型 / 启用）
GET /api/health      公开 —— 健康检查
GET /api/config      公开 —— 启动配置信息
GET /api/audit       认证 —— 审计日志（最新 N 条）
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from .. import audit
from .. import auth
from .. import constants
from .dependencies import get_scheduler

router = APIRouter()


@router.get("/api/constants")
def constants_dict():
    return {
        "groups": constants.ALL_GROUPS,
        "capabilities": {"fsPick": True},
    }


@router.get("/api/health")
def health():
    return {"status": "ok", "running": len(get_scheduler().running)}


@router.get("/api/config")
def config_info():
    s = get_scheduler()
    return {"config_path": str(s.config_path),
            "jobs_count": len(s.jobs)}


@router.get("/api/audit", dependencies=[Depends(auth.auth_required)])
def audit_logs(limit: int = Query(200, ge=1, le=5000)):
    return {"items": audit.read_recent(limit=limit)}
