# -*- coding: utf-8 -*-
"""子进程启动、退出码与终止。

从 executor.py 迁出，逻辑逐行保持一致。

依赖注入：_IO_ENCODING（Windows 下 tasklist 输出的解码编码）由 core 传入，
本模块不自行推导，避免与进程启动环境的编码判断产生分歧。
"""
from __future__ import annotations

import os
import subprocess
from typing import Any, List, Optional, Tuple

# Windows tasklist 输出解码编码（由 core 注入实际值）
_io_encoding = "utf-8"


def configure(io_encoding: str) -> None:
    """由 core 在导入后调用，注入平台 IO 编码。"""
    global _io_encoding
    _io_encoding = io_encoding


def spawn(argv: List[str], logf, cwd_env, env, detach: bool,
          detach_variants: List[int]) -> Tuple[subprocess.Popen, bool]:
    """启动子进程，detach 时按候选标志依次重试（权限失败降级）。

    返回 (proc, has_breakaway)。
    stdout 直写日志文件、stderr 合并到 stdout、stdin 置空，
    确保输出脱离 PIPE，任务不依赖调度中心的读取而存活。
    """
    variants = detach_variants if detach else [_spawn_flags_nondetach()]
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
                start_new_session=(not _is_windows()),
            )
            return proc, bool(flags & 0x01000000)
        except (PermissionError, OSError) as e:
            last_err = e
            continue
    raise last_err if last_err else RuntimeError("进程启动失败")


def _is_windows() -> bool:
    return os.name == "nt"


def _spawn_flags_nondetach() -> int:
    if not _is_windows():
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def pid_exists(pid) -> bool:
    """纯 OS 层判断 pid 是否存在（不依赖身份凭据）。"""
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
                encoding=_io_encoding,
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
        return True


def kill_by_pid(pid) -> bool:
    """按 pid 强制终止（Windows 连同子进程树）。"""
    if not pid:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if not pid_exists(pid):
        return True
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=10,
            )
            return not pid_exists(pid)
        else:
            os.kill(pid, 9)
            return True
    except Exception:  # noqa: BLE001
        return False


def kill_proc(proc: subprocess.Popen) -> None:
    """尽量连同子进程树一起中止。"""
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
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
