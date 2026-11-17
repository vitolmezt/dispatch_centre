# -*- coding: utf-8 -*-
"""FILEOP 文件操作实现。"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..models import Job, Run


def run_fileop(job: Job, run: Run, runner: Any) -> bool:
    
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
            runner.on_log(run, f"已递归删除目录: {source}")
        else:
            src.unlink()
            runner.on_log(run, f"已删除文件: {source}")
    elif action == "DELETE_DIR":
        if not src.exists():
            raise FileNotFoundError(f"删除目标不存在: {source}")
        if src.is_dir():
            shutil.rmtree(src)
        else:
            src.unlink()
        runner.on_log(run, f"已删除: {source}")
    elif action == "COPY":
        if not target:
            raise ValueError("copy 操作需要 target 参数")
        if src.is_dir():
            shutil.copytree(src, target, dirs_exist_ok=True)
        else:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
        runner.on_log(run, f"已复制: {source} -> {target}")
    elif action == "MOVE":
        if not target:
            raise ValueError("move 操作需要 target 参数")
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), target)
        runner.on_log(run, f"已移动: {source} -> {target}")
    else:
        raise ValueError(f"不支持的 FILEOP 动作: {action}")
    return True
