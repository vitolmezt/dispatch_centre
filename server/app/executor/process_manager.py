# -*- coding: utf-8 -*-
"""子进程启动、退出码与终止。"""
from __future__ import annotations

import os
import subprocess
from typing import Any, List, Optional, Tuple

_io_encoding = "utf-8"


def configure(io_encoding: str) -> None:
    global _io_encoding
    _io_encoding = io_encoding


def spawn(argv: List[str], logf, cwd_env, env, detach: bool,
          detach_variants: List[int]) -> Tuple[subprocess.Popen, bool]:
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
