# -*- coding: utf-8 -*-
"""心跳管理与超时检测。"""
from __future__ import annotations

import logging
import time
from datetime import datetime

from .. import identity as identity_mod
from ..models import Run

logger = logging.getLogger("scheduler")


class HeartbeatMonitor:
    def __init__(self, sched) -> None:
        self._sched = sched

    def _timeout_times(self) -> int:
        from .core import _heartbeat_timeout_times
        return _heartbeat_timeout_times()

    def loop(self) -> None:
        s = self._sched
        while not s._heartbeat_stop:
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"心跳 tick 异常: {e}")
            for _ in range(s.heartbeat_interval):
                if s._heartbeat_stop:
                    return
                time.sleep(1)

    def tick(self) -> None:
        s = self._sched
        runs = s.store.running_runs()
        managed = [r for r in runs
                   if r.status in ("RUNNING", "ORPHANED", "STARTING")]
        now_s = datetime.now().astimezone().isoformat(timespec="seconds")
        timeout_sec = s.heartbeat_interval * self._timeout_times()

        for r in managed:
            r.last_heartbeat = now_s
            s.store.save_run(r, heartbeat_only=True)
            hb = r.last_heartbeat or r.start
            if not self.within_timeout(hb, timeout_sec):
                self.check_timeout(r)
    @staticmethod
    def within_timeout(ts, timeout_sec: float) -> bool:
        try:
            from datetime import datetime as _dt
            t = _dt.fromisoformat(ts.replace("Z", "+00:00"))
            return (datetime.now().astimezone() - t).total_seconds() <= timeout_sec
        except Exception:  # noqa: BLE001
            return False

    def check_timeout(self, run: Run) -> None:
        s = self._sched
        if identity_mod.process_is_alive(run, run.pid):
            run.last_heartbeat = datetime.now().astimezone().isoformat(timespec="seconds")
            s.store.save_run(run, heartbeat_only=True)
            return
        if run.status == "INTERRUPTED":
            return
        run.status = "INTERRUPTED"
        run.interrupted_reason = (run.interrupted_reason or
                                  "心跳连续超时且 OS 探针确认进程已不在运行，判定中断")
        run.end = run.end or datetime.now().astimezone().isoformat(timespec="seconds")
        s.store.save_run(run)
        s._publish_status_change(run)
        logger.warning(f"心跳超时 + OS 探针确认死亡: run[{run.id}] -> INTERRUPTED")
