from __future__ import annotations

import hmac
import logging
import os
import secrets
import platform
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from fastapi import HTTPException, Request

logger = logging.getLogger("scheduler-center")

DEFAULT_SESSION_TTL = int(os.environ.get("SC_SESSION_TTL", "43200")) 
DATA_DIR = Path(os.environ.get("SC_DB", str(Path(__file__).resolve().parent.parent / "data" / "scheduler.db"))).parent


class _SessionStore:
    """内存会话表：session_id -> {exp, created}. 单机够用，重启即失效（强制重新登录）."""

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

    def revoke(self, sid: str):
        with self._lock:
            self._sessions.pop(sid, None)

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)


_store = _SessionStore()

_LOGIN_WINDOW = float(os.environ.get("SC_LOGIN_WINDOW", "900"))      # 统计窗口 15min
_LOGIN_MAX_FAIL = int(os.environ.get("SC_LOGIN_MAX_FAIL", "5"))      # 窗口内最多失败次数
_LOGIN_COOLDOWN = float(os.environ.get("SC_LOGIN_COOLDOWN", "300"))  # 触发后冷却 5min


class _LoginThrottle:
    def __init__(self, window: float, max_fail: int, cooldown: float) -> None:
        self._window = window
        self._max_fail = max_fail
        self._cooldown = cooldown
        self._fails: Dict[str, list] = {}     # ip -> [ts,...]
        self._lock = threading.Lock()

    def _prune(self, now: float, ip: str):
        rec = self._fails.get(ip)
        if not rec:
            return
        cutoff = now - self._window
        self._fails[ip] = [t for t in rec if t > cutoff]
        if not self._fails[ip]:
            self._fails.pop(ip, None)

    def locked(self, ip: str, now: float = None) -> float:
        """返回该 IP 是否处于冷却。若是，返回剩余冷却秒数；否则 0."""
        now = now or time.time()
        with self._lock:
            self._prune(now, ip)
            rec = self._fails.get(ip) or []
            if len(rec) >= self._max_fail:
                return max(0.0, self._cooldown - (now - rec[0]))
            return 0.0

    def record_fail(self, ip: str, now: float = None):
        now = now or time.time()
        with self._lock:
            self._prune(now, ip)
            self._fails.setdefault(ip, []).append(now)

    def reset(self, ip: str):
        with self._lock:
            self._fails.pop(ip, None)


_login_throttle = _LoginThrottle(_LOGIN_WINDOW, _LOGIN_MAX_FAIL, _LOGIN_COOLDOWN)


def client_ip(request: Request) -> str:
    """从请求解析客户端 IP（优先 X-Forwarded-For 首个地址，取可靠来源）."""
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
    """登录是否被限流（返回剩余冷却秒数，0 表示未限流）."""
    return _login_throttle.locked(ip)


def record_login_fail(ip: str):
    _login_throttle.record_fail(ip)


def reset_login_fails(ip: str):
    _login_throttle.reset(ip)


def reset_login_throttle():
    with _login_throttle._lock:
        _login_throttle._fails.clear()


def _load_long_term_token() -> str:
    env = os.environ.get("SC_TOKEN")
    if env:
        return env.strip()

    token_file = os.environ.get("SC_TOKEN_FILE")
    if token_file:
        p = Path(token_file)
        if p.is_file():
            val = p.read_text(encoding="utf-8").strip()
            if val:
                return val
        if token_file.lower() == "auto" or os.environ.get("SC_AUTO_GEN_TOKEN") in ("1", "true", "True"):
            return _auto_gen(p)
        return ""

    default_file = DATA_DIR / "sc_token"
    if default_file.is_file():
        val = default_file.read_text(encoding="utf-8").strip()
        if val:
            return val

    if os.environ.get("SC_AUTO_GEN_TOKEN") in ("1", "true", "True"):
        return _auto_gen(default_file)
    return ""


def _lockdown_keyfile(path: Path):

    if not platform.system() == "Windows":
        return
    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r",
             "/grant:r", "*S-1-5-18:(R)",   # SYSTEM
             "/grant:r", "*S-1-5-32-544:(R)",  # Administrators
             "/grant:r", f"{os.environ.get('USERNAME', 'S-1-5-11')}:(R)"],
            check=False, capture_output=True)
    except Exception:  # noqa: BLE001
        logger.warning("回收令牌文件多余权限失败（不影响使用）")


def _auto_gen(path: Path) -> str:
    """生成随机 token 并写入密钥文件（权限收紧）。幂等：已存在则读取返回."""
    if path.is_file():
        val = path.read_text(encoding="utf-8").strip()
        if val:
            return val
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        val = secrets.token_urlsafe(32)
        path.write_text(val, encoding="utf-8")
        _lockdown_keyfile(path)
        logger.warning("已自动生成管理凭证并保存到：%s （请妥善保管，勿提交到仓库）", str(path))
        return val
    except Exception as e:  # noqa: BLE001
        logger.error("自动生成凭证失败：%s", e)
        return ""


# 长期凭证（模块加载时解析一次）
LONG_TERM_TOKEN = _load_long_term_token()


def token_configured() -> bool:
    return bool(LONG_TERM_TOKEN)


class AuthError(Exception):
    pass


def authenticate(long_token: str) -> bool:
    if not LONG_TERM_TOKEN:
        return False
    return hmac.compare_digest(long_token.strip(), LONG_TERM_TOKEN)


def issue_session() -> str:
    return _store.issue(int(DEFAULT_SESSION_TTL))


def revoke_session(sid: str):
    _store.revoke(sid)


def check_session(sid: str) -> bool:
    return _store.valid(sid)


async def auth_required(request: Request) -> str:
    """FastAPI 依赖：校验 Authorization: Bearer <会话token>. 返回 token（供审计标识）."""
    auth = request.headers.get("Authorization", "")
    bearer = "Bearer "
    if not auth.startswith(bearer):
        raise HTTPException(status_code=401, detail="未认证：缺少 Authorization 头")
    sid = auth[len(bearer):].strip()
    if not sid or not check_session(sid):
        raise HTTPException(status_code=401, detail="会话无效或已过期")
    return sid


def session_short_id() -> str:
    return "unknown"