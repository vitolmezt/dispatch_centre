# -*- coding: utf-8 -*-
"""Backward-compatible re-export of the persistence layer.

阶段4 把存储实现迁到 app/persistence/，本模块保留为兼容导入层，
保证 `from app.store import Store` 以及既有的 `sched.store` 调用完全不变。

新代码可直接使用：
    from app.persistence import Store
"""
from __future__ import annotations

from .persistence import Store
from .persistence.database import heartbeat_batch_sec as _heartbeat_batch_sec

__all__ = ["Store"]
