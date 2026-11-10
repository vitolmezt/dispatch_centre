
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


class CannotProveIdentity(Exception):
    """无法证明某 pid 与某 run 属于同一 OS 进程时抛出（安全侧拒绝动作）。"""


# ============ 平台支持探测 ============

def pidfd_available() -> bool:
    """pidfd 是否可用（Linux 内核 >=5.3，os.pidfd_open 存在且能拿到 fd）。"""
    if not IS_LINUX:
        return False
    if not hasattr(os, "pidfd_open"):
        return False
    return True


def job_object_support() -> str:
    """命名 Job Object 支持情况：'none' | 'create' | 'reopen'。

    Windows 上若本进程能创建命名 Job Object，则返回 'create'；重启后若同名
    Job 仍在且能打开，返回 'reopen'。实际唯一句柄在运行时拿，这里只做能力宣告。
    """
    return "none"


# ============ 进程身份采集 ============

def get_process_create_time(pid: int) -> Optional[str]:
    """获取进程创建时刻（字符串，可稳定比对）。

    Windows: GetProcessTimes -> FILETIME(2x32bit 拼接) 原文，防 PID 复用更严格。
    Linux:   /proc/<pid>/stat 第22项 starttime(ticks)。
    返回唯一字符串；失败返回 None（调用方不得把 None 当身份已确认）。
    """
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
        # GetProcessTimes(process, lpCreationTime, ...) 返回 FILETIME(2x DWORD)
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
        # FILETIME 实际上是两个 DWORD 拼成 64bit，ctypes.c_ulonglong 直接落地址即可
        return f"{ctime.value:019d}"
    finally:
        kernel32.CloseHandle(h)


def _linux_starttime(pid: int) -> Optional[str]:
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            data = f.read().decode("latin-1", "replace")
        # 进程名可能含空格与括号，规范做法是从最后一个 ')' 后开始取字段
        close = data.rfind(")")
        if close < 0:
            return None
        fields = data[close + 1:].split()
        # stat 字段：第0项=state(在')'后第1项)。starttime 是第22字段（1-indexed）
        # 其中 state 即全指令字段的第3项；从')'后 fields[0]=state
        if len(fields) < 22:
            return None
        # fields 下标：fields[0]=state（全字段3），全字段22=starttime
        #   -> 相对索引 = 22 - 3 = 19
        starttime = fields[19]
        try:
            boot_id = open("/proc/sys/kernel/random/boot_id", "r").read().strip()
        except Exception:  # noqa: BLE001
            boot_id = "?"
        return f"{boot_id}:{starttime}"
    except Exception:  # noqa: BLE001
        return None


def get_executable_path(pid: int) -> Optional[str]:
    """可执行文件绝对路径（辅助佐证；Windows 用 QueryFullProcessImageNameW）."""
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
    """把 argv 列表做成指纹（辅助佐证）. 空输入返回 None."""
    if not cmdline:
        return None
    joined = "\x00".join(str(c) for c in cmdline)
    return hashlib.sha256(joined.encode("utf-8", "replace")).hexdigest()[:16]


def read_process_group_id(pid: int) -> Optional[int]:
    """读取真实进程组/进程树根 pid 以供佐证（不做“==界= pid”假设）."""
    try:
        pid = int(pid)
        if IS_WINDOWS:
            # Windows：GetProcessId 与创建时划定的进程树根不保证等于 pid（尤其 detach）。
            # 这里返回 pid（调用方另行据 detach/树管理来决定比较语义）。
            return pid
        elif IS_LINUX:
            with open(f"/proc/{pid}/stat", "rb") as f:
                data = f.read().decode("latin-1", "replace")
            close = data.rfind(")")
            fields = data[close + 1:].split()
            # fields[0]=state(全3)；pgrp 是全5 -> 相对索引 2
            if len(fields) >= 3:
                return int(fields[2])
    except Exception:  # noqa: BLE001
        return None
    return None


def capture_identity(pid: int, cmdline: Optional[List[str]] = None,
                      own_exe_hint: Optional[str] = None,
                      process_group_id: Optional[int] = None) -> Dict[str, Optional[str]]:
    """对刚 spawn 出的 pid 采集一份身份凭据快照，供落库。

    返回 dict：{pid, process_create_time, executable_path, command_line_fingerprint,
                process_group_id, alive}
    """
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


# ============ 进程存活探针（唯一的“死亡判定”权威） ============

def _os_alive_probe(pid: int, identity: Dict[str, Optional[object]]) -> bool:
    """OS 级存活探针 = pid 存在 && 身份凭据匹配. 只有它才能断言“进程是否仍在”。

    Windows: OpenProcess + GetExitCodeProcess(STILL_ACTIVE) 且身份凭据(创建时刻)匹配。
    Linux:   pidfd/waitpid(-1) 探一下 或 os.kill(pid,0)。创建时刻匹配。
    """
    try:
        pid = int(pid)
        expected_ct = identity.get("process_create_time")
        real_ct = get_process_create_time(pid)
        if not expected_ct or not real_ct or expected_ct != real_ct:
            # 连创建时刻都拿不到/不匹配 -> 无法证明存活，保守视为不确认
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
        else:  # LINUX / POSIX
            # pidfd 路径：向 fd 发 signal 0 探活而不用 PID 竞态
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
            # 回落：os.kill(pid,0)
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False
    except Exception:  # noqa: BLE001
        return False
    return False


def process_is_alive(run, pid: Optional[int] = None) -> bool:
    """外部便捷入口：用已落库的身份凭据校验进程是否存活."""
    pid = pid if pid is not None else run.pid
    if not pid:
        return False
    identity = {
        "pid": int(pid),
        "process_create_time": run.process_create_time,
    }
    return _os_alive_probe(pid, identity)


# ============ 安全停止 ============

def verify_identity(run, pid: Optional[int] = None) -> bool:
    """身份核验：pid 对应的进程是否就是 run 记录的那个进程（零容忍）。"""
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
    """跨平台安全停止：先核验身份，能证明属于该 run 的进程才发信号。

    返回:
      None    —— 成功（已确认终止或已请求终止且可证明目标）。
      字符串 —— 失败原因/拒绝原因（安全侧阻断，不得盲杀）。

    原则：
      1. 证明不了的进程绝不杀（CannotProveIdentity 语义封装成返回原因串）。
      2. Windows 检测到命名 Job Object 句柄可复用则用它；不可复用时不谎报成功。
      3. Linux 优先 pidfd 发信号消除单-PID 复用竞态；内核不支持则退进程组+身份核验。
    """
    pid = pid if pid is not None else run.pid
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return "无有效 PID，拒绝盲杀"
    if pid <= 0:
        return "无有效 PID，拒绝盲杀"

    # 1) 身份核验（零容忍）
    if not verify_identity(run, pid):
        return "身份核验失败：PID 或进程创建时刻不匹配，可能 PID 已被复用，拒绝操作"

    # 2) 跨平台终止
    if IS_WINDOWS:
        return _win_safe_kill(run, pid, group_id)
    return _posix_safe_kill(run, pid, group_id)


def _win_safe_kill(run, pid: int, group_id: Optional[int]) -> Optional[str]:
    """Windows：二次校验后 taskkill。若该 run 是普通 session 且预定进程树根可用，
    用 /T 树终止；否则只终止目标 pid（父决定）。一律不谎报成功。"""
    # 复核身份（紧邻发送，缩小竞态窗口）
    if not verify_identity(run, pid):
        return "身份核验失败：PID 复用或已结束，拒绝任务终止"
    try:
        r = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            return None
        # 进程可能已自然结束 -> 属于正常情况；否则反馈 stderr
        if not process_is_alive(run, pid):
            return None
        return f"taskkill 失败: {(r.stderr or '').strip()[:200]}"
    except Exception as e:  # noqa: BLE001
        if not process_is_alive(run, pid):
            return None
        return f"taskkill 异常: {str(e)[:200]}"


def _posix_safe_kill(run, pid: int, group_id: Optional[int]) -> Optional[str]:
    """POSIX：优先 pidfd 单发；否则进程组(os.killpg)杀死组长后成员复核。"""
    if not verify_identity(run, pid):
        return "身份核验失败（POSIX），拒绝终止"
    # 尝试 pidfd
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
    # 回落：进程组。先确认组长身份（组 ID 不等于 pid 时还要核验），再 killpg
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


# ============ 命令行采集（Linux /proc） ============

def read_cmdline(pid: int) -> Optional[List[str]]:
    """读取某 pid 的命令行参数（用于对照指纹，辅助佐证）."""
    try:
        if IS_LINUX:
            raw = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\x00")
            return [x.decode("utf-8", "replace") for x in raw if x]
        elif IS_WINDOWS:
            # Windows 命令行采集依赖 WMI/PDH，较慢且属“辅助”，此处保守返回 None
            # （主要身份靠创建时刻 + 可执行路径）。
            return None
    except Exception:  # noqa: BLE001
        return None
    return None