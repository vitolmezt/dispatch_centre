# -*- coding: utf-8 -*-
"""全局事件订阅与广播。

从 scheduler.py 迁出，逻辑逐行保持一致。

管理边界（重要）：
  本模块只持有 **订阅者队列** `_subscribers` 与同步锁 `_sub_lock`。
  这是**连接级资源**，不是业务运行状态，因此随模块迁移由其自行持有，
  不构成状态分裂。

  运行状态（jobs / running / service_groups / aps / store 等）
  仍由 Scheduler 门面唯一持有，本模块**不持有任何副本**。

  订阅队列操作使用本模块自己的 `_sub_lock`（与 Scheduler._lock 分离），
  避免广播时长时间占用业务锁；这与拆分前的行为一致（原先即用 _sub_lock）。
"""
from __future__ import annotations

import json
import queue
import threading
from typing import List


class EventBus:
    """全局 SSE 事件总线。

    与 Scheduler 的关系：持有门面回指 `_sched`，用于访问
    running / service_groups / _finished 等业务状态，自身不复制。
    """

    def __init__(self, sched) -> None:
        self._sched = sched
        self._subscribers: List[queue.Queue] = []
        self._sub_lock = threading.Lock()

    # ------------------------------------------------------------------
    # 订阅管理
    # ------------------------------------------------------------------
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=100)
        with self._sub_lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._sub_lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # 广播
    # ------------------------------------------------------------------
    def broadcast(self, data: dict) -> None:
        try:
            payload = f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
        except Exception:  # noqa: BLE001
            return
        with self._sub_lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                if q.full():
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        pass
                q.put_nowait(payload)
            except queue.Full:
                pass

    # ------------------------------------------------------------------
    # 事件便捷方法
    # ------------------------------------------------------------------
    def publish_finished(self, run) -> None:
        """广播执行完成；借助门面的 _finished 做去重（状态由门面持有）。"""
        sched = self._sched
        with sched._lock:
            if run.id in sched._finished:
                return
            sched._finished.add(run.id)
        self.broadcast({"type": "run", "event": "finished", "run_id": run.id,
                        "job_name": run.job_name, "status": run.status})

    def publish_status_change(self, run) -> None:
        """广播状态迁移（心跳/恢复收敛时使用）。"""
        try:
            self.broadcast({"type": "run", "event": "status_changed", "run_id": run.id,
                            "job_name": run.job_name, "status": run.status})
        except Exception:  # noqa: BLE001
            pass

    def publish_started(self, run) -> None:
        self.broadcast({"type": "run", "event": "started", "run_id": run.id,
                        "job_name": run.job_name, "status": run.status})
