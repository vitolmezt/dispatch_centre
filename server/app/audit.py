# -*- coding: utf-8 -*-
"""操作审计（阶段 1 安全加固）.

- 记录操作者、时间、操作对象、操作类型、结果。
- 绝不记录 Token、密码等敏感字段。
- 落盘到 data/audit.log（追加）。单机规模足够；不引第三方依赖。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("scheduler-center")

# 审计日志目录：与 DB 同级
AUDIT_DIR = Path(os.environ.get("SC_DB", str(Path(__file__).resolve().parent.parent / "data" / "scheduler.db"))).parent
AUDIT_FILE = AUDIT_DIR / "audit.log"

_lock = threading.Lock()
_MAX_KEEP = int(os.environ.get("SC_AUDIT_MAX_RECORDS", "2000"))
# 归档保留：保留最近 _KEEP_ARCHIVES 个时间戳归档（更完整的长期保留机制见运维完善阶段）
_KEEP_ARCHIVES = int(os.environ.get("SC_AUDIT_KEEP_ARCHIVES", "5"))
_TRUNCATE_BYTES = 5 * 1024 * 1024  # 触发轮转的阈值

# 本进程已在该文件中写入的“轮转提示”次数，避免每次写都重复提示
_ROTATED_HINT = False


def _trim_archives():
    """只保留最近 N 个时间戳归档文件（*.log.<ts>），避免堆积无上限."""
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
    """审计文件超过阈值时轮转：当前文件按时间戳归档(不覆盖)，新文件保留最近 N 行."""
    global _ROTATED_HINT
    try:
        if not AUDIT_FILE.is_file() or os.path.getsize(AUDIT_FILE) < _TRUNCATE_BYTES:
            return
        with AUDIT_FILE.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        keep = lines[-_MAX_KEEP:] if len(lines) > _MAX_KEEP else lines
        ts = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
        archive = AUDIT_DIR / ("audit.log." + ts)
        # 归档当前文件（完整复制一份旧内容，避免丢失）；再以最近 N 行重建主文件
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
        # 主文件重建后补一条轮转提示（进程级去重，避免每次写都提示）
        if not _ROTATED_HINT:
            _ROTATED_HINT = True
            hint = f"[{datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}] audit.rotate | op=system | obj={archive.name} | result=OK | 审计日志已轮转归档，新日志仅保留最近 {_MAX_KEEP} 行\n"
            with AUDIT_FILE.open("a", encoding="utf-8") as f:
                f.write(hint)
    except Exception as e:  # noqa: BLE001
        logger.warning("审计轮转失败：%s", e)


def audit(action: str, operator: str = "system", obj: str = "",
          detail: str = "", ok: bool = True):
    """写一条审计记录.

    action   操作类型（如 job.create / login / fileop.delete ...）
    operator 操作者标识（会话 token 前6位 或 "system"；绝不存全 token）
    obj      操作对象（如任务名 / run id / 路径）
    detail   补充说明（不含敏感信息）
    ok       是否成功
    """
    try:
        _rotate()
    except Exception:  # noqa: BLE001
        pass
    ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    # detail 中过滤明显的敏感键值，防误记
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
    # 简单规则：带 token/password/secret 字样即打码（保守）
    if any(k in low for k in _SENSITIVE_KEYS):
        return "<redacted>"
    return text


def read_recent(limit: int = 200) -> list:
    """读取最近 N 条审计（供管理界面）."""
    if not AUDIT_FILE.is_file():
        return []
    try:
        with AUDIT_FILE.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return [l.rstrip("\n") for l in lines[-limit:]]
    except Exception:  # noqa: BLE001
        return []