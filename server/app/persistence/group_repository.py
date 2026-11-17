# -*- coding: utf-8 -*-
"""ServiceGroup 数据访问。

从 store.py 迁出，逻辑逐行保持一致。
连接与锁由共享的 Database 提供。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict, Optional

from ..models import ServiceGroup
from .database import Database


class GroupRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert_service_group(self, group: ServiceGroup) -> None:
        db = self._db
        with db.lock:
            now = datetime.now(timezone.utc).isoformat()
            if not group.created_at:
                row = db.conn.execute(
                    "SELECT created_at FROM service_groups WHERE name=?",
                    (group.name,),
                ).fetchone()
                group.created_at = (row["created_at"] if row and row["created_at"] else now)
            else:
                group.created_at = group.created_at or now
            db.conn.execute(
                "INSERT INTO service_groups (name, group_json, created_at) "
                "VALUES (?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET "
                "group_json=excluded.group_json, created_at=excluded.created_at",
                (group.name, json.dumps(group.to_dict()), group.created_at),
            )
            db.conn.commit()

    def load_service_groups(self) -> Dict[str, ServiceGroup]:
        db = self._db
        with db.lock:
            rows = db.conn.execute("SELECT group_json FROM service_groups").fetchall()
        return {g.name: g for g in
                [ServiceGroup.from_dict(json.loads(r["group_json"])) for r in rows]}

    def get_service_group(self, name: str) -> Optional[ServiceGroup]:
        db = self._db
        with db.lock:
            row = db.conn.execute(
                "SELECT group_json FROM service_groups WHERE name=?", (name,)
            ).fetchone()
        if row is None:
            return None
        return ServiceGroup.from_dict(json.loads(row["group_json"]))

    def delete_service_group(self, name: str) -> None:
        db = self._db
        with db.lock:
            db.conn.execute("DELETE FROM service_groups WHERE name=?", (name,))
            db.conn.commit()
