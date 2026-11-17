from __future__ import annotations

import locale
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from ..models import Job, Run
from .. import identity as identity_mod
from . import command_parser
from . import file_operations
from . import log_monitor
from . import process_manager
from . import timeout as timeout_mod
from . import win_job

_IO_ENCODING = locale.getpreferredencoding(False) or "utf-8"
process_manager.configure(_IO_ENCODING)


class Executor:
    """负责把 Job 变成真实的 OS 子进程 / 文件操作流程."""

    def __init__(self, runner):
        self.runner = runner  # 回引用 Scheduler，用于上报状态
        self._procs: Dict[str, subprocess.Popen] = {}  # run_id -> Popen
        self._jobs: Dict[str, int] = {}                # run_id -> 命名 Job 句柄
        self._user_stopped: set = set()
        self._lock = threading.Lock()

    def mark_user_stopped(self, run_id: str):
        with self._lock:
            self._user_stopped.add(run_id)

    @staticmethod
    def p(job: Job, key: str, default=None):
        return job.params.get(key, default)

    @staticmethod
    def _detach_flags_variants() -> List[int]:

        base = (getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
        return [base | 0x01000000, base]

    # 父 Job 相关常量
    _KILL_ON_JOB_CLOSE = 0x00002000
    _BREAKAWAY_OK = 0x00000800
    _SILENT_BREAKAWAY_OK = 0x00001000

    @staticmethod
    def probe_job_environment() -> Dict[str, Optional[bool]]:
        return win_job.probe_job_environment()

    @staticmethod
    def _spawn_flags(detach: bool) -> int:
        """非 detach 用 CREATE_NO_WINDOW 隐藏窗口（跟随调度中心生命周期）。"""
        if not Executor._is_windows():
            return 0
        if detach:
            return Executor._detach_flags_variants()[0]
        return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    def _resolve_log_path(self, run: Run) -> str:
        prep = getattr(self.runner, "prepare_log_path", None)
        if callable(prep):
            return prep(run)
        if run.log_path:
            Path(run.log_path).parent.mkdir(parents=True, exist_ok=True)
            return run.log_path
        import tempfile
        d = Path(tempfile.gettempdir()) / "scheduler-runs"
        d.mkdir(parents=True, exist_ok=True)
        run.log_path = str(d / f"{run.id}.log")
        return run.log_path

    def _spawn(self, argv: List[str], logf, cwd_env, env, detach: bool):
        """启动子进程"""
        return process_manager.spawn(
            argv, logf, cwd_env, env, detach, Executor._detach_flags_variants())

    @staticmethod
    def _warn_isolation(envprobe: Dict[str, Optional[bool]]):
        """把 Job 隔离缺失告警打到日志"""
        import logging
        logging.getLogger("scheduler").warning(
            "detach 任务未能获得 Job 级隔离：in_job=%s kill_on_job_close=%s "
            "breakaway_allowed=%s -> 调度中心被强制结束时该任务可能被连带终止",
            envprobe.get("in_job"), envprobe.get("kill_on_close"),
            envprobe.get("breakaway_allowed"))


    def _job_attach(self, run: Run, pid: int) -> bool:
        """为运行实例建立命名 Job 并把进程收入"""
        return win_job.job_attach(run, pid, self._jobs, self._lock)

    def _job_terminate(self, run_id: str) -> bool:
        """用 Job 句柄终止整组"""
        return win_job.job_terminate(run_id, self._jobs, self._lock)

    def _job_close(self, run_id: str):
        """释放 Job 句柄。"""
        return win_job.job_close(run_id, self._jobs, self._lock)

    @staticmethod
    def _is_windows() -> bool:
        return win_job.is_windows()

    _SHELL_HINTS = command_parser.SHELL_HINTS
    _SHELL_BUILTINS = command_parser.SHELL_BUILTINS

    @staticmethod
    def _script_prefix(path: str) -> List[str]:
        return command_parser.script_prefix(path)

    @staticmethod
    def _strip_quoted(command: str) -> str:
        return command_parser.strip_quoted(command)

    @staticmethod
    def _needs_shell(command: str) -> bool:
        return command_parser.needs_shell(command)

    @staticmethod
    def _split_command(command: str) -> List[str]:
        return command_parser.split_command(command)

    @staticmethod
    def _build_argv(job: Job) -> List[str]:
        return command_parser.build_argv(job)

    def _run_fileop(self, job: Job, run: Run) -> bool:
        return file_operations.run_fileop(job, run, self.runner)

    def run(self, run: Run, job: Job):
        return self._run_local(run, job)

    def _run_local(self, run: Run, job: Job):
        run.status = "RUNNING"
        run.start = _now_local()
        run.pid = None
        run.error = ""
        proc: Optional[subprocess.Popen] = None
        timer = None
        was_user_stopped = False

        try:
            if job.type == "FILEOP":
                self._run_fileop(job, run)
                run.exit_code = 0
                run.status = "SUCCEEDED"
                self.runner.on_started(run)
                self.runner.on_finished(run)
                return

            argv = self._build_argv(job)
            env = os.environ.copy()
            if job.params.get("env"):
                env.update({str(k): str(v) for k, v in job.params["env"].items()})
            cwd_env = job.params.get("workdir") or None
            detach = bool(job.params.get("detach", False))
            env.setdefault("PYTHONUNBUFFERED", "1")
            env.setdefault("PYTHONIOENCODING", "utf-8")
            log_path = self._resolve_log_path(run)
            logf = open(log_path, "ab")
            try:
                proc, has_breakaway = self._spawn(argv, logf, cwd_env, env, detach)
            finally:
                logf.close()
            run.pid = proc.pid
            if detach and not has_breakaway:
                envprobe = Executor.probe_job_environment()
                if envprobe.get("kill_on_close") and not envprobe.get("breakaway_allowed"):
                    run.isolation_warning = (
                        "调度中心自身运行在带 KILL_ON_JOB_CLOSE 的 Job 中且不允许 "
                        "breakaway，任务进程无法获得 Job 级隔离：调度中心被强制结束时"
                        "该任务可能被连带终止。建议改用 run_headless.py 启动调度中心。")
                    self._warn_isolation(envprobe)

            self._job_attach(run, proc.pid)

            run.detached = detach
            run.via_shell = bool(
                argv and Path(str(argv[0])).name.lower() in (
                    "cmd.exe", "cmd", "sh", "/bin/sh", "bash", "/bin/bash")
            )
            run.process_group_id = None
            ident = identity_mod.capture_identity(
                proc.pid, cmdline=argv,
                own_exe_hint=(argv[0] if argv else None),
            )
            run.process_create_time = ident["process_create_time"]
            run.executable_path = ident["executable_path"]
            run.command_line_fingerprint = ident["command_line_fingerprint"]
            run.process_group_id = ident["process_group_id"]
            if not run.process_create_time:
                run.status = "INTERRUPTED"
                run.error = "无法采集进程创建时刻（身份凭据），已按安全规则拒绝继续"
                raise RuntimeError(run.error)

            with self._lock:
                self._procs[run.id] = proc
            self.runner.on_started(run)

            if not detach and job.timeout_s and job.timeout_s > 0:
                timer = timeout_mod.start_timer(
                    job.timeout_s, proc, run, self._kill_proc)

            if detach:
                self._tail_detached(run, log_path, proc)
                was_user_stopped = run.id in self._user_stopped
                run.exit_code = proc.returncode
                if run.status not in ("KILLED", "TIMEDOUT") and not was_user_stopped:
                    if proc.returncode == 0:
                        run.status = "SUCCEEDED"
                        run.error = ""
                    else:
                        run.status = "INTERRUPTED"
                        run.interrupted_reason = (
                            f"进程非 0 退出（exit={proc.returncode}）且非调度中心主动停止，"
                            f"已判定为外部终止/异常退出"
                        )
                        run.error = f"退出码 {proc.returncode}（外部终止或异常退出）"
            else:
                self._tail_detached(run, log_path, proc)
                run.exit_code = proc.returncode
                was_user_stopped = run.id in self._user_stopped
                if run.status not in ("KILLED", "TIMEDOUT") and not was_user_stopped:
                    self._verify_exit_was_external(run)
                    if proc.returncode == 0:
                        run.status = "SUCCEEDED"
                        run.error = ""
                    else:
                        run.status = "INTERRUPTED"
                        run.interrupted_reason = (
                            f"进程非 0 退出（exit={proc.returncode}）且非调度中心主动停止，"
                            f"已判定为外部终止/异常退出"
                        )
                        run.error = f"退出码 {proc.returncode}（外部终止或异常退出）"
        except FileNotFoundError as e:
            run.status = "FAILED"
            run.error = f"找不到可执行目标: {e}"
        except Exception as e:
            run.status = "FAILED"
            run.error = str(e)
        finally:
            if timer:
                timer.cancel()
            with self._lock:
                self._procs.pop(run.id, None)
                self._user_stopped.discard(run.id)
            run.end = _now_local()
            if was_user_stopped:
                self.runner.on_user_stopped(run)
            else:
                self.runner.on_finished(run)

    def _tail_detached(self, run: Run, log_path: str, proc: subprocess.Popen):
        return log_monitor.tail_detached(
            self.runner, run, log_path, proc, self._user_stopped, self._kill_proc)

    def _pump_log(self, run: Run, log_path: str, pos: int) -> int:
        return log_monitor.pump_log(self.runner, run, log_path, pos)

    def _emit_log(self, run: Run, line: str):
        return log_monitor.emit_log(self.runner, run, line)

    def abort(self, run: Run) -> bool:
        with self._lock:
            proc = self._procs.get(run.id)
        if proc is not None:
            self._user_stopped.add(run.id)
            if not self._job_terminate(run.id):
                self._kill_proc(proc)
            run.status = "KILLED"
            run.error = "用户中止"
            return True
        return False

    def _on_timeout(self, proc: subprocess.Popen, run: Run):
        return timeout_mod.on_timeout(proc, run, self._kill_proc)

    def _verify_exit_was_external(self, run: Run):
        try:
            if not run.pid:
                return
            identity_mod.process_is_alive(run, run.pid)
        except Exception:
            pass

    @staticmethod
    def kill_by_pid(pid) -> bool:
        return process_manager.kill_by_pid(pid)

    @staticmethod
    def _pid_exists(pid) -> bool:
        return process_manager.pid_exists(pid)

    @staticmethod
    def _kill_proc(proc: subprocess.Popen):
        return process_manager.kill_proc(proc)


def _now_local() -> str:
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec="seconds")