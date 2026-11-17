# -*- coding: utf-8 -*-
"""超时管理。

从 executor.py 迁出，逻辑逐行保持一致。

仅对非 detach 任务生效：detach 任务随服务常驻，不设超时。
超时触发时把状态置为 TIMEDOUT 并终止进程树。
"""
from __future__ import annotations

import subprocess
import threading
from typing import Callable

from ..models import Run


def on_timeout(proc: subprocess.Popen, run: Run,
               kill_proc: Callable[[subprocess.Popen], None]) -> None:
    """超时回调：标记 TIMEDOUT 并终止进程。"""
    run.status = "TIMEDOUT"
    run.error = f"执行超过 {run.timeout_s} 秒，已由调度中心终止"
    kill_proc(proc)


def start_timer(timeout_s: int, proc: subprocess.Popen, run: Run,
                kill_proc: Callable[[subprocess.Popen], None]) -> threading.Timer:
    """为非 detach 任务启动超时定时器（daemon 线程，不阻塞退出）。"""
    timer = threading.Timer(timeout_s, on_timeout, args=(proc, run, kill_proc))
    timer.daemon = True
    timer.start()
    return timer
