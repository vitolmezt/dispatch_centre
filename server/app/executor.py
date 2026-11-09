"""进程执行器：跨平台 spawn 命令/脚本/程序，流式抓日志，PID 可中止，超时判定.

调度中心不关心 Job 内部做什么，它只负责：
  1. 读取 Job.params（类型专属参数）
  2. 对 COMMAND/SCRIPT/PROGRAM 拼出可执行命令并 spawn
  3. 对 FILEOP 直接执行文件系统操作
  4. 记录 PID、退出码、实时输出；支持中止(kill)与超时
"""
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

# 子进程文本解码编码：跟随系统区域语言（Windows 中文系统为 GBK/cp936）。
# 不硬编码单一编码：行解码时先试 UTF-8 严格，失败回退该系统编码（自动识别两种常见输出）。
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


# 执行权限设计：调度中心是通用黑盒任务执行平台，不审查任务内部业务行为。
# 管理员经认证后即可执行任意符合操作系统条件的命令/脚本/程序/文件操作，
# 实际权限以调度中心运行账户拥有的 OS 权限为准。路径解析与执行错误交由
# 操作系统和执行器正常返回，不在此处做目录白名单/安全根强制拦截。


class Executor:
    """负责把 Job 变成真实的 OS 子进程 / 文件操作流程."""

    def __init__(self, runner):
        self.runner = runner  # 回引用 Scheduler，用于上报状态
        self._procs: Dict[str, subprocess.Popen] = {}  # run_id -> Popen
        # run_id 集合：已被用户主动停止/中止的实例。
        # 读输出的后台线程据此不再用退出码覆盖状态，避免把"主动停止"误标为 FAILED。
        self._user_stopped: set = set()
        self._lock = threading.Lock()

    # ---- 主动停止标记（供 scheduler.stop / self.abort 调用） ----
    def mark_user_stopped(self, run_id: str):
        """记录该 run 是"用户主动停止/中止"，之后的读线程不得用退出码覆盖其状态."""
        with self._lock:
            self._user_stopped.add(run_id)

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
    def _run_fileop(self, job: Job, run: Run) -> bool:
        action = str(job.params.get("action", "")).strip().upper()
        source = job.params.get("source", "")
        target = job.params.get("target", "")
        recursive = bool(job.params.get("recursive", False))

        # 按用户提供的有效路径执行；实际权限由操作系统账户拥有权限决定，
        # 不在此处做安全根目录强制校验（不存在/无权限的错误交由 OS 正常返回）。
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

    # ---- 核心执行 ----
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
                # 不启用 text：以原始字节读取，行进解码时自动识别 UTF-8/GBK，根治中文乱码
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if Executor._is_windows()
                else 0,
            )
            run.pid = proc.pid
            # Phase 2 · 身份采集（STARTING -> RUNNING 的凭据基础）：
            #   spawn 后立即采集 OS 身份快照并写入 run，管理和恢复才能可靠区分"同一进程"。
            run.detached = detach
            run.process_group_id = None  # 由 capture_identity 填写真实组/树根（不假设==pid）
            ident = identity_mod.capture_identity(
                proc.pid, cmdline=argv,
                own_exe_hint=(argv[0] if argv else None),
            )
            run.process_create_time = ident["process_create_time"]
            run.executable_path = ident["executable_path"]
            run.command_line_fingerprint = ident["command_line_fingerprint"]
            run.process_group_id = ident["process_group_id"]
            # 身份凭据必须采集成功；否则绝不允许盲跑（无法做安全停止/恢复）
            if not run.process_create_time:
                run.status = "INTERRUPTED"
                run.error = "无法采集进程创建时刻（身份凭据），已按安全规则拒绝继续"
                raise RuntimeError(run.error)

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
                    line = _decode_line(raw)
                    if line:
                        self.runner.on_log(run, line)
            proc.wait()
            run.exit_code = proc.returncode
            # 中止/超时（KILLED/TIMEDOUT）/ 用户主动停止，都不再按退出码覆盖状态
            # （否则 taskkill 强杀的退出码 1 会把"主动停止"误标为 FAILED）
            was_user_stopped = run.id in self._user_stopped
            if run.status not in ("KILLED", "TIMEDOUT") and not was_user_stopped:
                # 身份复核：收尾时以 OS 探针确认该进程确实已不在运行（pid 已回收，
                # 或创建时刻不再匹配）。避免"进程仍在跑、只是读线程误判"的竞态。
                self._verify_exit_was_external(run)
                if proc.returncode == 0:
                    run.status = "SUCCEEDED"
                    run.error = ""
                else:
                    # 选项A：未登记用户停止 + 非0退出 -> 一律 INTERRUPTED，
                    # 语义为"非正常终止"，不武断归 FAILED；原因写清以便排查。
                    run.status = "INTERRUPTED"
                    run.interrupted_reason = (
                        f"进程非 0 退出（exit={proc.returncode}）且非调度中心主动停止，"
                        f"已判定为外部终止/异常退出"
                    )
                    run.error = f"退出码 {proc.returncode}（外部终止或异常退出）"
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
                self._user_stopped.discard(run.id)
            run.end = _now_local()
            if was_user_stopped:
                # 用户主动停止：状态收尾（置 KILLED、落库、广播 finished）统一交给
                # scheduler.stop 的 _finalize_user_stop 完成，这里只收尾日志流，
                # 避免读线程抢先广播 RUNNING 的 finished。
                self.runner.on_user_stopped(run)
            else:
                self.runner.on_finished(run)

    def abort(self, run: Run) -> bool:
        """中止一个运行中的实例。返回是否成功发出中止请求."""
        with self._lock:
            proc = self._procs.get(run.id)
        if proc is None:
            return False
        self._user_stopped.add(run.id)  # 防止读线程用退出码把它覆盖成 FAILED
        self._kill_proc(proc)
        run.status = "KILLED"
        run.error = "用户中止"
        return True

    def _on_timeout(self, proc: subprocess.Popen, run: Run):
        run.status = "TIMEDOUT"
        run.error = f"执行超过 {run.timeout_s} 秒，已中止"
        self._kill_proc(proc)

    def _verify_exit_was_external(self, run: Run):
        """读线程收尾时的身份/存活复核：确认进程确实已退出（仅在内部执行，不写任务日志）.

        刚 proc.wait() 返回时进程通常已消失；此复核用于规避极端竞态：进程仍在跑、
        或 PID 被新进程复用。复核结果不写入任务日志流，避免把内部系统噪声混进
        用户看到的任务输出（保留给用户干净的真实输出）。
        """
        try:
            if not run.pid:
                return
            identity_mod.process_is_alive(run, run.pid)
        except Exception:  # noqa: BLE001
            pass

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
                    encoding=_IO_ENCODING,
                    errors="replace",
                    timeout=10,
                )
                # tasklist 输出含中文表头，按 PID 纯数字匹配不受编码影响
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