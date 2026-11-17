from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from ..executor import Executor
from .. import identity as identity_mod
from ..models import Job, Run, job_to_api, ServiceGroup
from ..store import Store
from .events import EventBus
from .log_manager import LogManager
from .views import Views
from .heartbeat import HeartbeatMonitor
from .registration import JobRegistration
from .recovery import RecoveryManager
from .process_control import ProcessController
from .service_groups import ServiceGroupCoordinator

logger = logging.getLogger("scheduler")


def _as_type(v) -> str:
    return str(v or "").strip().upper()


def _heartbeat_interval_sec() -> int:
    try:
        return max(1, int(os.environ.get("SC_HEARTBEAT_INTERVAL_SEC", "10")))
    except (TypeError, ValueError):
        return 10


def _heartbeat_timeout_times() -> int:
    try:
        return max(1, int(os.environ.get("SC_HEARTBEAT_TIMEOUT_TIMES", "3")))
    except (TypeError, ValueError):
        return 3


def _collect_job(job: Job, jobs: List[Job], errors: List[str]):
    errs = job.validate()
    if errs:
        errors.append(f"[{job.name}] " + "; ".join(errs))
    elif any(j.name == job.name for j in jobs):
        errors.append(f"重复的任务名: {job.name}")
    else:
        jobs.append(job)


class Scheduler:
    def __init__(self, config_path: str, db_path: str):
        self.config_path = config_path
        self.store = Store(db_path)
        self.executor = Executor(self)
        self.jobs: Dict[str, Job] = {}
        self.groups: Dict[str, dict] = {}
        self.service_groups: Dict[str, ServiceGroup] = {}
        self.running: Dict[str, Run] = {}
        self._lock = threading.RLock()
        _env_node = os.environ.get("SC_NODE_ID")
        if _env_node:
            self.node_id = _env_node
        elif hasattr(os, "uname"):
            self.node_id = os.uname().nodename or "local"
        else:
            self.node_id = os.environ.get("COMPUTERNAME", "local") or "local"
        self.heartbeat_interval = _heartbeat_interval_sec()
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._heartbeat_stop = False
        self._heartbeat_runs: Dict[str, "Run"] = {}
        self._finished: set = set()
        self._logs_dir = Path(db_path).parent / "logs"
        try:
            self._logs_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            self._logs_dir = None

        # ---- 子模块（无业务状态，仅持门面回指）----
        # 订阅队列随子模块迁移：EventBus._subscribers / LogManager._log_subscribers
        self._events = EventBus(self)
        self._logs = LogManager(self)
        self._views = Views(self)
        self._heartbeat = HeartbeatMonitor(self)
        self._registration = JobRegistration(self)
        self._recovery = RecoveryManager(self)
        self._control = ProcessController(self)
        self._group_exec = ServiceGroupCoordinator(self)

        self.aps = BackgroundScheduler()
        self.aps.add_listener(self._job_listener)

    def on_started(self, run: Run):
        if self._logs_dir and not run.log_path:
            run.log_path = str(self._logs_dir / f"{run.id}.log")
        if run.status == "STARTING":
            run.status = "RUNNING"
            run.node_id = self.node_id
        run.last_heartbeat = datetime.now().astimezone().isoformat(timespec="seconds")
        with self._lock:
            self.running[run.id] = run
        self.store.save_run(run)
        self.broadcast({"type": "run", "event": "started", "run_id": run.id,
                        "job_name": run.job_name, "status": run.status})

    # ---- 日志（实现在 log_manager）----
    def prepare_log_path(self, run: Run) -> str:
        return self._logs.prepare_log_path(run)

    def on_log(self, run: Run, line: str, write_file: bool = True):
        return self._logs.on_log(run, line, write_file)

    def subscribe_log(self, run_id: str) -> queue.Queue:
        return self._logs.subscribe_log(run_id)

    def unsubscribe_log(self, run_id: str, q: queue.Queue):
        return self._logs.unsubscribe_log(run_id, q)

    def publish_log(self, run_id: str, line: str):
        return self._logs.publish_log(run_id, line)

    def _append_log_file(self, run: Run, line: str):
        return self._logs.append_log_file(run, line)

    def _publish_log_end(self, run_id: str):
        return self._logs.publish_log_end(run_id)

    def read_log_file(self, run_id: str):
        return self._logs.read_log_file(run_id)

    # ---- 执行完成回调（状态由门面持有）----
    def on_finished(self, run: Run):
        with self._lock:
            self.running.pop(run.id, None)
        self.store.save_run(run)
        logger.info(f"Job[{run.job_name}] run[{run.id}] -> {run.status}")
        self.publish_finished(run)
        self._publish_log_end(run.id)

    def on_user_stopped(self, run: Run):
        self._publish_log_end(run.id)

    def publish_finished(self, run: "Run"):
        return self._events.publish_finished(run)


    # ---- 事件（实现在 events）----
    def _publish_status_change(self, run):
        return self._events.publish_status_change(run)

    def subscribe(self) -> queue.Queue:
        return self._events.subscribe()

    def unsubscribe(self, q: queue.Queue):
        return self._events.unsubscribe(q)

    def broadcast(self, data: dict):
        return self._events.broadcast(data)

    def _job_listener(self, event):
        if event.code == 4096:
            pass

    # ---- 配置加载与 cron 注册（实现在 registration）----
    def load_config(self) -> List[str]:
        return self._registration.load_config()

    def _register_all(self):
        return self._registration.register_all()

    def start(self):
        db_jobs = self.store.load_jobs()
        if db_jobs:
            self.jobs = db_jobs
        else:
            errs = self.load_config()
            for e in errs:
                logger.error(f"配置错误: {e}")
        self.service_groups = self.store.load_service_groups()
        self.recover_on_start()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, name="phase2-heartbeat", daemon=True)
        self._heartbeat_thread.start()
        self._register_all()
        self.aps.start()

    def shutdown(self):
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_stop = True
        for run_id in list(self.running.keys()):
            run = self.running.get(run_id)
            if run is None:
                continue
            # 独立后台进程（detach）随服务常驻：停止调度服务不连带中止它，
            # 交给下次启动的恢复机制按身份核验后重新接管；仅中止非 detach 运行实例。
            if getattr(run, "detached", False):
                continue
            self.abort(run_id)
        self.aps.shutdown(wait=False)

    # ---- 触发（实现在 process_control）----
    def _trigger_job(self, job_name: str, trigger: str = "manual"):
        return self._control.trigger_job(job_name, trigger)

    def _run_worker(self, run: Run, job: Job):
        return self._control.run_worker(run, job)

    # ---- 启动恢复 / 孤儿接管 / 状态核对（实现在 recovery）----
    def recover_on_start(self):
        return self._recovery.recover_on_start()

    def _reconcile_terminal_runs(self):
        return self._recovery.reconcile_terminal_runs()

    def _recover_one(self, run: Run):
        return self._recovery.recover_one(run)

    def _adopt_orphan(self, run: Run):
        return self._recovery.adopt_orphan(run)

    def _resume_detached_monitor(self, run: Run):
        return self._recovery.resume_detached_monitor(run)

    def _detached_watch_loop(self, run: Run):
        return self._recovery.detached_watch_loop(run)

    def _mark_detached_gone(self, run: Run):
        return self._recovery.mark_detached_gone(run)

    # ---- 心跳（实现在 heartbeat）----
    def _heartbeat_loop(self):
        return self._heartbeat.loop()

    def _heartbeat_tick(self):
        return self._heartbeat.tick()

    @staticmethod
    def _within_timeout(ts, timeout_sec: float) -> bool:
        return HeartbeatMonitor.within_timeout(ts, timeout_sec)

    def _check_heartbeat_timeout(self, run: Run):
        return self._heartbeat.check_timeout(run)

    # ---- 触发 / 中止 / 停止（实现在 process_control）----
    def trigger(self, job_name: str):
        return self._control.trigger(job_name)

    def abort(self, run_id: str) -> bool:
        return self._control.abort(run_id)

    def stop(self, run_id: str) -> dict:
        return self._control.stop(run_id)

    def _sweep_shell_orphans(self, run: Run) -> str:
        return self._control.sweep_shell_orphans(run)

    @staticmethod
    def _pid_exists_raw(pid) -> bool:
        return ProcessController.pid_exists_raw(pid)

    def _finalize_user_stop(self, run: Run, reason: str, status: str):
        return self._control.finalize_user_stop(run, reason, status)

    def add_job(self, job: Job):
        with self._lock:
            self.jobs[job.name] = job
            self.store.upsert_jobs([job])
        self._register_job(job)

    def update_job(self, job: Job, old_name: Optional[str] = None):
        """更新任务。old_name 与 job.name 不同时表示改名。"""
        renamed = bool(old_name) and old_name != job.name
        with self._lock:
            existed = job.name in self.jobs or (old_name in self.jobs if old_name else False)
            if renamed:
                self.jobs.pop(old_name, None)
                self.jobs[job.name] = job
                self.store.rename_job(old_name, job)
                self._rename_in_service_groups(old_name, job.name)
            else:
                self.jobs[job.name] = job
                self.store.upsert_jobs([job])
        if renamed:
            self._unregister_job(old_name)
        self._register_job(job)
        if not existed:
            logger.info(f"created job {job.name} via update (formerly absent)")
        elif renamed:
            logger.info(f"renamed job {old_name} -> {job.name}")

    def _rename_in_service_groups(self, old_name: str, new_name: str):
        """任务改名后，同步更新服务组成员里的引用"""
        changed = []
        for group in self.service_groups.values():
            hit = False
            for m in group.members:
                if m.get("job_name") == old_name:
                    m["job_name"] = new_name
                    hit = True
            if hit:
                changed.append(group)
        for group in changed:
            self.store.upsert_service_group(group)
        if changed:
            logger.info(f"service groups updated for rename {old_name} -> {new_name}: "
                        f"{[g.name for g in changed]}")

    def delete_job(self, name: str):
        with self._lock:
            self.jobs.pop(name, None)
            self.store.delete_job(name)
        self._unregister_job(name)
        logger.info(f"deleted job {name}")

    def set_job_enabled(self, name: str, enabled: bool):
        with self._lock:
            job = self.jobs.get(name)
            if job is None:
                return False
            if not enabled and any(r.job_name == name for r in self.running.values()):
                raise ValueError(f"任务「{name}」正在执行，不能停用")
            job.enabled = enabled
            self.store.upsert_jobs([job])
        if enabled:
            self._register_job(job)
        else:
            self._unregister_job(name)
        logger.info(f"job {name} enabled={enabled}")
        return True

    def _register_job(self, job: Job):
        return self._registration.register_job(job)

    def _unregister_job(self, name: str):
        return self._registration.unregister_job(name)

    # ---- 任务 / 分组 / 服务组 视图（实现在 views）----
    def jobs_view(self, *, sort: Optional[str] = None, order: str = "desc") -> List[dict]:
        return self._views.jobs_view(sort=sort, order=order)

    def jobs_search(self, *, job_type: Optional[str] = None, enabled: Optional[str] = None,
                    q: Optional[str] = None, sort: Optional[str] = None, order: str = "desc",
                    offset: int = 0, limit: int = 50) -> dict:
        return self._views.jobs_search(job_type=job_type, enabled=enabled, q=q,
                                       sort=sort, order=order,
                                       offset=offset, limit=limit)

    def groups_view(self) -> List[dict]:
        return self._views.groups_view()

    def service_groups_view(self) -> List[dict]:
        return self._views.service_groups_view()

    def _service_group_view(self, group: ServiceGroup) -> dict:
        return self._views.service_group_view(group)

    def _group_member_last(self, job_name: str) -> Optional[dict]:
        return self._views._group_member_last(job_name)

    # ---- 服务组（实现在 service_groups）----
    def add_service_group(self, group: ServiceGroup):
        return self._group_exec.add(group)

    def update_service_group(self, group: ServiceGroup):
        return self._group_exec.update(group)

    def delete_service_group(self, name: str):
        return self._group_exec.delete(name)

    def start_service_group(self, name: str) -> dict:
        return self._group_exec.start(name)

    def stop_service_group(self, name: str) -> dict:
        return self._group_exec.stop(name)

    def get_running(self, run_id: str):
        return self._views.get_running(run_id)

    def read_log_file(self, run_id: str) -> Optional[List[str]]:
        # 实现在 log_manager（此处保留方法名以兼容既有调用）
        return self._logs.read_log_file(run_id)

    def _sort_key(self, obj, field: str):
        v = getattr(obj, field, None)
        if v is None:
            return ""
        return v

    # ---- 运行中 / 历史 视图（实现在 views）----
    def running_view(self, *, job_type: Optional[str] = None, q: Optional[str] = None,
                     status: Optional[str] = None,
                     sort: Optional[str] = None, order: str = "desc",
                     offset: int = 0, limit: int = 100) -> dict:
        return self._views.running_view(job_type=job_type, q=q, status=status,
                                        sort=sort, order=order,
                                        offset=offset, limit=limit)

    def history_view(self, status: Optional[str] = None, job_name: Optional[str] = None,
                     job_type: Optional[str] = None, q: Optional[str] = None,
                     sort: Optional[str] = None, order: str = "desc",
                     offset: int = 0, limit: int = 50) -> dict:
        return self._views.history_view(status=status, job_name=job_name, job_type=job_type,
                                        q=q, sort=sort, order=order,
                                        offset=offset, limit=limit)

    @staticmethod
    def _sort_key(obj, field: str):
        return Views._sort_key(obj, field)

def _strip_json_comments(text: str) -> str:
    import re
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return text