# -*- coding: utf-8 -*-
"""Job 数据访问。

从 store.py 迁出，逻辑逐行保持一致。

必须保留的兼容逻辑：
  1. upsert_jobs 保留原 created_at（不因更新而丢失创建时间）
  2. rename_job 同时回填 runs 表的 job_name 列 **和** run_json 内嵌的
     job_name；run_json 损坏时只迁列、不覆盖原始内容，避免进一步破坏记录
  3. 改名全过程在单一事务内，异常回滚

连接与锁由共享的 Database 提供，本模块不自行创建连接。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..models import Job
from .database import Database

logger = logging.getLogger("scheduler-center")


class JobRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def upsert_jobs(self, jobs: List[Job]) -> None:
        db = self._db
        with db.lock:
            c = db.conn.cursor()
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
            db.conn.commit()

    def delete_job(self, name: str) -> None:
        db = self._db
        with db.lock:
            c = db.conn.cursor()
            c.execute("DELETE FROM jobs WHERE name=?", (name,))
            db.conn.commit()

    def rename_job(self, old_name: str, job: Job) -> None:
        """改名：迁移 jobs 行 + 回填 runs 的历史 job_name（单事务）。"""
        db = self._db
        with db.lock:
            c = db.conn.cursor()
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
                db.conn.commit()
            except Exception:
                db.conn.rollback()
                raise

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    def load_jobs(self) -> Dict[str, Job]:
        db = self._db
        with db.lock:
            c = db.conn.cursor()
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

    def get_job(self, name: str) -> Optional[Job]:
        db = self._db
        with db.lock:
            row = db.conn.execute(
                "SELECT job_json FROM jobs WHERE name=?", (name,)
            ).fetchone()
        if row is None:
            return None
        return Job.from_dict(json.loads(row["job_json"]))
