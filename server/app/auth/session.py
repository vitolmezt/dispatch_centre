# -*- coding: utf-8 -*-
"""短期会话 token：签发、校验、注销。

从 auth.py 迁出，逻辑逐行保持一致。

设计：内存会话表，单机够用；服务重启即清空（强制重新登录）。
TTL 取 DEFAULT_SESSION_TTL（默认 12h）。
"""
from __future__ import annotations

import secrets
import threading
import time
from typing import Dict

from .token import DEFAULT_SESSION_TTL


class _SessionStore:
    """内存会话表：session_id -> {exp, created}。"""

    def __init__(self) -> None:
        self._sessions: Dict[str, dict] = {}
        self._lock = threading.Lock()

    def issue(self, ttl: int) -> str:
        sid = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[sid] = {"exp": time.time() + ttl, "created": time.time()}
        return sid

    def valid(self, sid: str) -> bool:
        if not sid:
            return False
        with self._lock:
            rec = self._sessions.get(sid)
            if rec is None:
                return False
            if time.time() > rec["exp"]:
                self._sessions.pop(sid, None)
                return False
            return True

    def revoke(self, sid: str) -> None:
        with self._lock:
            self._sessions.pop(sid, None)

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)


_store = _SessionStore()


def issue_session() -> str:
    return _store.issue(int(DEFAULT_SESSION_TTL))


def revoke_session(sid: str) -> None:
    _store.revoke(sid)


def check_session(sid: str) -> bool:
    return _store.valid(sid)


def session_count() -> int:
    return _store.count()
