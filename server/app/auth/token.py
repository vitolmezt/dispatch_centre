# -*- coding: utf-8 -*-
"""长期管理凭证：加载、自动生成、校验。"""
from __future__ import annotations

import hmac
import logging
import os
import platform
import secrets
import subprocess
from pathlib import Path

logger = logging.getLogger("scheduler-center")

DEFAULT_SESSION_TTL = int(os.environ.get("SC_SESSION_TTL", "43200"))
DATA_DIR = Path(os.environ.get(
    "SC_DB",
    str(Path(__file__).resolve().parent.parent.parent / "data" / "scheduler.db"))).parent


def _lockdown_keyfile(path: Path) -> None:
    if not platform.system() == "Windows":
        return
    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r",
             "/grant:r", "*S-1-5-18:(R)",          # SYSTEM
             "/grant:r", "*S-1-5-32-544:(R)",      # Administrators
             "/grant:r", f"{os.environ.get('USERNAME', 'S-1-5-11')}:(R)"],
            check=False, capture_output=True)
    except Exception:
        logger.warning("回收令牌文件多余权限失败（不影响使用）")


def _auto_gen(path: Path) -> str:
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
    except Exception as e:
        logger.error("自动生成凭证失败：%s", e)
        return ""


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


# 长期凭证（模块加载时解析一次）
LONG_TERM_TOKEN = _load_long_term_token()


def token_configured() -> bool:
    return bool(LONG_TERM_TOKEN)


def authenticate(long_token: str) -> bool:
    if not LONG_TERM_TOKEN:
        return False
    return hmac.compare_digest(long_token.strip(), LONG_TERM_TOKEN)
