# -*- coding: utf-8 -*-
"""心跳管理与超时检测。

从 scheduler.py 迁出，逻辑逐行保持一致。

核心原则（不可更改）：
  **心跳超时绝不单独判定进程死亡**，必须由 OS 探针（identity.process_is_alive）
  实测确认。心跳仅作活性参考。

状态所有权：
  running / store / heartbeat_interval / _heartbeat_stop 全部由 Scheduler 门面持有。
  本模块不复制任何业务状态，只通过门面读写。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

from .. import identity as identity_mod
from ..models import Run

logger = logging.getLogger("scheduler")


class HeartbeatMonitor:
    """心跳循环、超时检测与状态收敛。"""

    def __init__(self, sched) -> None:
        self._sched = sched

    def _timeout_times(self) -> int:
        """超时倍数：复用 core 的模块级实现，避免重复读取配置。"""
        from .core import _heartbeat_timeout_times
        return _heartbeat_timeout_times()

    # ------------------------------------------------------------------
    # 循环
    # ------------------------------------------------------------------
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
        # 超时倍数复用 core 的模块级函数，避免重复实现配置读取
        timeout_sec = s.heartbeat_interval * self._timeout_times()

        for r in managed:
            r.last_heartbeat = now_s
            s.store.save_run(r, heartbeat_only=True)
            hb = r.last_heartbeat or r.start
            if not self.within_timeout(hb, timeout_sec):
                self.check_timeout(r)

    # ------------------------------------------------------------------
    # 超时判定
    # ------------------------------------------------------------------
    @staticmethod
    def within_timeout(ts, timeout_sec: float) -> bool:
        try:
            from datetime import datetime as _dt
            t = _dt.fromisoformat(ts.replace("Z", "+00:00"))
            return (datetime.now().astimezone() - t).total_seconds() <= timeout_sec
        except Exception:  # noqa: BLE001
            return False

    def check_timeout(self, run: Run) -> None:
        """检查单个 run 是否因心跳超时且确认死亡而需要收敛为 INTERRUPTED。

        关键：探针存活则刷新心跳并直接返回，绝不判死。
        """
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
