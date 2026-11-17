# -*- coding: utf-8 -*-
"""日志增量读取与跟随。

从 executor.py 迁出，逻辑逐行保持一致。

三个函数的协作关系：
  tail_detached  跟随进程输出直到退出（期间按 0.5s 轮询）
    → pump_log   从 offset 起增量读取，文件被截断则归零
      → emit_log 按行回调 runner.on_log

依赖由调用方以参数注入（runner / user_stopped / kill_proc），
模块自身不持有任何状态。
"""
from __future__ import annotations

import os
import subprocess
import time
from typing import Any, Callable

from ..models import Run


def emit_log(runner: Any, run: Run, line: str) -> None:
    """把一行日志回传给 runner，避免再写回同一文件造成自循环。"""
    try:
        runner.on_log(run, line, write_file=False)
    except TypeError:
        runner.on_log(run, line)


def pump_log(runner: Any, run: Run, log_path: str, pos: int) -> int:
    """从 pos 起读取日志文件新增内容，按行回调；返回新位置。"""
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
            emit_log(runner, run, line)
    run.log_offset = new_pos
    return new_pos


def tail_detached(runner: Any, run: Run, log_path: str,
                  proc: subprocess.Popen,
                  user_stopped: set,
                  kill_proc: Callable[[subprocess.Popen], None]) -> None:
    """跟随进程输出直到进程退出或被标记为用户停止。

    user_stopped 是 Executor 持有的 run_id 集合（同一引用，非副本）；
    kill_proc 用于用户停止时兜底终止进程。
    """
    pos = int(getattr(run, "log_offset", 0) or 0)
    try:
        if os.path.isfile(log_path) and os.path.getsize(log_path) < pos:
            pos = 0  # 文件被截断/轮转
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
            # 收尾：把最后写进日志的内容补齐
            pump_log(runner, run, log_path, pos)
            return
        time.sleep(0.5)
