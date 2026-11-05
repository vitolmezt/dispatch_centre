"""持久化存储：使用 SQLite 保存 Job 定义与 Run 历史记录.

- jobs   表：存配置的映射菜单项（以 JSON 文本列存储，便于灵活字段）
- runs   表：存每次执行实例
为避免引入 ORM 依赖，这里用标准库 sqlite3，轻量够用。
"""
from __future__ import annotations

import json
import sqlite3
import uuid
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import Job, Run


class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # sqlite3 连接默认按线程隔离在调用线程，这里统一用一个带锁的连接
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            c = self._conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    name TEXT PRIMARY KEY,
                    job_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    created_at TEXT
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    job_name TEXT NOT NULL,
                    job_type TEXT,
                    run_json TEXT NOT NULL,
                    started_at TEXT,
                    ended_at TEXT,
                    status TEXT
                )
            """)
            # 兼容旧库：jobs 表补 created_at 列，并回填为更新时间的首包时间
            cols = [r["name"] for r in c.execute("PRAGMA table_info(jobs)").fetchall()]
            if "created_at" not in cols:
                c.execute("ALTER TABLE jobs ADD COLUMN created_at TEXT")
                c.execute("UPDATE jobs SET created_at=updated_at WHERE created_at IS NULL")
            # 兼容旧库：runs 表补 job_type 列（若不存在）
            rcols = [r["name"] for r in c.execute("PRAGMA table_info(runs)").fetchall()]
            if "job_type" not in rcols:
                c.execute("ALTER TABLE runs ADD COLUMN job_type TEXT")
            c.execute("CREATE INDEX IF NOT EXISTS idx_runs_job ON runs(job_name)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status)")
            self._conn.commit()

    # ---- jobs ----
    def upsert_jobs(self, jobs: List[Job]):
        """保存任务：更新 updated_at，created_at 首次写入后保持不变（数据库列为权威来源）."""
        with self._lock:
            c = self._conn.cursor()
            for j in jobs:
                now = datetime.now(timezone.utc).isoformat()
                row = c.execute("SELECT created_at FROM jobs WHERE name=?",
                                (j.name,)).fetchone()
                # 已有任务沿用库内 created_at；新任务首次写入时间
                created = (row["created_at"] if row and row["created_at"]
                           else j.created_at or now)
                j.created_at = created
                j.updated_at = now
                c.execute(
                    "INSERT INTO jobs (name, job_json, updated_at, created_at) "
                    "VALUES (?,?,?,?) "
                    "ON CONFLICT(name) DO UPDATE SET "
                    "job_json=excluded.job_json, updated_at=excluded.updated_at, "
                    "created_at=excluded.created_at",
                    (j.name, json.dumps(j.to_dict()), now, created),
                )
            self._conn.commit()

    def load_jobs(self) -> Dict[str, Job]:
        with self._lock:
            c = self._conn.cursor()
            rows = c.execute("SELECT job_json, created_at, updated_at FROM jobs").fetchall()
        result: Dict[str, Job] = {}
        for r in rows:
            job = Job.from_dict(json.loads(r["job_json"]))
            # created_at/updated_at 以数据库列为权威来源（旧库 job_json 无这两个字段）
            if not job.created_at and r["created_at"]:
                job.created_at = r["created_at"]
            if not job.updated_at and r["updated_at"]:
                job.updated_at = r["updated_at"]
            result[job.name] = job
        return result

    def delete_job(self, name: str):
        with self._lock:
            c = self._conn.cursor()
            c.execute("DELETE FROM jobs WHERE name=?", (name,))
            self._conn.commit()

    def get_job(self, name: str) -> Optional[Job]:
        """按名取单条 Job 定义；不存在返回 None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT job_json FROM jobs WHERE name=?", (name,)
            ).fetchone()
        if row is None:
            return None
        return Job.from_dict(json.loads(row["job_json"]))

    # ---- runs ----
    def save_run(self, run: Run):
        row = json.dumps(run.to_dict(include_log=True))
        with self._lock:
            c = self._conn.cursor()
            c.execute(
                "INSERT INTO runs (id, job_name, job_type, run_json, started_at, ended_at, status) "
                "VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET job_name=excluded.job_name, "
                "job_type=excluded.job_type, run_json=excluded.run_json, status=excluded.status, "
                "started_at=excluded.started_at, ended_at=excluded.ended_at",
                (run.id, run.job_name, run.job_type, row, run.start, run.end, run.status),
            )
            self._conn.commit()

    def _run_query(self, /, status=None, job_name=None, job_type=None,
                   name_like=None, sort=None, order="desc",
                   offset=None, limit=None):
        """构造 runs 查询的 where 段与参数."""
        sql = "SELECT run_json FROM runs WHERE 1=1"
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
        # 排序字段白名单，仅允许受支持的列名，避免 SQL 注入
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
        """分页 + 过滤 + 排序查询 runs，返回 [Run, ...]."""
        sql, params = self._run_query(status=status, job_name=job_name,
                                      job_type=job_type, name_like=name_like,
                                      sort=sort, order=order,
                                      offset=offset, limit=limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [Run.from_dict(json.loads(r["run_json"])) for r in rows]

    def count_runs(self, *, status=None, job_name=None, job_type=None, name_like=None) -> int:
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
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return row[0] if row else 0

    def get_runs(self, limit: int = 200, status: Optional[str] = None,
                 job_name: Optional[str] = None) -> List[Run]:
        return self.query_runs(status=status, job_name=job_name, limit=limit)

    def get_run(self, run_id: str) -> Optional[Run]:
        with self._lock:
            row = self._conn.execute(
                "SELECT run_json FROM runs WHERE id=?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return Run.from_dict(json.loads(row["run_json"]))

    def running_runs(self) -> List[Run]:
        return self.get_runs(limit=500, status="RUNNING")

    @staticmethod
    def new_run_id() -> str:
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"run_{ts}_{uuid.uuid4().hex[:8]}"

    def close(self):
        with self._lock:
            self._conn.close()