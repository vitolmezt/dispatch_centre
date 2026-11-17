# -*- coding: utf-8 -*-
"""任务执行模块。

保留 Executor 作为统一执行入口（门面），内部按职责拆分：

  core.py             统一执行入口、执行主流程（run / _run_local / abort）
  command_parser.py   命令解析：shell 判断、argv 构建、脚本解释器前缀（纯函数）
  process_manager.py  子进程启动、退出码、终止
  win_job.py          Windows Job Object 管理
  file_operations.py  FILEOP 文件操作
  log_monitor.py      日志增量读取与跟随
  timeout.py          超时管理

兼容性：`from app.executor import Executor` 必须继续可用，
由本 __init__.py 统一导出。
"""
from .core import Executor

__all__ = ["Executor"]
