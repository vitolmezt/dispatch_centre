# -*- coding: utf-8 -*-
"""日志增量读取与跟随。"""
from __future__ import annotations

import os
import subprocess
import time
from typing import Any, Callable

from ..models import Run


def emit_log(runner: Any, run: Run, line: str) -> None:
    try:
        runner.on_log(run, line, write_file=False)
    except TypeError:
        runner.on_log(run, line)


def pump_log(runner: Any, run: Run, log_path: str, pos: int) -> int:
    try:
        if not os.path.isfile(log_path):
            return pos
        size = os.path.getsize(log_path)
        if size < pos:
            pos = 0
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
            emit_log(runner, run, line)
    run.log_offset = new_pos
    return new_pos


def tail_detached(runner: Any, run: Run, log_path: str,
                  proc: subprocess.Popen,
                  user_stopped: set,
                  kill_proc: Callable[[subprocess.Popen], None]) -> None:
    pos = int(getattr(run, "log_offset", 0) or 0)
    try:
        if os.path.isfile(log_path) and os.path.getsize(log_path) < pos:
            pos = 0
    except OSError:
        pos = 0
    while True:
        if run.id in user_stopped:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                kill_proc(proc)
            return
        rc = proc.poll()
        pos = pump_log(runner, run, log_path, pos)
        if rc is not None:
            pump_log(runner, run, log_path, pos)
            return
        time.sleep(0.5)
