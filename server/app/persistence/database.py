# -*- coding: utf-8 -*-
"""SQLite 连接、锁、事务与 schema 管理。

从 store.py 迁出，逻辑逐行保持一致。

**最关键的部分是 _init_schema 的在线迁移**：
  老库可能缺少 created_at / job_type 列，必须在启动时用 PRAGMA 检查后
  ALTER TABLE 补齐。这保证已有 scheduler.db 可直接升级，不丢数据。
  此逻辑不得改动。

连接与锁由 Database 唯一持有，三个 Repository 共享同一实例。
"""
from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path


def heartbeat_batch_sec() -> float:
    """心跳写库节流秒数（默认 5s）。"""
    try:
        return max(1.0, float(os.environ.get("SC_HEARTBEAT_BATCH_SEC", "5")))
    except (TypeError, ValueError):
        return 5.0


class Database:
    """SQLite 连接管理器：持有连接与锁，供各 Repository 共享。"""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.init_schema()

    # ------------------------------------------------------------------
    # schema 初始化 + 在线迁移
    # ------------------------------------------------------------------
    def init_schema(self) -> None:
        with self.lock:
            c = self.conn.cursor()
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
            # 在线迁移：老库补列
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
            self.conn.commit()

    def close(self) -> None:
        with self.lock:
            self.conn.close()
