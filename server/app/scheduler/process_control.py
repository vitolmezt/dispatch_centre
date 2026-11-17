# -*- coding: utf-8 -*-
"""运行控制：触发、中止、停止与孤儿清理。"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime

from .. import identity as identity_mod
from ..models import Job, Run

logger = logging.getLogger("scheduler")


class ProcessController:

    def __init__(self, sched) -> None:
        self._sched = sched

    def trigger(self, job_name: str):
        s = self._sched
        with s._lock:
            if job_name not in s.jobs:
                return None
            if not s.jobs[job_name].enabled:
                return "disabled"
            if any(r.job_name == job_name for r in s.running.values()):
                return "blocked"
        return self.trigger_job(job_name, "api")

    def trigger_job(self, job_name: str, trigger: str = "manual"):
        s = self._sched
        with s._lock:
            job = s.jobs.get(job_name)
            if job is None:
                return None
            if not job.enabled:
                return None
            if any(r.job_name == job_name for r in s.running.values()):
                return None
            run = Run(
                id=s.store.new_run_id(),
                job_name=job.name,
                job_type=job.type,
                trigger=trigger,
                timeout_s=job.timeout_s,
            )
            run.status = "STARTING"
            run.node_id = s.node_id
            run.detached = bool(job.params.get("detach", False))
            s.running[run.id] = run
        s.store.save_run(run)
        t = threading.Thread(target=self.run_worker, args=(run, job), daemon=True)
        t.start()
        return run

    def run_worker(self, run: Run, job: Job) -> None:
        s = self._sched
        try:
            s.executor.run(run, job)
        except Exception as e:  # noqa: BLE001
            run.status = "FAILED"
            run.error = str(e)
            s.on_finished(run)

    def abort(self, run_id: str) -> bool:
        s = self._sched
        with s._lock:
            run = s.running.get(run_id)
        if run is None:
            res = self.stop(run_id)
            return bool(res.get("ok") and res.get("stop"))
        s.executor.abort(run)
        self.finalize_user_stop(run, "用户中止", "KILLED")
        return True

    def stop(self, run_id: str) -> dict:
        s = self._sched
        run = s.store.get_run(run_id)
        if run is None:
            return {"ok": False, "stop": False, "reason": "run_not_found"}
        pid = getattr(run, "pid", None)
        if not pid:
            return {"ok": True, "stop": False, "reason": "no_pid"}

        identity_ok = identity_mod.verify_identity(run, pid)
        if not identity_ok:
            still = self.pid_exists_raw(pid)
            return {
                "ok": False, "stop": False, "pid": pid,
                "reason": "身份核验失败：PID 或进程创建时刻不匹配（可能 PID 已被复用），拒绝终止",
                "process_still_exists": still,
            }

        stop_via = "tree"
        if s.executor._job_terminate(run.id):
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
            orphan_note = self.sweep_shell_orphans(run)

        s.executor.mark_user_stopped(run.id)
        with s._lock:
            in_running = run.id in s.running
        if in_running:
            self.finalize_user_stop(run, "用户停止", "KILLED")
        s.executor._job_close(run.id)
        res = {"ok": True, "stop": True, "pid": pid,
               "reason": "killed" if in_running else "already_stopped",
               "stop_via": stop_via}
        if orphan_note:
            res["orphan_sweep"] = orphan_note
        return res

    def sweep_shell_orphans(self, run: Run) -> str:
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
    def pid_exists_raw(pid) -> bool:
        """纯 OS 层判断 pid 是否存在，不依赖身份凭据。"""
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

    def finalize_user_stop(self, run: Run, reason: str, status: str) -> None:
        s = self._sched
        with s._lock:
            if run.id in s.running:
                cur = s.running[run.id]
            else:
                cur = run
            cur.status = status
            cur.error = reason
            cur.end = cur.end or datetime.now().astimezone().isoformat(timespec="seconds")
            s.running.pop(run.id, None)
        s.store.save_run(cur)
        s.publish_finished(cur)
        s._publish_log_end(run.id)
