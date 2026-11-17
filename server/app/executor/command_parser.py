# -*- coding: utf-8 -*-
"""命令解析：shell 判定、argv 构建、脚本解释器前缀。"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List

from ..models import Job

# 需要 shell 解释的语法特征（引号外的这些字符意味着必须交给 shell）
SHELL_HINTS = ("|", ">", "<", "&", "&&", "||", "%", "`", "$(", ";")

# shell 内建命令：磁盘上没有对应可执行文件，必须交给 shell
SHELL_BUILTINS = {
    "echo", "dir", "cd", "chdir", "copy", "move", "del", "erase", "rd", "rmdir",
    "md", "mkdir", "ren", "rename", "type", "set", "setlocal", "endlocal", "if",
    "for", "goto", "call", "shift", "pause", "cls", "title", "color", "ver",
    "vol", "path", "prompt", "start", "exit", "rem", "pushd", "popd",
    # POSIX
    "export", "unset", "source", ".", "alias", "printf", "test", "true", "false",
    "cd", "pwd", "read", "umask", "wait", "trap", "eval",
}


def is_windows() -> bool:
    return os.name == "nt"


def script_prefix(path: str) -> List[str]:
    """根据脚本扩展名返回合适的解释器命令。"""
    ext = Path(path).suffix.lower()
    if is_windows():
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


def strip_quoted(command: str) -> str:
    """剥离引号包裹的内容，只保留引号外的字符。"""
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


def needs_shell(command: str) -> bool:

    if not command or not command.strip():
        return False
    bare = strip_quoted(command)
    if any(h in bare for h in SHELL_HINTS):
        return True
    first = bare.strip().split()[0].strip('"').strip("'")
    name = first.replace("\\", "/").rsplit("/", 1)[-1].lower()
    for ext in (".exe", ".com", ".bat", ".cmd"):
        if name.endswith(ext):
            name = name[: -len(ext)]
            break
    return name in SHELL_BUILTINS


def split_command(command: str) -> List[str]:
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


def build_argv(job: Job) -> List[str]:
    """根据任务类型构造 spawn 用的 argv。"""
    if job.type == "COMMAND":
        command = job.params.get("command", "")
        if not needs_shell(command):
            parts = split_command(command)
            if parts:
                return parts
        if is_windows():
            return ["cmd", "/c", command]
        return ["/bin/sh", "-c", command]
    if job.type == "SCRIPT":
        path = job.params.get("path", "")
        prefix = script_prefix(path)
        return prefix + [path] + list(job.params.get("args") or [])
    if job.type == "PROGRAM":
        path = job.params.get("path", "")
        return [path] + list(job.params.get("args") or [])
    raise ValueError(f"不依赖 OS 进程的 Job 类型: {job.type}")
