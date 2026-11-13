from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import Job, Run, ServiceGroup

logger = logging.getLogger("scheduler-center")


def _heartbeat_batch_sec() -> float:
    try:
        return max(1.0, float(os.environ.get("SC_HEARTBEAT_BATCH_SEC", "5")))
    except (TypeError, ValueError):
        return 5.0


class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
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
            cols = [r["name"] for r in c.execute("PRAGMA table_info(jobs)").fetchall()]
            if "created_at" not in cols:
                c.execute("ALTER TABLE jobs ADD COLUMN created_at TEXT")
                c.execute("UPDATE jobs SET created_at=updated_at WHERE created_at IS NULL")
            rcols = [r["name"] for r in c.execute("PRAGMA table_info(runs)").fetchall()]
            if "job_type" not in rcols:
                c.execute("ALTER TABLE runs ADD COLUMN job_type TEXT")
            c.execute("CREATE INDEX IF NOT EXISTS idx_runs_job ON runs(job_name)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status)")
            c.execute("""
                CREATE TABLE IF NOT EXISTS service_groups (
                    name TEXT PRIMARY KEY,
                    group_json TEXT NOT NULL,
                    created_at TEXT
                )
            """)
            self._conn.commit()

    def upsert_jobs(self, jobs: List[Job]):
        with self._lock:
            c = self._conn.cursor()
            for j in jobs:
                now = datetime.now(timezone.utc).isoformat()
                row = c.execute("SELECT created_at FROM jobs WHERE name=?",
                                (j.name,)).fetchone()
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

    def rename_job(self, old_name: str, job: Job):
        """把 old_name 这一行改名为 job.name，并写入新内容。

        name 是主键，直接 upsert 新名会留下旧名那条记录，造成同一条任务出现两份，
        所以改名必须是「先删旧、再写新」且在同一事务里完成。

        同时回填历史执行记录：runs 表里所有 job_name=old_name 的记录，其独立
        job_name 列和 run_json 内嵌的 job_name 都更新为 job.name，保证改名后
        执行历史 / 看板排行按新名归并（产品语义：历史随任务改名）。
        """
        with self._lock:
            c = self._conn.cursor()
            now = datetime.now(timezone.utc).isoformat()
            row = c.execute("SELECT created_at FROM jobs WHERE name=?",
                            (old_name,)).fetchone()
            created = row["created_at"] if row and row["created_at"] else (
                job.created_at or now)
            job.created_at = created
            job.updated_at = now
            try:
                c.execute("DELETE FROM jobs WHERE name=?", (old_name,))
                c.execute(
                    "INSERT INTO jobs (name, job_json, updated_at, created_at) "
                    "VALUES (?,?,?,?) "
                    "ON CONFLICT(name) DO UPDATE SET "
                    "job_json=excluded.job_json, updated_at=excluded.updated_at, "
                    "created_at=excluded.created_at",
                    (job.name, json.dumps(job.to_dict()), now, created),
                )
                # 回填历史执行记录：独立 job_name 列 + run_json 内嵌 job_name
                c.execute("SELECT id, run_json FROM runs WHERE job_name=?", (old_name,))
                moved_rows = c.fetchall()
                for r in moved_rows:
                    try:
                        payload = json.loads(r["run_json"])
                    except (TypeError, ValueError):
                        # run_json 损坏/不是合法 JSON：只迁独立 job_name 列，
                        # 不覆盖原始内容，避免进一步破坏该记录
                        c.execute("UPDATE runs SET job_name=? WHERE id=?",
                                  (job.name, r["id"]))
                        continue
                    if payload.get("job_name") == old_name:
                        payload["job_name"] = job.name
                    c.execute(
                        "UPDATE runs SET job_name=?, run_json=? WHERE id=?",
                        (job.name, json.dumps(payload, ensure_ascii=False), r["id"]),
                    )
                if moved_rows:
                    logger.info("rename_job: 回填 %d 条历史执行记录 %s -> %s",
                                len(moved_rows), old_name, job.name)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def get_job(self, name: str) -> Optional[Job]:
        with self._lock:
            row = self._conn.execute(
                "SELECT job_json FROM jobs WHERE name=?", (name,)
            ).fetchone()
        if row is None:
            return None
        return Job.from_dict(json.loads(row["job_json"]))

    def upsert_service_group(self, group: "ServiceGroup"):
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            if not group.created_at:
                row = self._conn.execute(
                    "SELECT created_at FROM service_groups WHERE name=?",
                    (group.name,),
                ).fetchone()
                group.created_at = (row["created_at"] if row and row["created_at"] else now)
            else:
                group.created_at = group.created_at or now
            self._conn.execute(
                "INSERT INTO service_groups (name, group_json, created_at) "
                "VALUES (?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET "
                "group_json=excluded.group_json, created_at=excluded.created_at",
                (group.name, json.dumps(group.to_dict()), group.created_at),
            )
            self._conn.commit()

    def load_service_groups(self) -> Dict[str, "ServiceGroup"]:
        with self._lock:
            rows = self._conn.execute("SELECT group_json FROM service_groups").fetchall()
        return {g.name: g for g in
                [ServiceGroup.from_dict(json.loads(r["group_json"])) for r in rows]}

    def get_service_group(self, name: str) -> Optional["ServiceGroup"]:
        with self._lock:
            row = self._conn.execute(
                "SELECT group_json FROM service_groups WHERE name=?", (name,)
            ).fetchone()
        if row is None:
            return None
        return ServiceGroup.from_dict(json.loads(row["group_json"]))

    def delete_service_group(self, name: str):
        with self._lock:
            self._conn.execute("DELETE FROM service_groups WHERE name=?", (name,))
            self._conn.commit()


    def _run_from_row(self, row) -> Run:
        """从 runs 表一行构造 Run；run_json 缺失/损坏时用独立列兜底。"""

        def _fallback() -> "Optional[Run]":
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

    def save_run(self, run: Run, heartbeat_only: bool = False):
        now = datetime.now(timezone.utc).isoformat()
        if heartbeat_only:
            old = self.get_run(run.id)
            if old is not None:
                prev = old.last_heartbeat or ""
                try:
                    from datetime import datetime as _dt
                    gap = (datetime.now(timezone.utc) -
                           _dt.fromisoformat(prev)).total_seconds()
                except Exception:
                    gap = 1.0 + _heartbeat_batch_sec()
                if gap < _heartbeat_batch_sec():
                    run.last_heartbeat = now
                    return
            run.last_heartbeat = now
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
        sql, params = self._run_query(status=status, job_name=job_name,
                                      job_type=job_type, name_like=name_like,
                                      sort=sort, order=order,
                                      offset=offset, limit=limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._run_from_row(r) for r in rows]

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
                "SELECT * FROM runs WHERE id=?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return self._run_from_row(row)

    def running_runs(self) -> List[Run]:
        states = ("STARTING", "RUNNING", "ORPHANED", "UNKNOWN")
        if not states:
            return []
        sql = "SELECT * FROM runs WHERE status IN ({})".format(
            ",".join("?" * len(states)))
        with self._lock:
            rows = self._conn.execute(sql, tuple(states)).fetchall()
        return [self._run_from_row(r) for r in rows]

    def running_process_count(self) -> int:
        states = ("STARTING", "RUNNING", "ORPHANED")
        sql = "SELECT COUNT(*) FROM runs WHERE status IN ({})".format(
            ",".join("?" * len(states)))
        with self._lock:
            row = self._conn.execute(sql, tuple(states)).fetchone()
        return row[0] if row else 0

    def delete_runs(self, ids: List[str]) -> int:
        ids = [i for i in ids if i]
        if not ids:
            return 0
        with self._lock:
            c = self._conn.cursor()
            placeholders = ",".join("?" * len(ids))
            c.execute(f"DELETE FROM runs WHERE id IN ({placeholders})", ids)
            self._conn.commit()
            return c.rowcount

    @staticmethod
    def new_run_id() -> str:
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"run_{ts}_{uuid.uuid4().hex[:8]}"

    def close(self):
        with self._lock:
            self._conn.close()