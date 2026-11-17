# -*- coding: utf-8 -*-
"""调度核心模块。

保留 Scheduler 作为统一门面，内部按职责拆分：

  core.py            生命周期、状态持有、门面委托、触发与执行回调
  registration.py    APScheduler 任务注册与注销
  recovery.py        启动恢复、四问接管、状态核对
  heartbeat.py       心跳管理与超时检测
  process_control.py 运行任务的停止与中止
  events.py          全局事件订阅与广播
  log_manager.py     日志文件管理与日志推送
  views.py           任务/运行/历史/服务组视图
  service_groups.py  服务组执行协调

状态所有权（阶段3 硬约束）：
  jobs / groups / service_groups / running / aps / store / _lock / config_path
  全部由 Scheduler 门面**唯一持有**。子模块只持门面回指 `self._sched`，
  不复制任何业务状态。

  唯一例外：EventBus 的 `_subscribers` 与 LogManager 的 `_log_subscribers`
  是连接级订阅队列，随模块迁移由各自持有，不属于业务状态。

兼容性：`from app.scheduler import Scheduler` 必须继续可用，
由本 __init__.py 统一导出。
"""
from .core import Scheduler

__all__ = ["Scheduler"]
