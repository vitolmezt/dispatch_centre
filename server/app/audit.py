# -*- coding: utf-8 -*-

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("scheduler-center")

AUDIT_DIR = Path(os.environ.get("SC_DB", str(Path(__file__).resolve().parent.parent / "data" / "scheduler.db"))).parent
AUDIT_FILE = AUDIT_DIR / "audit.log"

_lock = threading.Lock()
_MAX_KEEP = int(os.environ.get("SC_AUDIT_MAX_RECORDS", "2000"))
_KEEP_ARCHIVES = int(os.environ.get("SC_AUDIT_KEEP_ARCHIVES", "5"))
_TRUNCATE_BYTES = 5 * 1024 * 1024

_ROTATED_HINT = False


def _trim_archives():
    try:
        pat = "audit.log."
        cands = []
        for p in AUDIT_DIR.glob("audit.log.*"):
            cands.append(p)
        cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for p in cands[_KEEP_ARCHIVES:]:
            try:
                p.unlink()
                logger.warning("清理过期审计归档：%s", p.name)
            except OSError:
                pass
    except Exception:  # noqa: BLE001
        pass


def _rotate():
    global _ROTATED_HINT
    try:
        if not AUDIT_FILE.is_file() or os.path.getsize(AUDIT_FILE) < _TRUNCATE_BYTES:
            return
        with AUDIT_FILE.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        keep = lines[-_MAX_KEEP:] if len(lines) > _MAX_KEEP else lines
        ts = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
        archive = AUDIT_DIR / ("audit.log." + ts)
        try:
            AUDIT_DIR.mkdir(parents=True, exist_ok=True)
            with archive.open("w", encoding="utf-8") as f:
                f.writelines(lines)
        except OSError as e:
            logger.warning("审计归档写入失败：%s", e)
            return
        with AUDIT_FILE.open("w", encoding="utf-8") as f:
            f.writelines(keep)
        _trim_archives()
        if not _ROTATED_HINT:
            _ROTATED_HINT = True
            hint = f"[{datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}] audit.rotate | op=system | obj={archive.name} | result=OK | 审计日志已轮转归档，新日志仅保留最近 {_MAX_KEEP} 行\n"
            with AUDIT_FILE.open("a", encoding="utf-8") as f:
                f.write(hint)
    except Exception as e:  # noqa: BLE001
        logger.warning("审计轮转失败：%s", e)


def audit(action: str, operator: str = "system", obj: str = "",
          detail: str = "", ok: bool = True):
    try:
        _rotate()
    except Exception:  # noqa: BLE001
        pass
    ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    safe_detail = _redact(detail)
    line = f"[{ts}] {action} | op={operator} | obj={obj or '-'} | result={'OK' if ok else 'FAIL'} | {safe_detail}\n"
    try:
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        with _lock:
            with AUDIT_FILE.open("a", encoding="utf-8") as f:
                f.write(line)
    except Exception as e:  # noqa: BLE001
        logger.warning("写入审计失败：%s", e)


_SENSITIVE_KEYS = ("token", "authorization", "password", "secret", "bearer")


def _redact(text: str) -> str:
    """把字段/值中疑似敏感的键值替换为 <redacted>."""
    if not text:
        return ""
    low = text.lower()
    if any(k in low for k in _SENSITIVE_KEYS):
        return "<redacted>"
    return text


def read_recent(limit: int = 200) -> list:
    if not AUDIT_FILE.is_file():
        return []
    try:
        with AUDIT_FILE.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return [l.rstrip("\n") for l in lines[-limit:]]
    except Exception:  # noqa: BLE001
        return []