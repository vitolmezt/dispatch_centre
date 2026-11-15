from __future__ import annotations

import locale
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Dict, List, Optional

from .models import Job, Run
from . import identity as identity_mod

_IO_ENCODING = locale.getpreferredencoding(False) or "utf-8"


def _decode_line(raw: bytes) -> str:
    """把子进程输出的一行字节解码为 str，自动识别 UTF-8 / 系统编码."""
    no_lf = raw.rstrip(b"\n")
    if not no_lf:
        return ""
    try:
        return no_lf.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return no_lf.decode(_IO_ENCODING, errors="replace")
class Executor:
    """负责把 Job 变成真实的 OS 子进程 / 文件操作流程."""

    def __init__(self, runner):
        self.runner = runner  # 回引用 Scheduler，用于上报状态
        self._procs: Dict[str, subprocess.Popen] = {}  # run_id -> Popen
        self._user_stopped: set = set()
        self._lock = threading.Lock()

    def mark_user_stopped(self, run_id: str):
        """记录该 run 是"用户主动停止/中止"，之后的读线程不得用退出码覆盖其状态."""
        with self._lock:
            self._user_stopped.add(run_id)

    @staticmethod
    def p(job: Job, key: str, default=None):
        return job.params.get(key, default)

    @staticmethod
    def _is_windows() -> bool:
        return os.name == "nt"

    @staticmethod
    def _script_prefix(path: str) -> List[str]:
        """根据脚本扩展名返回合适的解释器命令。跨平台知识（菜单本身不需要）."""
        ext = Path(path).suffix.lower()
        if Executor._is_windows():
            if ext in (".ps1",):
                return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
            if ext in (".bat", ".cmd"):
                return ["cmd", "/c"]
            if ext in (".py", ".pyw"):
                return [sys.executable]
            return []
        # Unix
        if ext in (".sh",):
            return ["/bin/bash"]
        if ext in (".py",):
            return [sys.executable]
        return []

    @staticmethod
    def _build_argv(job: Job) -> List[str]:
        """构造 spawn 的 argv（不经过 shell，避免转义坑，也不依赖平台 shell）. """
        if job.type == "COMMAND":
            command = job.params.get("command", "")
            if Executor._is_windows():
                return ["cmd", "/c", command]
            return ["/bin/sh", "-c", command]
        if job.type == "SCRIPT":
            path = job.params.get("path", "")
            prefix = Executor._script_prefix(path)
            return prefix + [path] + list(job.params.get("args") or [])
        if job.type == "PROGRAM":
            path = job.params.get("path", "")
            return [path] + list(job.params.get("args") or [])
        raise ValueError(f"不依赖 OS 进程的 Job 类型: {job.type}")

    def _run_fileop(self, job: Job, run: Run) -> bool:
        action = str(job.params.get("action", "")).strip().upper()
        source = job.params.get("source", "")
        target = job.params.get("target", "")
        recursive = bool(job.params.get("recursive", False))

        src = Path(source)
        if action == "DELETE_FILE":
            if not src.exists():
                raise FileNotFoundError(f"删除目标不存在: {source}")
            if src.is_dir():
                if not recursive:
                    raise IsADirectoryError(f"目标是目录，需开启 recursive 才能删除: {source}")
                shutil.rmtree(src)
                self.runner.on_log(run, f"已递归删除目录: {source}")
            else:
                src.unlink()
                self.runner.on_log(run, f"已删除文件: {source}")
        elif action == "DELETE_DIR":
            if not src.exists():
                raise FileNotFoundError(f"删除目标不存在: {source}")
            if src.is_dir():
                shutil.rmtree(src)
            else:
                src.unlink()
            self.runner.on_log(run, f"已删除: {source}")
        elif action == "COPY":
            if not target:
                raise ValueError("copy 操作需要 target 参数")
            if src.is_dir():
                shutil.copytree(src, target, dirs_exist_ok=True)
            else:
                Path(target).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
            self.runner.on_log(run, f"已复制: {source} -> {target}")
        elif action == "MOVE":
            if not target:
                raise ValueError("move 操作需要 target 参数")
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), target)
            self.runner.on_log(run, f"已移动: {source} -> {target}")
        else:
            raise ValueError(f"不支持的 FILEOP 动作: {action}")
        return True

    def run(self, run: Run, job: Job):
        """启动子进程（或执行文件操作），更新 run 状态。在独立线程中调用."""
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

            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                cwd=cwd_env,
                env=env,
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if Executor._is_windows()
                else 0,
            )
            run.pid = proc.pid

            run.detached = detach
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
                timer = threading.Timer(
                    job.timeout_s, self._on_timeout, args=(proc, run)
                )
                timer.daemon = True
                timer.start()

            if proc.stdout:
                for raw in proc.stdout:
                    line = _decode_line(raw)
                    if line:
                        self.runner.on_log(run, line)
            proc.wait()
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

    def abort(self, run: Run) -> bool:
        with self._lock:
            proc = self._procs.get(run.id)
        if proc is None:
            return False
        self._user_stopped.add(run.id)
        self._kill_proc(proc)
        run.status = "KILLED"
        run.error = "用户中止"
        return True

    def _on_timeout(self, proc: subprocess.Popen, run: Run):
        run.status = "TIMEDOUT"
        run.error = f"执行超过 {run.timeout_s} 秒，已中止"
        self._kill_proc(proc)

    def _verify_exit_was_external(self, run: Run):
        try:
            if not run.pid:
                return
            identity_mod.process_is_alive(run, run.pid)
        except Exception:
            pass

    @staticmethod
    def kill_by_pid(pid) -> bool:
        if not pid:
            return False
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return False
        if not Executor._pid_exists(pid):
            return True
        try:
            if os.name == "nt":
                r = subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    timeout=10,
                )
                return not Executor._pid_exists(pid)
            else:
                os.kill(pid, 9)
                return True
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _pid_exists(pid) -> bool:
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return False
        try:
            if os.name == "nt":
                out = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                    capture_output=True,
                    text=True,
                    encoding=_IO_ENCODING,
                    errors="replace",
                    timeout=10,
                )
                return str(pid) in out.stdout
            else:
                os.kill(pid, 0)
                return True
        except ProcessLookupError:
            return False
        except Exception:
            return True

    @staticmethod
    def _kill_proc(proc: subprocess.Popen):
        """尽量连同子进程树一起中止."""
        try:
            if proc.poll() is None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        capture_output=True,
                    )
                else:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def _now_local() -> str:
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec="seconds")