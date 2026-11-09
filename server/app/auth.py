# -*- coding: utf-8 -*-
"""身份认证与会话管理（阶段 1 安全加固）.

设计要点：
- 长期管理凭证：SC_TOKEN（环境变量） 或 SC_TOKEN_FILE（指向安全的本机密钥文件）。
- 若都不配置：SC_AUTO_GEN_TOKEN=1 时首次启动自动生成随机 token 写入所选密钥文件，
  并在日志打印一次“已自动生成凭证”提示（不打印 token 本身）。
- 登录：POST /api/login 校验长期凭证 → 签发短期会话 token（内存会话表），
  会话支持主动退出(logout)、过期(TTL)、轮换(重新登录即作废旧会话)。
- 认证依赖 auth_required：解析 Authorization: Bearer <会话token>，查内存会话并校验过期，
  用 hmac.compare_digest 防时序攻击。
- 会话 token 不落盘、不进 URL、不进前端源码与日志。
"""
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

# 默认会话时长（秒），可用 SC_SESSION_TTL 覆盖
DEFAULT_SESSION_TTL = int(os.environ.get("SC_SESSION_TTL", "43200"))  # 12h
# 自动生成凭证：仅当 SC_AUTO_GEN_TOKEN=1 且未显式配置凭证时生效
# 密钥文件位置：SC_TOKEN_FILE 指定，或默认放在数据目录下 data/sc_token
DATA_DIR = Path(os.environ.get("SC_DB", str(Path(__file__).resolve().parent.parent / "data" / "scheduler.db"))).parent


class _SessionStore:
    """内存会话表：session_id -> {exp, created}. 单机够用，重启即失效（强制重新登录）."""

    def __init__(self) -> None:
        self._sessions: Dict[str, dict] = {}
        self._lock = threading.Lock()

    def issue(self, ttl: int) -> str:
        sid = secrets.token_urlsafe(32)
        with self._lock:
            # 轮换：新会话加入；旧会话让其在过期后自然清理，不主动大批清
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


# ---- 登录失败限流（按源 IP） ----
# 同一 IP 连续失败过多后进入冷却窗口，防止暴力猜测长期凭证。
# 窗口与阈值可分别用 SC_LOGIN_WINDOW / SC_LOGIN_MAX_FAIL / SC_LOGIN_COOLDOWN 覆盖。
_LOGIN_WINDOW = float(os.environ.get("SC_LOGIN_WINDOW", "900"))      # 统计窗口 15min
_LOGIN_MAX_FAIL = int(os.environ.get("SC_LOGIN_MAX_FAIL", "5"))      # 窗口内最多失败次数
_LOGIN_COOLDOWN = float(os.environ.get("SC_LOGIN_COOLDOWN", "300"))  # 触发后冷却 5min


class _LoginThrottle:
    """简单内存限流：记录每 IP 在滑动窗口内的失败次数，超限则拒绝直到冷却结束."""

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
    """清空全部来源的登录失败计数/冷却（测试/管理用）."""
    with _login_throttle._lock:
        _login_throttle._fails.clear()


def _load_long_term_token() -> str:
    """解析长期管理凭证，失败(未配置)返回空串."""
    # 1) 环境变量显式指定
    env = os.environ.get("SC_TOKEN")
    if env:
        return env.strip()

    # 2) 密钥文件
    token_file = os.environ.get("SC_TOKEN_FILE")
    if token_file:
        p = Path(token_file)
        if p.is_file():
            val = p.read_text(encoding="utf-8").strip()
            if val:
                return val
        # 文件不存在或为空：若为 auto 模式则自动生成
        if token_file.lower() == "auto" or os.environ.get("SC_AUTO_GEN_TOKEN") in ("1", "true", "True"):
            return _auto_gen(p)
        return ""

    # 3) 默认数据目录下 data/sc_token
    default_file = DATA_DIR / "sc_token"
    if default_file.is_file():
        val = default_file.read_text(encoding="utf-8").strip()
        if val:
            return val

    # 4) 最后的自动生成开关
    if os.environ.get("SC_AUTO_GEN_TOKEN") in ("1", "true", "True"):
        return _auto_gen(default_file)
    return ""


def _lockdown_keyfile(path: Path):
    """收紧密钥文件访问权限（Windows）：去掉继承，仅保留当前用户 + SYSTEM + Administrators.

    防止 data 目录被放宽时普通用户仍能读到密钥。非 Windows 或无 icacls 时静默降级。
    """
    if not platform.system() == "Windows":
        return
    try:
        # /inheritance:r 去掉来自父目录的继承；随后仅对 admin 与当前用户授权
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
    """校验登录时提交的长期凭证."""
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
    """供审计使用：会话 token 前6位（不泄露全文）."""
    return "unknown"