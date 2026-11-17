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

    def load_config(self) -> List[str]:
        import json

        with open(self.config_path, "r", encoding="utf-8") as f:
            raw = f.read()
        raw = _strip_json_comments(raw)
        data = json.loads(raw)

        jobs: List[Job] = []
        errors: List[str] = []
        self.groups = {}

        if isinstance(data, dict):
            groups = data.get("groups")
            if groups is not None:
                for g in groups:
                    g_key = str(g.get("key") or g.get("name") or "")
                    g_task_type = _as_type(g.get("task_type"))
                    self.groups[g_key] = {
                        "key": g_key,
                        "name": str(g.get("name") or g_key),
                        "remark": str(g.get("remark") or ""),
                        "task_type": g_task_type,
                    }
                    for item in g.get("items", []):
                        entry = dict(item)
                        entry.setdefault("group", g_key)
                        if not entry.get("type") and g_task_type:
                            entry["type"] = g_task_type
                        job = Job.from_dict(entry)
                        _collect_job(job, jobs, errors)
            else:
                flat = data.get("jobs", [])
                if isinstance(flat, list):
                    for entry in flat:
                        job = Job.from_dict(entry)
                        _collect_job(job, jobs, errors)
                else:
                    errors.append("cfg 顶层需是 groups 或 jobs 数组")
        elif isinstance(data, list):
            for entry in data:
                job = Job.from_dict(entry)
                _collect_job(job, jobs, errors)
        else:
            errors.append("配置根节点必须是对象或数组")

        if not errors:
            self.jobs = {j.name: j for j in jobs}
            self.store.upsert_jobs(jobs)
        return errors

    def _register_all(self):
        self.aps.remove_all_jobs()
        for name, job in self.jobs.items():
            if job.enabled and job.schedule:
                try:
                    self.aps.add_job(
                        self._trigger_job,
                        CronTrigger.from_crontab(job.schedule),
                        args=[name, "cron"],
                        id=f"job_{name}",
                        replace_existing=True,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.error(f"Job[{name}] cron 注册失败: {e}")

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

    def _trigger_job(self, job_name: str, trigger: str = "manual"):
        with self._lock:
            job = self.jobs.get(job_name)
            if job is None:
                return None
            if not job.enabled:
                return None
            if any(r.job_name == job_name for r in self.running.values()):
                return None
            run = Run(
                id=self.store.new_run_id(),
                job_name=job.name,
                job_type=job.type,
                trigger=trigger,
                timeout_s=job.timeout_s,
            )
            run.status = "STARTING"
            run.node_id = self.node_id
            run.detached = bool(job.params.get("detach", False))
            self.running[run.id] = run
        self.store.save_run(run)
        t = threading.Thread(target=self._run_worker, args=(run, job), daemon=True)
        t.start()
        return run

    def _run_worker(self, run: Run, job: Job):
        try:
            self.executor.run(run, job)
        except Exception as e:
            run.status = "FAILED"
            run.error = str(e)
            self.on_finished(run)

    def _adopt_orphan(self, run: Run):

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

    def recover_on_start(self):
        candidates = self.store.running_runs()
        for run in candidates:
            try:
                self._recover_one(run)
                if run.status == "RUNNING":
                    with self._lock:
                        self.running[run.id] = run
            except Exception as e:  # noqa: BLE001
                logger.error(f"恢复 run[{run.id}] 时异常: {e}")
        try:
            self._reconcile_terminal_runs()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"终态记录一致性核对失败: {e}")

    def _reconcile_terminal_runs(self):
        fixed = 0
        for run in self.store.recent_runs(200):
            if run.status in ("RUNNING", "STARTING", "ORPHANED", "UNKNOWN"):
                continue  # 非终态已由 _recover_one 处理
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
            self.store.save_run(run)
            self._publish_status_change(run)
            with self._lock:
                self.running[run.id] = run
            t = threading.Thread(target=self._detached_watch_loop, args=(run,),
                                 name=f"reconcile-watch-{run.id}", daemon=True)
            t.start()
            fixed += 1
            logger.warning(
                f"状态不一致已修正 run[{run.id}]: db={prev} -> RUNNING (pid={pid})")
        if fixed:
            logger.info(f"终态记录核对完成：修正 {fixed} 条不一致记录")

    def _resume_detached_monitor(self, run: Run):
        t = threading.Thread(target=self._detached_watch_loop, args=(run,),
                             name=f"detach-watch-{run.id}", daemon=True)
        t.start()
        logger.info(f"恢复 run[{run.id}] detach 监控已重建 (pid={run.pid})")

    def _detached_watch_loop(self, run: Run):
        log_path = run.log_path or ""
        pos = int(getattr(run, "log_offset", 0) or 0)
        try:
            if log_path and os.path.isfile(log_path) and os.path.getsize(log_path) < pos:
                pos = 0  # 文件被截断/轮转
        except OSError:
            pass
        while not self._heartbeat_stop:
            try:
                with self._lock:
                    cur = self.running.get(run.id)
                if cur is None or cur.status not in ("RUNNING", "ORPHANED"):
                    return
                if log_path:
                    pos = self.executor._pump_log(run, log_path, pos)
                if not identity_mod.process_is_alive(run, run.pid):
                    self._mark_detached_gone(run)
                    return
            except Exception as e:  # noqa: BLE001
                logger.warning(f"detach 监控 run[{run.id}] 异常: {e}")
            if self._heartbeat_stop:
                return
            time.sleep(1)

    def _mark_detached_gone(self, run: Run):
        """detach 进程已确认退出：写入结束时间并归入历史。"""
        with self._lock:
            cur = self.running.pop(run.id, None) or run
            cur.status = "INTERRUPTED"
            cur.interrupted_reason = (
                cur.interrupted_reason or "独立进程已退出（OS 探针核验），标记为中断并归入历史")
            cur.end = cur.end or datetime.now().astimezone().isoformat(timespec="seconds")
        self.store.save_run(cur)
        self._publish_status_change(cur)
        self.publish_finished(cur)
        self._publish_log_end(cur.id)
        logger.info(f"detach run[{cur.id}] 进程已退出 -> INTERRUPTED")

    def _recover_one(self, run: Run):
        if not run.process_create_time:
            if run.status == "STARTING":
                run.status = "UNKNOWN"
                run.interrupted_reason = ("残留 STARTING 预登记，无有效身份凭据；"
                                          "启动意图已保存，无法认领具体进程，转待核查")
            elif run.status in ("RUNNING", "ORPHANED", "UNKNOWN"):
                run.status = "UNKNOWN"
                run.interrupted_reason = ("Phase-1 遗留：缺少进程身份凭据(create_time)，"
                                          "无法安全核验，转待核查")
            self.store.save_run(run)
            self._publish_status_change(run)
            return

        alive = identity_mod.process_is_alive(run, run.pid)
        if alive:
            run.last_heartbeat = datetime.now().astimezone().isoformat(timespec="seconds")
            if run.status in ("STARTING",):
                run.status = "RUNNING"
                run.interrupted_reason = ""
                self.store.save_run(run)
                self._publish_status_change(run)
                logger.info(f"恢复 run[{run.id}] 存活 -> RUNNING")
                return
            if run.status == "UNKNOWN":
                run.status = "ORPHANED"
                run.interrupted_reason = ("重启恢复：进程存活且身份匹配，转孤儿管理，"
                                          "待接管核查")
                self.store.save_run(run)
                self._publish_status_change(run)
                logger.info(f"恢复 run[{run.id}] 存活 & 身份匹配 -> ORPHANED")
                return
            if run.status == "ORPHANED":
                adopted, note = self._adopt_orphan(run)
                run.adopted = adopted
                if adopted:
                    run.status = "RUNNING"
                    run.interrupted_reason = ""
                else:
                    run.interrupted_reason = (run.interrupted_reason or
                                              "四问接管未全过: " + note)
                self.store.save_run(run)
                self._publish_status_change(run)
                logger.info(f"恢复 run[{run.id}] ORPHANED -> {'RUNNING(接管)' if adopted else '保持ORPHANED'} {note}")
                if adopted and getattr(run, "detached", False):
                    self._resume_detached_monitor(run)
                return
            self.store.save_run(run)
            logger.info(f"恢复 run[{run.id}] RUNNING 存活 -> 保持")
            if getattr(run, "detached", False):
                self._resume_detached_monitor(run)
            return
        if run.status == "INTERRUPTED":
            return
        run.status = "INTERRUPTED"
        run.interrupted_reason = run.interrupted_reason or (
            "重启恢复：进程已确认不在运行（OS 探针核验），标记为中断并归入历史")
        run.end = run.end or datetime.now().astimezone().isoformat(timespec="seconds")
        run.last_heartbeat = datetime.now().astimezone().isoformat(timespec="seconds")
        self.store.save_run(run)
        self._publish_status_change(run)
        logger.info(f"恢复 run[{run.id}] 已确认结束 -> INTERRUPTED")

    def _heartbeat_loop(self):
        while not self._heartbeat_stop:
            try:
                self._heartbeat_tick()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"心跳 tick 异常: {e}")
            for _ in range(self.heartbeat_interval):
                if self._heartbeat_stop:
                    return
                time.sleep(1)

    def _heartbeat_tick(self):

        runs = self.store.running_runs()
        managed = [r for r in runs
                   if r.status in ("RUNNING", "ORPHANED", "STARTING")]
        now_s = datetime.now().astimezone().isoformat(timespec="seconds")
        timeout_sec = self.heartbeat_interval * _heartbeat_timeout_times()

        for r in managed:
            r.last_heartbeat = now_s
            self.store.save_run(r, heartbeat_only=True)
            hb = r.last_heartbeat or r.start
            if not self._within_timeout(hb, timeout_sec):
                self._check_heartbeat_timeout(r)

    @staticmethod
    def _within_timeout(ts, timeout_sec: float) -> bool:
        try:
            from datetime import datetime as _dt
            t = _dt.fromisoformat(ts.replace("Z", "+00:00"))
            return (datetime.now().astimezone() - t).total_seconds() <= timeout_sec
        except Exception:  # noqa: BLE001
            return False

    def _check_heartbeat_timeout(self, run: Run):
        if identity_mod.process_is_alive(run, run.pid):
            run.last_heartbeat = datetime.now().astimezone().isoformat(timespec="seconds")
            self.store.save_run(run, heartbeat_only=True)
            return
        if run.status == "INTERRUPTED":
            return
        run.status = "INTERRUPTED"
        run.interrupted_reason = (run.interrupted_reason or
                                  "心跳连续超时且 OS 探针确认进程已不在运行，判定中断")
        run.end = run.end or datetime.now().astimezone().isoformat(timespec="seconds")
        self.store.save_run(run)
        self._publish_status_change(run)
        logger.warning(f"心跳超时 + OS 探针确认死亡: run[{run.id}] -> INTERRUPTED")

    def trigger(self, job_name: str):
        with self._lock:
            if job_name not in self.jobs:
                return None
            if not self.jobs[job_name].enabled:
                return "disabled"
            if any(r.job_name == job_name for r in self.running.values()):
                return "blocked"
        return self._trigger_job(job_name, "api")

    def abort(self, run_id: str) -> bool:
        with self._lock:
            run = self.running.get(run_id)
        if run is None:
            # 重启恢复/孤儿实例没有 Popen 句柄，回落到按 pid 身份核验的停止逻辑，
            # 否则会出现「已标记用户中止但进程其实还活着」的假成功。
            res = self.stop(run_id)
            return bool(res.get("ok") and res.get("stop"))
        self.executor.abort(run)
        self._finalize_user_stop(run, "用户中止", "KILLED")
        return True

    def stop(self, run_id: str) -> dict:
        run = self.store.get_run(run_id)
        if run is None:
            return {"ok": False, "stop": False, "reason": "run_not_found"}
        pid = getattr(run, "pid", None)
        if not pid:
            return {"ok": True, "stop": False, "reason": "no_pid"}

        identity_ok = identity_mod.verify_identity(run, pid)
        if not identity_ok:
            still = self._pid_exists_raw(pid)
            return {
                "ok": False, "stop": False, "pid": pid,
                "reason": "身份核验失败：PID 或进程创建时刻不匹配（可能 PID 已被复用），拒绝终止",
                "process_still_exists": still,
            }

        stop_via = "tree"
        if self.executor._job_terminate(run.id):
            stop_via = "job"
            for _ in range(30):
                if not identity_mod.process_is_alive(run, pid):
                    break
                time.sleep(0.2)
        else:
            reason = identity_mod.safe_kill(run, pid, group_id=run.process_group_id)
            if reason is not None:
                if identity_mod.process_is_alive(run, pid):
                    return {"ok": False, "stop": False, "pid": pid, "reason": reason}
                reason = None
        orphan_note = ""
        if getattr(run, "via_shell", False):
            orphan_note = self._sweep_shell_orphans(run)

        self.executor.mark_user_stopped(run.id)
        with self._lock:
            in_running = run.id in self.running
        if in_running:
            self._finalize_user_stop(run, "用户停止", "KILLED")
        self.executor._job_close(run.id)
        res = {"ok": True, "stop": True, "pid": pid,
               "reason": "killed" if in_running else "already_stopped",
               "stop_via": stop_via}
        if orphan_note:
            res["orphan_sweep"] = orphan_note
        return res

    def _sweep_shell_orphans(self, run: Run) -> str:
        if os.name != "nt":
            return ""
        pid = getattr(run, "pid", None)
        if not pid:
            return ""
        try:
            import subprocess as _sp
            ps = (
                "Get-CimInstance Win32_Process -Filter \"ParentProcessId="
                + str(int(pid)) + "\" | Select-Object -ExpandProperty ProcessId"
            )
            out = _sp.run(["powershell", "-NoProfile", "-Command", ps],
                          capture_output=True, text=True, errors="replace", timeout=20)
            kids = [t for t in (out.stdout or "").split() if t.isdigit()
                    and int(t) != os.getpid()]
            if not kids:
                return ""
            for k in kids:
                _sp.run(["taskkill", "/F", "/T", "/PID", k],
                        capture_output=True, timeout=10)
            return f"cleaned_children={','.join(kids)}"
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _pid_exists_raw(pid) -> bool:
        """纯 OS 层判断 pid 是否存在，不依赖身份凭据"""
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return False
        if pid <= 0:
            return False
        if os.name == "nt":
            try:
                import subprocess as _sp
                out = _sp.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                              capture_output=True, text=True, errors="replace", timeout=10)
                return f'"{pid}"' in (out.stdout or "")
            except Exception:  # noqa: BLE001
                return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _finalize_user_stop(self, run: Run, reason: str, status: str):
        with self._lock:
            if run.id in self.running:
                cur = self.running[run.id]
            else:
                cur = run
            cur.status = status
            cur.error = reason
            cur.end = cur.end or datetime.now().astimezone().isoformat(timespec="seconds")
            self.running.pop(run.id, None)
        self.store.save_run(cur)
        self.publish_finished(cur)
        self._publish_log_end(run.id)

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
        try:
            self.aps.remove_job(f"job_{job.name}")
        except Exception:
            pass
        if job.enabled and job.schedule:
            try:
                self.aps.add_job(
                    self._trigger_job,
                    CronTrigger.from_crontab(job.schedule),
                    args=[job.name, "cron"],
                    id=f"job_{job.name}",
                    replace_existing=True,
                )
            except Exception as e:
                logger.error(f"Job[{job.name}] cron 注册失败: {e}")

    def _unregister_job(self, name: str):
        try:
            self.aps.remove_job(f"job_{name}")
        except Exception:
            pass

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

    def add_service_group(self, group: ServiceGroup):
        with self._lock:
            self.service_groups[group.name] = group
            self.store.upsert_service_group(group)
        logger.info(f"service_group created: {group.name} ({len(group.members)} members)")

    def update_service_group(self, group: ServiceGroup):
        with self._lock:
            self.service_groups[group.name] = group
            self.store.upsert_service_group(group)
        logger.info(f"service_group updated: {group.name}")

    def delete_service_group(self, name: str):
        with self._lock:
            self.service_groups.pop(name, None)
            self.store.delete_service_group(name)
        logger.info(f"service_group deleted: {name}")

    def start_service_group(self, name: str) -> dict:
        with self._lock:
            group = self.service_groups.get(name)
        if group is None:
            return {"ok": False, "reason": "not_found"}
        started = []
        errors = []
        for m in sorted(group.members, key=lambda x: int(x.get("order", 0))):
            jn = str(m.get("job_name") or "")
            with self._lock:
                job = self.jobs.get(jn)
                if job is None:
                    errors.append(f"{jn}: 任务不存在")
                    continue
                if any(r.job_name == jn and r.status == "RUNNING"
                       for r in self.running.values()):
                    run = next(r for r in self.running.values()
                               if r.job_name == jn and r.status == "RUNNING")
                    started.append({"job_name": jn, "run_id": run.id, "status": "already_running"})
                    continue
            run = self._trigger_job(jn, "api")
            started.append({"job_name": jn, "run_id": run.id if run else None,
                            "status": "started" if run else "failed"})
            if run is None:
                errors.append(f"{jn}: 启动失败")
        return {"ok": not errors, "name": name, "started": started, "errors": errors}

    def stop_service_group(self, name: str) -> dict:
        with self._lock:
            group = self.service_groups.get(name)
        if group is None:
            return {"ok": False, "reason": "not_found"}
        stopped = []
        errors = []
        for m in group.members:
            jn = str(m.get("job_name") or "")
            with self._lock:
                run = next((r for r in self.running.values()
                            if r.job_name == jn and r.status == "RUNNING"), None)
            if run is None:
                continue
            res = self.stop(run.id)
            if res.get("ok"):
                stopped.append({"job_name": jn, "run_id": run.id,
                                "stopped": res.get("stop"),
                                "reason": res.get("reason")})
            else:
                errors.append(f"{jn}: 停止失败 ({res.get('reason')})")
        return {"ok": not errors, "name": name, "stopped": stopped, "errors": errors}

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