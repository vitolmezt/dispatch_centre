# -*- coding: utf-8 -*-
"""数据持久化层。

保留 Store 作为统一门面（兼容 sched.store 的既有调用），内部按实体拆分：

  database.py          SQLite 连接、锁、事务、schema 初始化、在线迁移
  job_repository.py    Job 数据访问
  run_repository.py    Run 数据访问
  group_repository.py  ServiceGroup 数据访问

关键约束：
  1. 三个 Repository **共享同一个 Database 实例**，不各自创建连接
  2. 数据库结构、JSON 序列化格式、在线迁移、历史数据兼容均保持不变
  3. Store 门面透传全部原方法名（测试与业务代码大量使用 sched.store.xxx）

兼容性：`from app.store import Store` 必须继续可用。
"""
from .store_facade import Store

__all__ = ["Store"]
