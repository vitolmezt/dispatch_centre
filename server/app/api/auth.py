# -*- coding: utf-8 -*-
"""认证路由（2 个端点）。

POST /api/login    公开 —— 用长期凭证换取短期会话 token
POST /api/logout   认证 —— 作废当前会话 token
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from .. import audit
from .. import auth
from .dependencies import _bearer_token

router = APIRouter()


@router.post("/api/login")
def login(payload: dict = Body(...), request: Request = None):
    ip = auth.client_ip(request) if request else "unknown"
    remaining = auth.login_locked(ip)
    if remaining > 0:
        audit.audit("login", obj=ip, detail=f"登录被限流（冷却剩余{int(remaining)}s）", ok=False)
        raise HTTPException(status_code=401, detail=f"登录尝试过于频繁，请 {int(remaining)} 秒后再试")
    provided = (payload.get("token") or "").strip()
    if not auth.token_configured():
        auth.record_login_fail(ip)
        audit.audit("login", obj=ip, detail="服务端未配置管理凭证", ok=False)
        raise HTTPException(status_code=401, detail="服务端未配置管理凭证")
    if not auth.authenticate(provided):
        auth.record_login_fail(ip)
        audit.audit("login", obj=ip, detail="登录凭证校验失败", ok=False)
        raise HTTPException(status_code=401, detail="凭证无效")
    auth.reset_login_fails(ip)
    sid = auth.issue_session()
    audit.audit("login", operator=sid[:6], obj=ip, detail="登录成功")
    return {"token": sid, "expires_in": int(auth.DEFAULT_SESSION_TTL), "configured": True}


@router.post("/api/logout", dependencies=[Depends(auth.auth_required)])
def logout(request: Request):
    sid = _bearer_token(request)
    if sid:
        auth.revoke_session(sid)
        audit.audit("logout", operator=sid[:6], detail="登出成功")
    return {"ok": True}
