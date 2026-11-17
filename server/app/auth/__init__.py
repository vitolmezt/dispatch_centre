# -*- coding: utf-8 -*-
"""认证模块。

阶段5 从 auth.py 拆出，按职责分为三个子模块：

  token.py     长期管理凭证的加载/生成/校验（含密钥文件权限收紧）
  session.py   短期会话 token 的签发/校验/注销
  throttle.py  登录失败限流（滑动窗口 + 冷却）

兼容性：以下公开接口保持不变（api/auth.py、main.py、测试均直接使用）
  auth_required    FastAPI 依赖（Bearer 校验）
  client_ip        客户端 IP 解析
  token_configured / authenticate
  issue_session / revoke_session / check_session
  login_locked / record_login_fail / reset_login_fails / reset_login_throttle
  DEFAULT_SESSION_TTL / LONG_TERM_TOKEN
"""
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


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------
def issue_session() -> str:
    return _session_mod.issue_session()


def revoke_session(sid: str) -> None:
    _session_mod.revoke_session(sid)


def check_session(sid: str) -> bool:
    return _session_mod.check_session(sid)


# ---------------------------------------------------------------------------
# 登录限流
# ---------------------------------------------------------------------------
def login_locked(ip: str) -> float:
    """登录是否被限流（返回剩余冷却秒数，0 表示未限流）。"""
    return _throttle_mod.login_locked(ip)


def record_login_fail(ip: str) -> None:
    _throttle_mod.record_login_fail(ip)


def reset_login_fails(ip: str) -> None:
    _throttle_mod.reset_login_fails(ip)


def reset_login_throttle() -> None:
    _throttle_mod.reset_login_throttle()


# ---------------------------------------------------------------------------
# FastAPI 依赖
# ---------------------------------------------------------------------------
async def auth_required(request: Request) -> str:
    """FastAPI 依赖：校验 Authorization: Bearer <会话token>。返回 token 供审计标识。"""
    auth = request.headers.get("Authorization", "")
    bearer = "Bearer "
    if not auth.startswith(bearer):
        raise HTTPException(status_code=401, detail="未认证：缺少 Authorization 头")
    sid = auth[len(bearer):].strip()
    if not sid or not check_session(sid):
        raise HTTPException(status_code=401, detail="会话无效或已过期")
    return sid
