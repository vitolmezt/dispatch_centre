
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from typing import Dict, List, Optional

if os.name == "nt":
    import ctypes
    from ctypes import wintypes
    import ctypes.wintypes as _wt

IS_WINDOWS = os.name == "nt"
IS_LINUX = sys.platform.startswith("linux")


def get_process_create_time(pid: int) -> Optional[str]:
    try:
        pid = int(pid)
        if pid <= 0:
            return None
        if IS_WINDOWS:
            return _win_create_time(pid)
        elif IS_LINUX:
            return _linux_starttime(pid)
    except Exception:  # noqa: BLE001
        return None
    return None


def _win_create_time(pid: int) -> Optional[str]:
    kernel32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return None
    try:
        ctime = ctypes.c_ulonglong(0)
        etime = ctypes.c_ulonglong(0)
        ktime = ctypes.c_ulonglong(0)
        utime = ctypes.c_ulonglong(0)
        ok = kernel32.GetProcessTimes(
            wintypes.HANDLE(h),
            ctypes.byref(ctime), ctypes.byref(etime),
            ctypes.byref(ktime), ctypes.byref(utime),
        )
        if not ok:
            return None
        return f"{ctime.value:019d}"
    finally:
        kernel32.CloseHandle(h)


def _linux_starttime(pid: int) -> Optional[str]:
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            data = f.read().decode("latin-1", "replace")
        close = data.rfind(")")
        if close < 0:
            return None
        fields = data[close + 1:].split()
        if len(fields) < 22:
            return None
        starttime = fields[19]
        try:
            boot_id = open("/proc/sys/kernel/random/boot_id", "r").read().strip()
        except Exception:  # noqa: BLE001
            boot_id = "?"
        return f"{boot_id}:{starttime}"
    except Exception:  # noqa: BLE001
        return None


def get_executable_path(pid: int) -> Optional[str]:
    try:
        pid = int(pid)
        if IS_WINDOWS:
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return None
            try:
                buf = ctypes.create_unicode_buffer(4096)
                size = wintypes.DWORD(4096)
                if kernel32.QueryFullProcessImageNameW(wintypes.HANDLE(h), 0, buf,
                                                       ctypes.byref(size)):
                    return buf.value
            finally:
                kernel32.CloseHandle(h)
        elif IS_LINUX:
            return os.path.realpath(f"/proc/{pid}/exe")
    except Exception:  # noqa: BLE001
        return None
    return None


def command_line_fingerprint(cmdline: Optional[List[str]]) -> Optional[str]:
    if not cmdline:
        return None
    joined = "\x00".join(str(c) for c in cmdline)
    return hashlib.sha256(joined.encode("utf-8", "replace")).hexdigest()[:16]


def read_process_group_id(pid: int) -> Optional[int]:
    try:
        pid = int(pid)
        if IS_WINDOWS:
            return pid
        elif IS_LINUX:
            with open(f"/proc/{pid}/stat", "rb") as f:
                data = f.read().decode("latin-1", "replace")
            close = data.rfind(")")
            fields = data[close + 1:].split()
            if len(fields) >= 3:
                return int(fields[2])
    except Exception:  # noqa: BLE001
        return None
    return None


def capture_identity(pid: int, cmdline: Optional[List[str]] = None,
                      own_exe_hint: Optional[str] = None,
                      process_group_id: Optional[int] = None) -> Dict[str, Optional[str]]:
    d = {
        "pid": int(pid),
        "process_create_time": get_process_create_time(pid),
        "executable_path": get_executable_path(pid) or own_exe_hint,
        "command_line_fingerprint": command_line_fingerprint(cmdline),
        "process_group_id": int(process_group_id) if process_group_id is not None
                            else read_process_group_id(pid),
        "alive": _os_alive_probe(pid,
                                 {"pid": int(pid),
                                  "process_create_time": get_process_create_time(pid)}),
    }
    return d



def _os_alive_probe(pid: int, identity: Dict[str, Optional[object]]) -> bool:
    try:
        pid = int(pid)
        expected_ct = identity.get("process_create_time")
        real_ct = get_process_create_time(pid)
        if not expected_ct or not real_ct or expected_ct != real_ct:
            return False
        if IS_WINDOWS:
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False
            try:
                code = wintypes.DWORD(0)
                if not kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
                    return False
                return code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(h)
        else:
            fd = None
            try:
                if hasattr(os, "pidfd_open"):
                    fd = os.pidfd_open(pid, 0)
                    os.kill(fd, 0)  # 探活：存在即不会抛
                    return True
            except Exception:  # noqa: BLE001
                if fd is not None:
                    try:
                        os.close(fd)
                    except Exception:
                        pass
                fd = None
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False
    except Exception:  # noqa: BLE001
        return False
    return False


def process_is_alive(run, pid: Optional[int] = None) -> bool:
    pid = pid if pid is not None else run.pid
    if not pid:
        return False
    identity = {
        "pid": int(pid),
        "process_create_time": run.process_create_time,
    }
    return _os_alive_probe(pid, identity)



def verify_identity(run, pid: Optional[int] = None) -> bool:
    pid = pid if pid is not None else run.pid
    if not pid:
        return False
    try:
        expected_ct = run.process_create_time
        if not expected_ct:
            return False
        return get_process_create_time(pid) == expected_ct
    except Exception:  # noqa: BLE001
        return False


def safe_kill(run, pid: Optional[int] = None,
              group_id: Optional[int] = None) -> Optional[str]:
    pid = pid if pid is not None else run.pid
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return "无有效 PID，拒绝盲杀"
    if pid <= 0:
        return "无有效 PID，拒绝盲杀"

    if not verify_identity(run, pid):
        return "身份核验失败：PID 或进程创建时刻不匹配，可能 PID 已被复用，拒绝操作"

    if IS_WINDOWS:
        return _win_safe_kill(run, pid, group_id)
    return _posix_safe_kill(run, pid, group_id)


def _win_safe_kill(run, pid: int, group_id: Optional[int]) -> Optional[str]:

    if not verify_identity(run, pid):
        return "身份核验失败：PID 复用或已结束，拒绝任务终止"
    try:
        r = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            return None
        if not process_is_alive(run, pid):
            return None
        return f"taskkill 失败: {(r.stderr or '').strip()[:200]}"
    except Exception as e:  # noqa: BLE001
        if not process_is_alive(run, pid):
            return None
        return f"taskkill 异常: {str(e)[:200]}"


def _posix_safe_kill(run, pid: int, group_id: Optional[int]) -> Optional[str]:
    if not verify_identity(run, pid):
        return "身份核验失败（POSIX），拒绝终止"
    fd = None
    try:
        if hasattr(os, "pidfd_open"):
            fd = os.pidfd_open(pid, 0)
        if fd is not None:
            try:
                os.kill(fd, 15)  # SIGTERM via pidfd（不依赖 PID）
            except Exception:  # noqa: BLE001
                try:
                    os.kill(fd, 9)
                except Exception:  # noqa: BLE001
                    return "pidfd 发送信号失败"
            finally:
                try:
                    os.close(fd)
                except Exception:
                    pass
            return None
    except Exception:  # noqa: BLE001
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
    send_pid = int(group_id) if group_id else pid
    if not verify_identity(run, send_pid):
        send_pid = pid
        if not verify_identity(run, pid):
            return "身份核验失败（POSIX 进程组），拒绝终止"
    try:
        import signal
        os.killpg(send_pid, signal.SIGTERM)
        return None
    except ProcessLookupError:
        return None  # 组已不存在 = 进程已结束
    except PermissionError:
        return "无权限终止该进程组，拒绝继续"
    except Exception as e:  # noqa: BLE001
        return f"killpg 失败: {str(e)[:200]}"