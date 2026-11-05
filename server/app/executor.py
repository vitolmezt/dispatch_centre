"""进程执行器：跨平台 spawn 命令/脚本/程序，流式抓日志，PID 可中止，超时判定.

调度中心不关心 Job 内部做什么，它只负责：
  1. 读取 Job.params（类型专属参数）
  2. 对 COMMAND/SCRIPT/PROGRAM 拼出可执行命令并 spawn
  3. 对 FILEOP 直接执行文件系统操作
  4. 记录 PID、退出码、实时输出；支持中止(kill)与超时
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Dict, List, Optional

from .models import Job, Run


class Executor:
    """负责把 Job 变成真实的 OS 子进程 / 文件操作流程."""

    def __init__(self, runner):
        self.runner = runner  # 回引用 Scheduler，用于上报状态
        self._procs: Dict[str, subprocess.Popen] = {}  # run_id -> Popen
        self._lock = threading.Lock()

    # ---- params 便捷访问 ----
    @staticmethod
    def p(job: Job, key: str, default=None):
        return job.params.get(key, default)

    # ---- 平台差异 ----
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

    # ---- FILEOP：文件系统操作（Python 原生，不拉进程） ----
    @staticmethod
    def _run_fileop(job: Job, run: Run) -> bool:
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
                run.append_log(f"已递归删除目录: {source}")
            else:
                src.unlink()
                run.append_log(f"已删除文件: {source}")
        elif action == "DELETE_DIR":
            if not src.exists():
                raise FileNotFoundError(f"删除目标不存在: {source}")
            if src.is_dir():
                shutil.rmtree(src)
            else:
                src.unlink()
            run.append_log(f"已删除: {source}")
        elif action == "COPY":
            if not target:
                raise ValueError("copy 操作需要 target 参数")
            if src.is_dir():
                shutil.copytree(src, target, dirs_exist_ok=True)
            else:
                Path(target).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
            run.append_log(f"已复制: {source} -> {target}")
        elif action == "MOVE":
            if not target:
                raise ValueError("move 操作需要 target 参数")
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), target)
            run.append_log(f"已移动: {source} -> {target}")
        else:
            raise ValueError(f"不支持的 FILEOP 动作: {action}")
        return True

    # ---- 核心执行 ----
    def run(self, run: Run, job: Job):
        """启动子进程（或执行文件操作），更新 run 状态。在独立线程中调用."""
        run.status = "RUNNING"
        run.start = _now_local()
        run.pid = None
        run.error = ""
        proc: Optional[subprocess.Popen] = None
        timer = None

        try:
            if job.type == "FILEOP":
                # 文件系统操作：同步、无进程
                self._run_fileop(job, run)
                run.exit_code = 0
                run.status = "SUCCEEDED"
                self.runner.on_started(run)
                self.runner.on_finished(run)
                return

            # ---- 进程类类型 ----
            argv = self._build_argv(job)
            env = os.environ.copy()
            if job.params.get("env"):
                env.update({str(k): str(v) for k, v in job.params["env"].items()})
            cwd_env = job.params.get("workdir") or None
            # 后台常驻：进程独立存活，状态保持“进行中”，不启用超时，但照样读日志、可中止。
            detach = bool(job.params.get("detach", False))

            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                cwd=cwd_env,
                env=env,
                text=True,
                # UTF-8 读取子进程输出，非法字节替换为占位符
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if Executor._is_windows()
                else 0,
            )
            run.pid = proc.pid

            with self._lock:
                self._procs[run.id] = proc
            self.runner.on_started(run)

            # 超时定时器（后台常驻进程不上超时，随服务一直跑）
            if not detach and job.timeout_s and job.timeout_s > 0:
                timer = threading.Timer(
                    job.timeout_s, self._on_timeout, args=(proc, run)
                )
                timer.daemon = True
                timer.start()

            # 阻塞读输出（常驻进程也照样抓日志，供执行中心实时查看）
            if proc.stdout:
                for raw in proc.stdout:
                    line = raw.rstrip("\n")
                    if line:
                        run.append_log(line)
                        self.runner.on_log(run, line)
            proc.wait()
            run.exit_code = proc.returncode
            # 中止/超时（KILLED/TIMEDOUT）状态下不再按退出码覆盖状态
            if run.status not in ("KILLED", "TIMEDOUT"):
                run.status = "SUCCEEDED" if proc.returncode == 0 else "FAILED"
                if proc.returncode != 0:
                    run.error = f"退出码 {proc.returncode}"
        except FileNotFoundError as e:
            run.status = "FAILED"
            run.error = f"找不到可执行目标: {e}"
        except Exception as e:  # noqa: BLE001
            run.status = "FAILED"
            run.error = str(e)
        finally:
            if timer:
                timer.cancel()
            with self._lock:
                self._procs.pop(run.id, None)
            run.end = _now_local()
            self.runner.on_finished(run)

    def abort(self, run: Run) -> bool:
        """中止一个运行中的实例。返回是否成功发出中止请求."""
        with self._lock:
            proc = self._procs.get(run.id)
        if proc is None:
            return False
        self._kill_proc(proc)
        run.status = "KILLED"
        run.error = "用户中止"
        return True

    def _on_timeout(self, proc: subprocess.Popen, run: Run):
        run.status = "TIMEDOUT"
        run.error = f"执行超过 {run.timeout_s} 秒，已中止"
        self._kill_proc(proc)

    @staticmethod
    def kill_by_pid(pid) -> bool:
        """按 PID 精准杀死进程（Windows 用 taskkill /F /PID，Unix 用 kill -9）.

        用于终止 detach 方式拉起的独立后台进程（如 dsh web），
        只终止该 PID，不影响其它进程。返回是否发出了终止指令。
        """
        if not pid:
            return False
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return False
        # 目标进程已不存在（可能已被前一次中止/停止清掉）：目标就是"让它停"，
        # 已达成即视为成功，避免前端误报"停止失败"。
        if not Executor._pid_exists(pid):
            return True
        try:
            if os.name == "nt":
                r = subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    timeout=10,
                )
                # taskkill 返回非 0（例如进程刚好退出），只要进程已不存在仍算成功。
                return not Executor._pid_exists(pid)
            else:
                os.kill(pid, 9)
                return True
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _pid_exists(pid) -> bool:
        """判断 PID 对应的进程是否还存在（Windows 用 tasklist，Unix 用 signal 0）."""
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
                    encoding="utf-8",
                    errors="replace",
                    timeout=10,
                )
                return str(pid) in out.stdout
            else:
                os.kill(pid, 0)
                return True
        except ProcessLookupError:
            return False
        except Exception:  # noqa: BLE001
            return True  # 无法确认时保守认为仍存在，交由后续终止逻辑处理

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