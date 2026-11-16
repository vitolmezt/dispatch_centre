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

from .models import Job, Run
from . import identity as identity_mod

_IO_ENCODING = locale.getpreferredencoding(False) or "utf-8"


class Executor:
    """负责把 Job 变成真实的 OS 子进程 / 文件操作流程."""

    def __init__(self, runner):
        self.runner = runner  # 回引用 Scheduler，用于上报状态
        self._procs: Dict[str, subprocess.Popen] = {}  # run_id -> Popen
        self._user_stopped: set = set()
        self._lock = threading.Lock()
        # 是否走独立 Task Host。单元测试可显式关闭，避免依赖外部宿主进程。
        self._host_enabled = Executor.use_host()

    def mark_user_stopped(self, run_id: str):
        """记录该 run 是"用户主动停止/中止"，之后的读线程不得用退出码覆盖其状态."""
        with self._lock:
            self._user_stopped.add(run_id)

    @staticmethod
    def p(job: Job, key: str, default=None):
        return job.params.get(key, default)

    @staticmethod
    def _detach_flags_variants() -> List[int]:

        base = (getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
        return [base | 0x01000000, base]  # 先带 CREATE_BREAKAWAY_FROM_JOB

    # 父 Job 相关常量
    _KILL_ON_JOB_CLOSE = 0x00002000
    _BREAKAWAY_OK = 0x00000800
    _SILENT_BREAKAWAY_OK = 0x00001000

    @staticmethod
    def probe_job_environment() -> Dict[str, Optional[bool]]:
        result: Dict[str, Optional[bool]] = {
            "in_job": None, "kill_on_close": None,
            "breakaway_allowed": None, "can_escape": None,
        }
        if not Executor._is_windows():
            result.update(in_job=False, kill_on_close=False,
                          breakaway_allowed=False, can_escape=True)
            return result
        try:
            import ctypes
            from ctypes import wintypes

            k = ctypes.windll.kernel32
            k.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE,
                                         ctypes.POINTER(wintypes.BOOL)]
            k.IsProcessInJob.restype = wintypes.BOOL
            k.GetCurrentProcess.restype = wintypes.HANDLE

            hproc = k.GetCurrentProcess()
            inj = wintypes.BOOL()
            if not k.IsProcessInJob(hproc, None, ctypes.byref(inj)):
                return result
            in_job = bool(inj.value)
            result["in_job"] = in_job
            if not in_job:
                result.update(kill_on_close=False, breakaway_allowed=False,
                              can_escape=True)
                return result

            # 读取所属 Job 的 LimitFlags
            class _BASIC(ctypes.Structure):
                _fields_ = [("a", ctypes.c_int64), ("b", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD),
                            ("c", ctypes.c_size_t), ("d", ctypes.c_size_t),
                            ("e", wintypes.DWORD), ("f", ctypes.c_size_t),
                            ("g", wintypes.DWORD), ("h", wintypes.DWORD)]

            class _IO(ctypes.Structure):
                _fields_ = [("x", ctypes.c_ulonglong)] * 6

            class _EXT(ctypes.Structure):
                _fields_ = [("Basic", _BASIC), ("Io", _IO),
                            ("p1", ctypes.c_size_t), ("p2", ctypes.c_size_t),
                            ("p3", ctypes.c_size_t), ("p4", ctypes.c_size_t)]

            k.QueryInformationJobObject.argtypes = [
                wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                wintypes.DWORD, wintypes.LPVOID]
            k.QueryInformationJobObject.restype = wintypes.BOOL

            info = _EXT()
            if not k.QueryInformationJobObject(None, 9, ctypes.byref(info),
                                               ctypes.sizeof(info), None):
                return result
            flags = info.Basic.LimitFlags
            kill = bool(flags & Executor._KILL_ON_JOB_CLOSE)
            allowed = bool(flags & (Executor._BREAKAWAY_OK |
                                    Executor._SILENT_BREAKAWAY_OK))
            result["kill_on_close"] = kill
            result["breakaway_allowed"] = allowed
            result["can_escape"] = (not kill) or allowed
            return result
        except Exception:  # noqa: BLE001 —— 探测失败不影响主流程
            return result

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
        variants = Executor._detach_flags_variants() if detach else [Executor._spawn_flags(False)]
        last_err: Optional[Exception] = None
        for flags in variants:
            try:
                proc = subprocess.Popen(
                    argv,
                    stdout=logf,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    cwd=cwd_env,
                    env=env,
                    bufsize=0,
                    creationflags=flags,
                    close_fds=True,
                    start_new_session=(not Executor._is_windows()),
                )
                return proc, bool(flags & 0x01000000)
            except (PermissionError, OSError) as e:
                last_err = e
                continue
        raise last_err if last_err else RuntimeError("进程启动失败")

    @staticmethod
    def _warn_isolation(envprobe: Dict[str, Optional[bool]]):
        """把 Job 隔离缺失告警打到日志（每次启动只提示一次足够）。"""
        import logging
        logging.getLogger("scheduler").warning(
            "detach 任务未能获得 Job 级隔离：in_job=%s kill_on_job_close=%s "
            "breakaway_allowed=%s -> 调度中心被强制结束时该任务可能被连带终止",
            envprobe.get("in_job"), envprobe.get("kill_on_close"),
            envprobe.get("breakaway_allowed"))

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

    _SHELL_HINTS = ("|", ">", "<", "&", "&&", "||", "%", "`", "$(", ";")

    _SHELL_BUILTINS = {
        "echo", "dir", "cd", "chdir", "copy", "move", "del", "erase", "rd", "rmdir",
        "md", "mkdir", "ren", "rename", "type", "set", "setlocal", "endlocal", "if",
        "for", "goto", "call", "shift", "pause", "cls", "title", "color", "ver",
        "vol", "path", "prompt", "start", "exit", "rem", "pushd", "popd",
        # POSIX
        "export", "unset", "source", ".", "alias", "printf", "test", "true", "false",
        "cd", "pwd", "read", "umask", "wait", "trap", "eval",
    }

    @staticmethod
    def _strip_quoted(command: str) -> str:
        out = []
        i = 0
        n = len(command)
        while i < n:
            ch = command[i]
            if ch == '"':
                i += 1
                while i < n and command[i] != '"':
                    i += 1
                i += 1
            elif ch == "'":
                i += 1
                while i < n and command[i] != "'":
                    i += 1
                i += 1
            else:
                out.append(ch)
                i += 1
        return "".join(out)

    @staticmethod
    def _needs_shell(command: str) -> bool:
        if not command or not command.strip():
            return False
        bare = Executor._strip_quoted(command)
        if any(h in bare for h in Executor._SHELL_HINTS):
            return True
        first = bare.strip().split()[0].strip('"').strip("'")
        name = first.replace("\\", "/").rsplit("/", 1)[-1].lower()
        for ext in (".exe", ".com", ".bat", ".cmd"):
            if name.endswith(ext):
                name = name[: -len(ext)]
                break
        return name in Executor._SHELL_BUILTINS

    @staticmethod
    def _split_command(command: str) -> List[str]:
        """把 'exe arg1 "arg 2"' 拆成 argv，不做 shell 解释。"""
        import shlex
        try:
            parts = shlex.split(command, posix=False)
        except ValueError:
            return []
        cleaned = []
        for p in parts:
            if len(p) >= 2 and p[0] == '"' and p[-1] == '"':
                p = p[1:-1]
            cleaned.append(p)
        return cleaned

    @staticmethod
    def _build_argv(job: Job) -> List[str]:
        if job.type == "COMMAND":
            command = job.params.get("command", "")
            if not Executor._needs_shell(command):
                parts = Executor._split_command(command)
                if parts:
                    return parts
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
        if job.type != "FILEOP" and self._host_enabled:
            return self._run_via_host(run, job)
        return self._run_local(run, job)

    @staticmethod
    def use_host() -> bool:
        """是否启用独立 Task Host 执行任务。

        默认启用；设置 SC_USE_TASK_HOST=0 可回退到进程内 Popen 方式（便于排障/对照）。
        """
        v = os.environ.get("SC_USE_TASK_HOST", "1").strip().lower()
        return v not in ("0", "false", "no", "off")

    def _run_via_host(self, run: Run, job: Job):
        """通过独立 Task Host 启动并托管任务。"""
        from . import host_launcher as HL

        argv = self._build_argv(job)
        log_path = self._resolve_log_path(run)
        env = dict(job.params.get("env") or {})
        cwd = job.params.get("workdir") or None

        # 与进程内路径保持一致：先记录开始时间，否则 UI 的「开始/耗时」为空
        run.status = "RUNNING"
        run.start = _now_local()
        run.pid = None
        run.error = ""

        try:
            HL.ensure_host()
            resp = HL.request("POST", "/start", {
                "run_id": run.id,
                "argv": argv,
                "log_path": log_path,
                "env": env,
                "cwd": cwd,
            }, timeout=30)
        except Exception as e:  # noqa: BLE001
            run.status = "FAILED"
            run.error = f"Task Host 不可用: {e}"
            self.runner.on_finished(run)
            return

        if not resp.get("ok"):
            run.status = "FAILED"
            run.error = f"Task Host 启动失败: {resp.get('error') or resp}"
            self.runner.on_finished(run)
            return

        run.pid = resp.get("pid")
        run.detached = bool(job.params.get("detach", False))
        run.via_shell = bool(resp.get("via_shell"))
        run.process_create_time = resp.get("process_create_time")
        run.executable_path = resp.get("executable_path") or (
            str(argv[0]) if argv else None)
        run.log_path = resp.get("log_path") or log_path
        run.process_group_id = run.pid
        # 由 Task Host 托管，Job 隔离由宿主保证，不再有隔离告警
        run.isolation_warning = ""

        if not run.process_create_time:
            run.status = "INTERRUPTED"
            run.error = "Task Host 未返回进程身份凭据，拒绝托管"
            self.runner.on_finished(run)
            return

        self.runner.on_started(run)

        # 本地只做日志跟随与状态收尾；进程本身由 Task Host 管理
        self._tail_via_host(run, log_path, HL)

    def _tail_via_host(self, run: Run, log_path: str, HL):
        """跟随日志并轮询 Task Host 状态，直到任务结束或被停止。"""
        pos = int(getattr(run, "log_offset", 0) or 0)
        try:
            if os.path.isfile(log_path) and os.path.getsize(log_path) < pos:
                pos = 0
        except OSError:
            pos = 0

        while True:
            if run.id in self._user_stopped:
                break
            pos = self._pump_log(run, log_path, pos)
            try:
                st = HL.request("GET", f"/status?run_id={run.id}", timeout=10)
            except Exception:  # noqa: BLE001
                # Host 暂时不可达：不判定任务死亡，稍后重试
                time.sleep(1.0)
                continue
            if st.get("error") == "run_not_found":
                break
            if not st.get("alive"):
                self._pump_log(run, log_path, pos)
                rc = st.get("exit_code")
                run.exit_code = rc if isinstance(rc, int) else None
                if run.id in self._user_stopped:
                    break
                if run.status in ("KILLED", "TIMEDOUT"):
                    break
                if run.exit_code == 0:
                    run.status = "SUCCEEDED"
                    run.error = ""
                else:
                    run.status = "INTERRUPTED"
                    if run.exit_code is None:
                        run.interrupted_reason = (
                            "任务已结束但无可靠退出码凭证，标记为中断")
                        run.error = "退出码未知（无可靠凭证）"
                    else:
                        run.interrupted_reason = (
                            f"进程非 0 退出（exit={run.exit_code}），已判定为异常退出")
                        run.error = f"退出码 {run.exit_code}（异常退出）"
                break
            time.sleep(1.0)

        run.end = _now_local()
        if run.id in self._user_stopped:
            self.runner.on_user_stopped(run)
        else:
            self.runner.on_finished(run)

    def _run_local(self, run: Run, job: Job):
        """原有的进程内执行方式（FILEOP 或禁用 Host 时使用）。"""
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
                        "该任务可能被连带终止。建议改用非 Job 约束方式启动调度中心。")
                    self._warn_isolation(envprobe)

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
                timer = threading.Timer(
                    job.timeout_s, self._on_timeout, args=(proc, run)
                )
                timer.daemon = True
                timer.start()

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
        pos = int(getattr(run, "log_offset", 0) or 0)
        try:
            if os.path.isfile(log_path) and os.path.getsize(log_path) < pos:
                pos = 0  # 文件被截断/轮转
        except OSError:
            pos = 0
        while True:
            if run.id in self._user_stopped:
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._kill_proc(proc)
                return
            rc = proc.poll()
            pos = self._pump_log(run, log_path, pos)
            if rc is not None:
                # 收尾：把最后写进日志的内容补齐
                self._pump_log(run, log_path, pos)
                return
            time.sleep(0.5)

    def _pump_log(self, run: Run, log_path: str, pos: int) -> int:
        """从 pos 起读取日志文件新增内容，按行回调 on_log；返回新位置。"""
        try:
            if not os.path.isfile(log_path):
                return pos
            size = os.path.getsize(log_path)
            if size < pos:
                pos = 0  # 文件被截断/重建
            if size == pos:
                return pos
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(pos)
                chunk = f.read()
                new_pos = f.tell()
        except OSError:
            return pos
        for line in chunk.splitlines():
            if line:
                self._emit_log(run, line)
        run.log_offset = new_pos
        return new_pos

    def _emit_log(self, run: Run, line: str):
        try:
            self.runner.on_log(run, line, write_file=False)
        except TypeError:
            self.runner.on_log(run, line)

    def abort(self, run: Run) -> bool:
        with self._lock:
            proc = self._procs.get(run.id)
        if proc is not None:
            self._user_stopped.add(run.id)
            self._kill_proc(proc)
            run.status = "KILLED"
            run.error = "用户中止"
            return True

        # Host 托管的任务没有本地 Popen 句柄，改为请求 Task Host 停止
        if self._host_enabled:
            from . import host_launcher as HL
            self._user_stopped.add(run.id)
            try:
                resp = HL.request("POST", "/stop", {"run_id": run.id}, timeout=30)
            except Exception as e:  # noqa: BLE001
                run.error = f"停止失败（Task Host 不可达）: {e}"
                return False
            if resp.get("stop"):
                run.status = "KILLED"
                run.error = "用户中止"
                return True
            run.error = resp.get("reason") or "停止失败"
            return False
        return False

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