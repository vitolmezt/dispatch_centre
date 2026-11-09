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


# ---- 阶段1：安全根目录（FILEOP 与 SCRIPT/PROGRAM 分开约束） ----
#   SC_SAFE_ROOT ：FILEOP 文件操作允许的根目录（可多个，分号/换行分隔）。
#                  未配置 -> FAIL-CLOSED：一切文件操作被拒绝。
#   SC_EXEC_ROOT ：SCRIPT/PROGRAM 可执行文件所在目录（可多个）。
#                  未配置 -> FAIL-CLOSED：SCRIPT/PROGRAM 类型的 path 被拒绝。
# 二者互相独立，避免执行目录覆盖到 .git / server/data / 密钥 / 核心源码等敏感区。
class UnsafePathError(Exception):
    """路径不在安全根目录内时抛出."""


def _parse_roots(var: str) -> List[Path]:
    """从环境变量解析安全根目录列表（分号或换行分隔），解析为绝对路径."""
    raw = os.environ.get(var, "")
    if not raw:
        return []
    roots: List[Path] = []
    for part in raw.replace(";", "\n").replace("\n\n", "\n").split("\n"):
        part = part.strip()
        if not part:
            continue
        try:
            roots.append(Path(part).expanduser().resolve())
        except Exception:  # noqa: BLE001
            continue
    return roots


def _fileop_roots() -> List[Path]:
    """FILEOP 允许的根目录（SC_SAFE_ROOT）."""
    return _parse_roots("SC_SAFE_ROOT")


def _exec_roots() -> List[Path]:
    """SCRIPT/PROGRAM 可执行文件所在根目录（SC_EXEC_ROOT）."""
    # 兼容：若只配了 SC_SAFE_ROOT，未显式配 SC_EXEC_ROOT 时，SCRIPT/PROGRAM 也沿用 FILEOP 根
    # （历史配置不炸），但反向（只配 SC_EXEC_ROOT）不影响 FILEOP 的 fail-closed。
    if os.environ.get("SC_EXEC_ROOT"):
        return _parse_roots("SC_EXEC_ROOT")
    return _parse_roots("SC_SAFE_ROOT")


def _is_within(path: Path, root: Path) -> bool:
    """判断 path 是否严格位于 root 之下（解析符号链接/junction 后判断，防穿越）."""
    try:
        resolved = path.resolve() if path.exists() else Path(os.path.abspath(str(path)))
    except Exception:  # noqa: BLE001
        resolved = None
    if resolved is None:
        # 目标不存在时按词法 + resolve(strict=False) 处理
        resolved = Path(os.path.abspath(str(path)))
    try:
        return resolved == root or root in resolved.parents
    except Exception:  # noqa: BLE001
        return False


def _check_in_roots(p: str, roots: List[Path], label: str) -> Path:
    """通用：校验 p 必须在某根目录内（先词法绝对化，再 resolve 消除 symlink/junction）."""
    if not roots:
        raise UnsafePathError(f"未配置{label}安全根，危险操作已被拒绝: {p}")
    path = Path(p)
    abs_p = Path(os.path.abspath(str(path)))
    if not any(_is_within(abs_p, root) for root in roots):
        raise UnsafePathError(f"{label}路径不在安全根目录内: {p}")
    if path.exists():
        resolved = path.resolve()
        if not any(_is_within(resolved, root) for root in roots):
            raise UnsafePathError(f"{label}路径解析后越出安全根目录: {p}")
        return resolved
    return abs_p


def check_safe_path(p: str) -> Path:
    """校验 FILEOP 路径必须在 SC_SAFE_ROOT 某根目录之内；未配置则 fail-closed 拒绝.

    返回已解析的绝对 Path。越权抛 UnsafePathError.
    """
    return _check_in_roots(p, _fileop_roots(), "FILEOP")


def check_exec_path(p: str) -> Path:
    """校验 SCRIPT/PROGRAM 的可执行文件路径必须在 SC_EXEC_ROOT 某根目录之内."""
    return _check_in_roots(p, _exec_roots(), "可执行文件")


def safe_roots_configured() -> bool:
    """是否配置了任一安全根（用于判定 FILEOP 是否需要校验；未配置则连 FILEOP 都禁止）."""
    return bool(_fileop_roots() or _exec_roots())


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
            check_exec_path(path)  # 阶段1安全：脚本路径须在 SC_EXEC_ROOT 内
            prefix = Executor._script_prefix(path)
            return prefix + [path] + list(job.params.get("args") or [])
        if job.type == "PROGRAM":
            path = job.params.get("path", "")
            check_exec_path(path)  # 阶段1安全：程序路径须在 SC_EXEC_ROOT 内
            return [path] + list(job.params.get("args") or [])
        raise ValueError(f"不依赖 OS 进程的 Job 类型: {job.type}")

    # ---- FILEOP：文件系统操作（Python 原生，不拉进程） ----
    def _run_fileop(self, job: Job, run: Run) -> bool:
        action = str(job.params.get("action", "")).strip().upper()
        source = job.params.get("source", "")
        target = job.params.get("target", "")
        recursive = bool(job.params.get("recursive", False))

        # 阶段1安全：source 必须在安全根内
        src = check_safe_path(source)
        # target 存在时也必须校验（copy/move 的落点）
        if target:
            _ = check_safe_path(target)
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