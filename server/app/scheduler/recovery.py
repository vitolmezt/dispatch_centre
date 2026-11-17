# -*- coding: utf-8 -*-
"""启动恢复、孤儿接管与状态一致性核对。

从 scheduler.py 迁出，逻辑逐行保持一致。

⚠ 这是系统核心能力，行为必须与拆分前完全一致，不得「顺手优化」：

  1. 有无身份凭据（process_create_time）决定能否安全核验
  2. 是否存活一律以 OS 探针（identity.process_is_alive）实测为准
  3. 四问接管：身份 / 监控 / 日志 / 停止能力，全过才重建为 RUNNING
  4. 终态记录与真实进程不一致时予以修正（数据库为终态但进程仍活）
  5. detach 进程退出后收敛为 INTERRUPTED 并归入历史

状态所有权：
  running / store / _lock / _heartbeat_stop 全部由 Scheduler 门面持有。
  本模块不复制任何业务状态，只通过门面读写。
  detach 监控线程通过 self._sched.executor._pump_log 做日志增量读取，
  与拆分前一致。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime

from .. import identity as identity_mod
from ..models import Run

logger = logging.getLogger("scheduler")


class RecoveryManager:
    """启动恢复与孤儿接管。"""

    def __init__(self, sched) -> None:
        self._sched = sched

    # ------------------------------------------------------------------
    # 入口：服务启动时调用
    # ------------------------------------------------------------------
    def recover_on_start(self) -> None:
        s = self._sched
        candidates = s.store.running_runs()
        for run in candidates:
            try:
                self.recover_one(run)
                if run.status == "RUNNING":
                    with s._lock:
                        s.running[run.id] = run
            except Exception as e:  # noqa: BLE001
                logger.error(f"恢复 run[{run.id}] 时异常: {e}")
        try:
            self.reconcile_terminal_runs()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"终态记录一致性核对失败: {e}")

    # ------------------------------------------------------------------
    # 四问接管
    # ------------------------------------------------------------------
    def adopt_orphan(self, run: Run):
        """四问接管校验：身份 / 监控 / 日志 / 停止能力。

        返回 (是否接管成功, 说明)。
        """
        if not identity_mod.verify_identity(run, run.pid):
            return False, "身份核验失败(PID/创建时刻不匹配)，拒绝接管"
        if not run.log_path:
            return False, "缺少日志文件锚点，无法重建监控"
        if not os.path.isfile(run.log_path):
            return False, "日志文件不存在，无法恢复采集"
        try:
            with open(run.log_path, "a", encoding="utf-8", errors="replace"):
                pass
        except OSError as e:
            return False, f"日志文件不可追加，无法恢复采集: {e}"
        if not identity_mod.verify_identity(run, run.pid):
            return False, "停止能力未通过身份核验，拒绝接管"
        return True, "四问全过"

    # ------------------------------------------------------------------
    # 单条记录的恢复收敛
    # ------------------------------------------------------------------
    def recover_one(self, run: Run) -> None:
        s = self._sched

        # --- 无身份凭据：无法安全核验，转待核查 ---
        if not run.process_create_time:
            if run.status == "STARTING":
                run.status = "UNKNOWN"
                run.interrupted_reason = ("残留 STARTING 预登记，无有效身份凭据；"
                                          "启动意图已保存，无法认领具体进程，转待核查")
            elif run.status in ("RUNNING", "ORPHANED", "UNKNOWN"):
                run.status = "UNKNOWN"
                run.interrupted_reason = ("Phase-1 遗留：缺少进程身份凭据(create_time)，"
                                          "无法安全核验，转待核查")
            s.store.save_run(run)
            s._publish_status_change(run)
            return

        # --- 有凭据：以 OS 探针实测为准 ---
        alive = identity_mod.process_is_alive(run, run.pid)
        if alive:
            run.last_heartbeat = datetime.now().astimezone().isoformat(timespec="seconds")
            if run.status in ("STARTING",):
                run.status = "RUNNING"
                run.interrupted_reason = ""
                s.store.save_run(run)
                s._publish_status_change(run)
                logger.info(f"恢复 run[{run.id}] 存活 -> RUNNING")
                return
            if run.status == "UNKNOWN":
                run.status = "ORPHANED"
                run.interrupted_reason = ("重启恢复：进程存活且身份匹配，转孤儿管理，"
                                          "待接管核查")
                s.store.save_run(run)
                s._publish_status_change(run)
                logger.info(f"恢复 run[{run.id}] 存活 & 身份匹配 -> ORPHANED")
                return
            if run.status == "ORPHANED":
                adopted, note = self.adopt_orphan(run)
                run.adopted = adopted
                if adopted:
                    run.status = "RUNNING"
                    run.interrupted_reason = ""
                else:
                    run.interrupted_reason = (run.interrupted_reason or
                                              "四问接管未全过: " + note)
                s.store.save_run(run)
                s._publish_status_change(run)
                logger.info(f"恢复 run[{run.id}] ORPHANED -> "
                            f"{'RUNNING(接管)' if adopted else '保持ORPHANED'} {note}")
                if adopted and getattr(run, "detached", False):
                    self.resume_detached_monitor(run)
                return
            s.store.save_run(run)
            logger.info(f"恢复 run[{run.id}] RUNNING 存活 -> 保持")
            if getattr(run, "detached", False):
                self.resume_detached_monitor(run)
            return

        # --- 探针确认已死 ---
        if run.status == "INTERRUPTED":
            return
        run.status = "INTERRUPTED"
        run.interrupted_reason = run.interrupted_reason or (
            "重启恢复：进程已确认不在运行（OS 探针核验），标记为中断并归入历史")
        run.end = run.end or datetime.now().astimezone().isoformat(timespec="seconds")
        run.last_heartbeat = datetime.now().astimezone().isoformat(timespec="seconds")
        s.store.save_run(run)
        s._publish_status_change(run)
        logger.info(f"恢复 run[{run.id}] 已确认结束 -> INTERRUPTED")

    # ------------------------------------------------------------------
    # 终态记录一致性核对
    # ------------------------------------------------------------------
    def reconcile_terminal_runs(self) -> None:
        """数据库标为终态、但进程实际仍在运行 → 修正为 RUNNING 并重新接管。"""
        s = self._sched
        fixed = 0
        for run in s.store.recent_runs(200):
            if run.status in ("RUNNING", "STARTING", "ORPHANED", "UNKNOWN"):
                continue  # 非终态已由 recover_one 处理
            pid = getattr(run, "pid", None)
            if not pid:
                continue
            if not identity_mod.verify_identity(run, pid):
                continue
            if not identity_mod.process_is_alive(run, pid):
                continue

            prev = run.status
            run.status = "RUNNING"
            run.end = None
            run.exit_code = None
            run.error = ""
            run.interrupted_reason = (
                f"状态不一致已修正：数据库原为 {prev}，"
                f"但进程仍在运行（pid={pid}），已按运行中接管")
            run.last_heartbeat = datetime.now().astimezone().isoformat(timespec="seconds")
            s.store.save_run(run)
            s._publish_status_change(run)
            with s._lock:
                s.running[run.id] = run
            t = threading.Thread(target=self.detached_watch_loop, args=(run,),
                                 name=f"reconcile-watch-{run.id}", daemon=True)
            t.start()
            fixed += 1
            logger.warning(
                f"状态不一致已修正 run[{run.id}]: db={prev} -> RUNNING (pid={pid})")
        if fixed:
            logger.info(f"终态记录核对完成：修正 {fixed} 条不一致记录")

    # ------------------------------------------------------------------
    # detach 进程监控
    # ------------------------------------------------------------------
    def resume_detached_monitor(self, run: Run) -> None:
        t = threading.Thread(target=self.detached_watch_loop, args=(run,),
                             name=f"detach-watch-{run.id}", daemon=True)
        t.start()
        logger.info(f"恢复 run[{run.id}] detach 监控已重建 (pid={run.pid})")

    def detached_watch_loop(self, run: Run) -> None:
        """每秒轮询：增量读日志 + 探针判活；进程消失则收敛为 INTERRUPTED。"""
        s = self._sched
        log_path = run.log_path or ""
        pos = int(getattr(run, "log_offset", 0) or 0)
        try:
            if log_path and os.path.isfile(log_path) and os.path.getsize(log_path) < pos:
                pos = 0  # 文件被截断/轮转
        except OSError:
            pass
        while not s._heartbeat_stop:
            try:
                with s._lock:
                    cur = s.running.get(run.id)
                if cur is None or cur.status not in ("RUNNING", "ORPHANED"):
                    return
                if log_path:
                    pos = s.executor._pump_log(run, log_path, pos)
                if not identity_mod.process_is_alive(run, run.pid):
                    self.mark_detached_gone(run)
                    return
            except Exception as e:  # noqa: BLE001
                logger.warning(f"detach 监控 run[{run.id}] 异常: {e}")
            if s._heartbeat_stop:
                return
            time.sleep(1)

    def mark_detached_gone(self, run: Run) -> None:
        """detach 进程已确认退出：写入结束时间并归入历史。"""
        s = self._sched
        with s._lock:
            cur = s.running.pop(run.id, None) or run
            cur.status = "INTERRUPTED"
            cur.interrupted_reason = (
                cur.interrupted_reason or "独立进程已退出（OS 探针核验），标记为中断并归入历史")
            cur.end = cur.end or datetime.now().astimezone().isoformat(timespec="seconds")
        s.store.save_run(cur)
        s._publish_status_change(cur)
        s.publish_finished(cur)
        s._publish_log_end(cur.id)
        logger.info(f"detach run[{cur.id}] 进程已退出 -> INTERRUPTED")
