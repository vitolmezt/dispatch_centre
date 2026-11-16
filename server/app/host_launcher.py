# -*- coding: utf-8 -*-
"""Task Host 启动器。

关键点：Task Host 不能作为 Scheduler 的普通子进程启动（会继承受限 Job），
必须通过计划任务由 Task Scheduler 服务创建，从而脱离当前会话的 Job Object。

本模块提供：
  ensure_host()  确保 Task Host 在运行，返回 (host, port)；必要时用计划任务拉起
  host_request() 向 Task Host 发请求（带 token）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

HERE = Path(__file__).resolve().parent
SERVER_DIR = HERE.parent
DATA_DIR = SERVER_DIR / "data"
STATE_DIR = DATA_DIR / "host"
ENDPOINT_FILE = STATE_DIR / "host_endpoint.json"
TOKEN_FILE = STATE_DIR / "host_token"
HOST_SCRIPT = SERVER_DIR / "task_host.py"

TASK_NAME = "SchedulerCenterTaskHost"
IS_WINDOWS = os.name == "nt"

STATE_DIR.mkdir(parents=True, exist_ok=True)


def _token() -> str:
    if TOKEN_FILE.exists():
        t = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if t:
            return t
    t = os.urandom(24).hex()
    TOKEN_FILE.write_text(t, encoding="utf-8")
    return t


def _read_endpoint() -> Optional[Tuple[str, int]]:
    if not ENDPOINT_FILE.exists():
        return None
    try:
        d = json.loads(ENDPOINT_FILE.read_text(encoding="utf-8"))
        pid = int(d.get("pid") or 0)
        if pid and not _pid_alive(pid):
            return None
        return str(d.get("host") or "127.0.0.1"), int(d["port"])
    except Exception:
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if IS_WINDOWS:
            o = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                               capture_output=True, text=True, errors="replace",
                               timeout=10).stdout
            return f'"{pid}"' in o
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def request(method: str, path: str, body: Optional[dict] = None,
            timeout: float = 15.0) -> dict:
    ep = _read_endpoint()
    if not ep:
        raise RuntimeError("Task Host 未运行")
    host, port = ep
    url = f"http://{host}:{port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, method=method, data=data, headers={
        "X-Host-Token": _token(),
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"error": f"http_{e.code}"}


def _pythonw() -> str:
    """优先用 pythonw.exe 启动宿主。

    计划任务默认在交互式会话中运行，用 python.exe（控制台子系统）会弹出一个
    黑色 cmd 窗口。pythonw.exe 是同一 Python 的无控制台构建，可在后台静默运行。
    找不到时退回 python.exe（仍可工作，只是会出现窗口）。
    """
    py = sys.executable
    p = Path(py)
    if p.suffix.lower() == ".exe":
        cand = p.with_name("pythonw.exe")
        if cand.exists():
            return str(cand)
    return py


def _launch_via_schtasks() -> bool:
    """用计划任务启动 Task Host —— 唯一能脱离宿主受限 Job 的方式。"""
    if not IS_WINDOWS:
        return False
    # 先删旧任务避免残留
    subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
                   capture_output=True)
    py = _pythonw()
    cmd = f'"{py}" "{HOST_SCRIPT}"'
    r = subprocess.run(
        ["schtasks", "/Create", "/TN", TASK_NAME, "/TR", cmd,
         "/SC", "ONCE", "/ST", "23:59", "/F", "/RL", "LIMITED"],
        capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        return False
    r2 = subprocess.run(["schtasks", "/Run", "/TN", TASK_NAME],
                        capture_output=True, text=True, errors="replace")
    return r2.returncode == 0


def ensure_host(timeout: float = 20.0) -> Tuple[str, int]:
    """确保 Task Host 就绪，返回 (host, port)。"""
    ep = _read_endpoint()
    if ep:
        try:
            r = request("GET", "/health", timeout=3)
            if r.get("ok"):
                return ep
        except Exception:
            pass

    # 需要拉起
    if IS_WINDOWS:
        ok = _launch_via_schtasks()
        if not ok:
            raise RuntimeError("无法通过计划任务启动 Task Host（schtasks 失败）")

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.4)
        ep = _read_endpoint()
        if not ep:
            continue
        try:
            r = request("GET", "/health", timeout=2)
            if r.get("ok"):
                return ep
        except Exception:
            continue
    raise RuntimeError("Task Host 启动超时")


def host_alive() -> bool:
    try:
        r = request("GET", "/health", timeout=2)
        return bool(r.get("ok"))
    except Exception:
        return False


if __name__ == "__main__":
    # 手动拉起用于测试
    print(ensure_host())
