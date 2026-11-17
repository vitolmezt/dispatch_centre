# -*- coding: utf-8 -*-
"""Run 数据访问。

从 store.py 迁出，逻辑逐行保持一致。

必须保留的兼容逻辑：
  1. _run_from_row 的兜底：run_json 缺失/损坏时用独立列
     (id/job_name/job_type/status/started_at/ended_at) 重构 Run
  2. save_run(heartbeat_only=True) 的心跳写库节流：
     距上次心跳不足 heartbeat_batch_sec 时只更新内存字段不落库
  3. running_runs 返回 4 个状态（含 UNKNOWN）
  4. running_process_count 只算 3 个状态（不含 UNKNOWN，
     因 UNKNOWN 无法确认进程是否真实存在）

连接与锁由共享的 Database 提供。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional

from ..models import Run
from .database import Database, heartbeat_batch_sec


class RunRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # 行 → Run（含损坏兜底）
    # ------------------------------------------------------------------
    def run_from_row(self, row) -> Optional[Run]:
        """从 runs 表一行构造 Run；run_json 缺失/损坏时用独立列兜底。"""
        def _fallback() -> Optional[Run]:
            kwargs = {
                "id": row["id"],
                "job_name": row["job_name"] or "unknown",
                "job_type": row["job_type"] or "COMMAND",
                "status": row["status"] or "UNKNOWN",
                "start": row["started_at"],
                "end": row["ended_at"],
            }
            try:
                return Run(**kwargs)
            except Exception:  # noqa: BLE001
                return None

        raw = row["run_json"]
        if isinstance(raw, str):
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                payload = None
            if isinstance(payload, dict):
                try:
                    return Run.from_dict(payload)
                except (TypeError, ValueError):
                    pass
        return _fallback()

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def save_run(self, run: Run, heartbeat_only: bool = False) -> None:
        db = self._db
        now = datetime.now(timezone.utc).isoformat()
        if heartbeat_only:
            old = self.get_run(run.id)
            if old is not None:
                prev = old.last_heartbeat or ""
                try:
                    from datetime import datetime as _dt
                    gap = (datetime.now(timezone.utc) -
                           _dt.fromisoformat(prev)).total_seconds()
                except Exception:  # noqa: BLE001
                    gap = 1.0 + heartbeat_batch_sec()
                if gap < heartbeat_batch_sec():
                    run.last_heartbeat = now
                    return
            run.last_heartbeat = now
        row = json.dumps(run.to_dict(include_log=True))
        with db.lock:
            c = db.conn.cursor()
            c.execute(
                "INSERT INTO runs (id, job_name, job_type, run_json, started_at, ended_at, status) "
                "VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET job_name=excluded.job_name, "
                "job_type=excluded.job_type, run_json=excluded.run_json, status=excluded.status, "
                "started_at=excluded.started_at, ended_at=excluded.ended_at",
                (run.id, run.job_name, run.job_type, row, run.start, run.end, run.status),
            )
            db.conn.commit()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def build_query(self, /, status=None, job_name=None, job_type=None,
                    name_like=None, sort=None, order="desc",
                    offset=None, limit=None):
        sql = "SELECT * FROM runs WHERE 1=1"
        params: List[Any] = []
        if status:
            sql += " AND status=?"
            params.append(status)
        if job_name:
            sql += " AND job_name=?"
            params.append(job_name)
        if job_type:
            sql += " AND job_type=?"
            params.append(job_type)
        if name_like:
            sql += " AND job_name LIKE ?"
            params.append(f"%{name_like}%")
        col = {"start": "started_at", "name": "job_name", "end": "ended_at"}.get(
            sort if sort in ("start", "name", "end") else "start", "started_at")
        direction = "ASC" if str(order).lower() == "asc" else "DESC"
        order_sql = f" ORDER BY {col} {direction}"
        if limit is not None and offset is not None:
            sql = sql + order_sql + " LIMIT ? OFFSET ?"
            params += [limit, offset]
        elif limit is not None:
            sql = sql + order_sql + " LIMIT ?"
            params.append(limit)
        else:
            sql += order_sql
        return sql, params

    def query_runs(self, *, status=None, job_name=None, job_type=None, name_like=None,
                   sort=None, order="desc", offset=0, limit=200) -> List[Run]:
        db = self._db
        sql, params = self.build_query(status=status, job_name=job_name,
                                       job_type=job_type, name_like=name_like,
                                       sort=sort, order=order,
                                       offset=offset, limit=limit)
        with db.lock:
            rows = db.conn.execute(sql, params).fetchall()
        return [self.run_from_row(r) for r in rows]

    def count_runs(self, *, status=None, job_name=None, job_type=None,
                   name_like=None) -> int:
        db = self._db
        sql = "SELECT COUNT(*) FROM runs WHERE 1=1"
        params: List[Any] = []
        if status:
            sql += " AND status=?"
            params.append(status)
        if job_name:
            sql += " AND job_name=?"
            params.append(job_name)
        if job_type:
            sql += " AND job_type=?"
            params.append(job_type)
        if name_like:
            sql += " AND job_name LIKE ?"
            params.append(f"%{name_like}%")
        with db.lock:
            row = db.conn.execute(sql, params).fetchone()
        return row[0] if row else 0

    def get_runs(self, limit: int = 200, status: Optional[str] = None,
                 job_name: Optional[str] = None) -> List[Run]:
        return self.query_runs(status=status, job_name=job_name, limit=limit)

    def get_run(self, run_id: str) -> Optional[Run]:
        db = self._db
        with db.lock:
            row = db.conn.execute(
                "SELECT * FROM runs WHERE id=?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return self.run_from_row(row)

    def recent_runs(self, limit: int = 200) -> List[Run]:
        """最近若干条 run（含终态），用于状态一致性核对。

        场景：数据库某条记录已被标为终态（如 KILLED），但进程其实还在运行。
        只查非终态会把这类不一致永久漏掉，所以要连终态一起核对。
        """
        db = self._db
        sql = "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?"
        with db.lock:
            rows = db.conn.execute(sql, (int(limit),)).fetchall()
        return [self.run_from_row(r) for r in rows]

    def running_runs(self) -> List[Run]:
        db = self._db
        states = ("STARTING", "RUNNING", "ORPHANED", "UNKNOWN")
        sql = "SELECT * FROM runs WHERE status IN ({})".format(
            ",".join("?" * len(states)))
        with db.lock:
            rows = db.conn.execute(sql, tuple(states)).fetchall()
        return [self.run_from_row(r) for r in rows]

    def running_process_count(self) -> int:
        """实际运行进程数：**不含 UNKNOWN**（无法确认是否真实存在）。"""
        db = self._db
        states = ("STARTING", "RUNNING", "ORPHANED")
        sql = "SELECT COUNT(*) FROM runs WHERE status IN ({})".format(
            ",".join("?" * len(states)))
        with db.lock:
            row = db.conn.execute(sql, tuple(states)).fetchone()
        return row[0] if row else 0

    # ------------------------------------------------------------------
    # 删除 / 工具
    # ------------------------------------------------------------------
    def delete_runs(self, ids: List[str]) -> int:
        db = self._db
        ids = [i for i in ids if i]
        if not ids:
            return 0
        with db.lock:
            c = db.conn.cursor()
            placeholders = ",".join("?" * len(ids))
            c.execute(f"DELETE FROM runs WHERE id IN ({placeholders})", ids)
            db.conn.commit()
            return c.rowcount

    @staticmethod
    def new_run_id() -> str:
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"run_{ts}_{uuid.uuid4().hex[:8]}"
