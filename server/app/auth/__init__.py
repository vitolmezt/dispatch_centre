# -*- coding: utf-8 -*-
"""认证模块。"""
from __future__ import annotations

from fastapi import HTTPException, Request

from . import session as _session_mod
from . import throttle as _throttle_mod
from . import token as _token_mod
from .throttle import client_ip
from .token import DEFAULT_SESSION_TTL, LONG_TERM_TOKEN, token_configured, authenticate

__all__ = [
    "auth_required", "client_ip",
    "token_configured", "authenticate", "LONG_TERM_TOKEN", "DEFAULT_SESSION_TTL",
    "issue_session", "revoke_session", "check_session",
    "login_locked", "record_login_fail", "reset_login_fails", "reset_login_throttle",
]


def issue_session() -> str:
    return _session_mod.issue_session()


def revoke_session(sid: str) -> None:
    _session_mod.revoke_session(sid)


def check_session(sid: str) -> bool:
    return _session_mod.check_session(sid)


def login_locked(ip: str) -> float:
    return _throttle_mod.login_locked(ip)


def record_login_fail(ip: str) -> None:
    _throttle_mod.record_login_fail(ip)


def reset_login_fails(ip: str) -> None:
    _throttle_mod.reset_login_fails(ip)


def reset_login_throttle() -> None:
    _throttle_mod.reset_login_throttle()

async def auth_required(request: Request) -> str:

    auth = request.headers.get("Authorization", "")
    bearer = "Bearer "
    if not auth.startswith(bearer):
        raise HTTPException(status_code=401, detail="未认证：缺少 Authorization 头")
    sid = auth[len(bearer):].strip()
    if not sid or not check_session(sid):
        raise HTTPException(status_code=401, detail="会话无效或已过期")
    return sid
