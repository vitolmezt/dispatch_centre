# -*- coding: utf-8 -*-
"""Windows Job Object 管理。

从 executor.py 迁出，逻辑逐行保持一致。纯 Win32 ctypes 实现。

设计要点（不可更改）：
  _job_attach 建立**每实例命名 Job**，并显式设置 LimitFlags = 0，
  即**不启用 KILL_ON_JOB_CLOSE**。这是「任务不依赖调度中心存活」的
  技术前提——调度中心退出时其 Job 句柄关闭、Job 销毁，但任务进程不受影响。
  命名 Job 无法跨进程重开（已实测确认），因此 Job 句柄仅在调度中心
  存活期间用于精确终止整个进程组。

状态所有权：
  _jobs（run_id → Job 句柄）与 _lock 由 Executor 持有，
  本模块通过参数接收，不自行维护副本。
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from ..models import Run


def is_windows() -> bool:
    return os.name == "nt"


def job_attach(run: Run, pid: int, jobs: Dict[str, int], lock: Any) -> bool:
    """为运行实例建立命名 Job 并把进程收入。失败不影响任务本身。"""
    if not is_windows():
        return False
    try:
        import ctypes
        from ctypes import wintypes

        k = ctypes.windll.kernel32
        k.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                              wintypes.LPVOID, wintypes.DWORD]
        k.SetInformationJobObject.restype = wintypes.BOOL
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k.AssignProcessToJobObject.restype = wintypes.BOOL
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE

        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in run.id)
        name = f"Local\\SCRun_{safe}"
        h = k.CreateJobObjectW(None, name)
        if not h:
            return False

        class _B(ctypes.Structure):
            _fields_ = [("a", ctypes.c_int64), ("b", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("c", ctypes.c_size_t), ("d", ctypes.c_size_t),
                        ("e", wintypes.DWORD), ("f", ctypes.c_size_t),
                        ("g", wintypes.DWORD), ("h", wintypes.DWORD)]

        class _I(ctypes.Structure):
            _fields_ = [("x", ctypes.c_ulonglong)] * 6

        class _E(ctypes.Structure):
            _fields_ = [("Basic", _B), ("Io", _I),
                        ("p1", ctypes.c_size_t), ("p2", ctypes.c_size_t),
                        ("p3", ctypes.c_size_t), ("p4", ctypes.c_size_t)]

        info = _E()
        info.Basic.LimitFlags = 0  # 关键：不启用 KILL_ON_JOB_CLOSE
        k.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info))

        hp = k.OpenProcess(0x1F0FFF, False, int(pid))
        assigned = False
        if hp:
            try:
                assigned = bool(k.AssignProcessToJobObject(
                    wintypes.HANDLE(h), wintypes.HANDLE(hp)))
            finally:
                k.CloseHandle(hp)
        with lock:
            jobs[run.id] = int(h)
        if not assigned:
            job_close(run.id, jobs, lock)
        return assigned
    except Exception:  # noqa: BLE001
        return False


def job_terminate(run_id: str, jobs: Dict[str, int], lock: Any) -> bool:
    """用 Job 句柄终止整组（仅存活期有效）。"""
    if not is_windows():
        return False
    with lock:
        h = jobs.get(run_id)
    if not h:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.windll.kernel32
        k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.TerminateJobObject.restype = wintypes.BOOL
        return bool(k.TerminateJobObject(wintypes.HANDLE(h), 1))
    except Exception:  # noqa: BLE001
        return False


def job_close(run_id: str, jobs: Dict[str, int], lock: Any) -> None:
    """释放 Job 句柄。释放后 Job 若已无其他句柄引用即被销毁。"""
    with lock:
        h = jobs.pop(run_id, None)
    if not h:
        return
    try:
        import ctypes
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(h))
    except Exception:  # noqa: BLE001
        pass


def probe_job_environment() -> Dict[str, Optional[bool]]:
    """探测当前进程所处 Job 的约束情况。

    返回 in_job / kill_on_close / breakaway_allowed / can_escape 四个字段。
    非 Windows 明确判定为无约束。探测失败不影响主流程。
    """
    result: Dict[str, Optional[bool]] = {
        "in_job": None, "kill_on_close": None,
        "breakaway_allowed": None, "can_escape": None,
    }
    if not is_windows():
        result.update(in_job=False, kill_on_close=False,
                      breakaway_allowed=False, can_escape=True)
        return result
    try:
        import ctypes
        from ctypes import wintypes

        k = ctypes.windll.kernel32
        k.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE,
                                     ctypes.POINTER(wintypes.BOOL)]
        k.IsProcessInJob.restype = wintypes.BOOL
        k.GetCurrentProcess.restype = wintypes.HANDLE

        hproc = k.GetCurrentProcess()
        inj = wintypes.BOOL()
        if not k.IsProcessInJob(hproc, None, ctypes.byref(inj)):
            return result
        in_job = bool(inj.value)
        result["in_job"] = in_job
        if not in_job:
            result.update(kill_on_close=False, breakaway_allowed=False,
                          can_escape=True)
            return result

        class _BASIC(ctypes.Structure):
            _fields_ = [("a", ctypes.c_int64), ("b", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("c", ctypes.c_size_t), ("d", ctypes.c_size_t),
                        ("e", wintypes.DWORD), ("f", ctypes.c_size_t),
                        ("g", wintypes.DWORD), ("h", wintypes.DWORD)]

        class _IO(ctypes.Structure):
            _fields_ = [("x", ctypes.c_ulonglong)] * 6

        class _EXT(ctypes.Structure):
            _fields_ = [("Basic", _BASIC), ("Io", _IO),
                        ("p1", ctypes.c_size_t), ("p2", ctypes.c_size_t),
                        ("p3", ctypes.c_size_t), ("p4", ctypes.c_size_t)]

        k.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
            wintypes.DWORD, wintypes.LPVOID]
        k.QueryInformationJobObject.restype = wintypes.BOOL

        info = _EXT()
        if not k.QueryInformationJobObject(None, 9, ctypes.byref(info),
                                           ctypes.sizeof(info), None):
            return result
        flags = info.Basic.LimitFlags
        KILL_ON_JOB_CLOSE = 0x00002000
        BREAKAWAY_OK = 0x00000800
        SILENT_BREAKAWAY_OK = 0x00001000
        kill = bool(flags & KILL_ON_JOB_CLOSE)
        allowed = bool(flags & (BREAKAWAY_OK | SILENT_BREAKAWAY_OK))
        result["kill_on_close"] = kill
        result["breakaway_allowed"] = allowed
        result["can_escape"] = (not kill) or allowed
        return result
    except Exception:  # noqa: BLE001 —— 探测失败不影响主流程
        return result
