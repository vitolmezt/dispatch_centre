# -*- coding: utf-8 -*-
"""全局事件订阅与广播。"""
from __future__ import annotations

import json
import queue
import threading
from typing import List


class EventBus:

    def __init__(self, sched) -> None:
        self._sched = sched
        self._subscribers: List[queue.Queue] = []
        self._sub_lock = threading.Lock()

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

    def publish_finished(self, run) -> None:
        sched = self._sched
        with sched._lock:
            if run.id in sched._finished:
                return
            sched._finished.add(run.id)
        self.broadcast({"type": "run", "event": "finished", "run_id": run.id,
                        "job_name": run.job_name, "status": run.status})

    def publish_status_change(self, run) -> None:
        try:
            self.broadcast({"type": "run", "event": "status_changed", "run_id": run.id,
                            "job_name": run.job_name, "status": run.status})
        except Exception:  # noqa: BLE001
            pass

    def publish_started(self, run) -> None:
        self.broadcast({"type": "run", "event": "started", "run_id": run.id,
                        "job_name": run.job_name, "status": run.status})
