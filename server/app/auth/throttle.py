# -*- coding: utf-8 -*-
"""登录失败限流与客户端 IP 解析。"""
from __future__ import annotations

import os
import threading
import time
from typing import Dict

from fastapi import Request

_LOGIN_WINDOW = float(os.environ.get("SC_LOGIN_WINDOW", "900"))      # 统计窗口 15min
_LOGIN_MAX_FAIL = int(os.environ.get("SC_LOGIN_MAX_FAIL", "5"))      # 窗口内最多失败次数
_LOGIN_COOLDOWN = float(os.environ.get("SC_LOGIN_COOLDOWN", "300"))  # 触发后冷却 5min


class _LoginThrottle:
    def __init__(self, window: float, max_fail: int, cooldown: float) -> None:
        self._window = window
        self._max_fail = max_fail
        self._cooldown = cooldown
        self._fails: Dict[str, list] = {}     # ip -> [ts, ...]
        self._lock = threading.Lock()

    def _prune(self, now: float, ip: str) -> None:
        rec = self._fails.get(ip)
        if not rec:
            return
        cutoff = now - self._window
        self._fails[ip] = [t for t in rec if t > cutoff]
        if not self._fails[ip]:
            self._fails.pop(ip, None)

    def locked(self, ip: str, now: float = None) -> float:
        now = now or time.time()
        with self._lock:
            self._prune(now, ip)
            rec = self._fails.get(ip) or []
            if len(rec) >= self._max_fail:
                return max(0.0, self._cooldown - (now - rec[0]))
            return 0.0

    def record_fail(self, ip: str, now: float = None) -> None:
        now = now or time.time()
        with self._lock:
            self._prune(now, ip)
            self._fails.setdefault(ip, []).append(now)

    def reset(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)


_login_throttle = _LoginThrottle(_LOGIN_WINDOW, _LOGIN_MAX_FAIL, _LOGIN_COOLDOWN)


def client_ip(request: Request) -> str:
    try:
        xff = request.headers.get("X-Forwarded-For")
        if xff:
            return xff.split(",")[0].strip()
        if request.client is not None and request.client.host:
            return request.client.host
    except Exception:  # noqa: BLE001
        pass
    return "unknown"


def login_locked(ip: str) -> float:
    return _login_throttle.locked(ip)


def record_login_fail(ip: str) -> None:
    _login_throttle.record_fail(ip)


def reset_login_fails(ip: str) -> None:
    _login_throttle.reset(ip)


def reset_login_throttle() -> None:
    with _login_throttle._lock:
        _login_throttle._fails.clear()
