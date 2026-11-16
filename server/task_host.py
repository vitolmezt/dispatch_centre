# -*- coding: utf-8 -*-
"""独立任务宿主（Task Host）。

设计目标：真实任务进程由本宿主启动并管理，宿主自身通过「计划任务」方式启动，
因此不属于 Scheduler / FastAPI / 终端 / VS Code 的任何受限 Job Object。
Scheduler 关闭、终端关闭、VS Code 关闭都不会影响本宿主和它管理的任务。

与 Scheduler 的通信：仅绑定 127.0.0.1 的 HTTP + 共享 token 认证。

职责：
  1. 接收启动请求，启动真实任务进程
  2. 记录并持久化 run_id -> (pid, create_time, exe, 指纹) 映射
  3. stdout/stderr 直写日志文件
  4. 监控进程退出，记录退出码
  5. 支持停止（按身份核验后 taskkill /T）
  6. 支持状态查询与接管查询
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse, parse_qs

HERE = Path(__file__).resolve().parent          # server/
SERVER_DIR = HERE
DATA_DIR = SERVER_DIR / "data"
STATE_DIR = DATA_DIR / "host"
LOGS_DIR = DATA_DIR / "logs"
STATE_FILE = STATE_DIR / "instances.json"
TOKEN_FILE = STATE_DIR / "host_token"

STATE_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

IS_WINDOWS = os.name == "nt"
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000

_lock = threading.RLock()
_instances: Dict[str, dict] = {}
_procs: Dict[str, subprocess.Popen] = {}


# ---------------- 身份工具 ----------------

def _win_create_time(pid: int) -> Optional[str]:
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))
        if not h:
            return None
        try:
            c = ctypes.c_ulonglong(0)
            e = ctypes.c_ulonglong(0)
            kt = ctypes.c_ulonglong(0)
            u = ctypes.c_ulonglong(0)
            if not k.GetProcessTimes(wintypes.HANDLE(h), ctypes.byref(c),
                                     ctypes.byref(e), ctypes.byref(kt),
                                     ctypes.byref(u)):
                return None
            return f"{c.value:019d}"
        finally:
            k.CloseHandle(h)
    except Exception:
        return None


def process_create_time(pid: int) -> Optional[str]:
    if IS_WINDOWS:
        return _win_create_time(pid)
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            data = f.read().decode("latin-1", "replace")
        close = data.rfind(")")
        fields = data[close + 1:].split()
        return fields[19] if len(fields) >= 20 else None
    except Exception:
        return None


def pid_alive(pid: int, expected_ct: Optional[str] = None) -> bool:
    """存活判断：pid 存在且（若有凭据）创建时间一致。"""
    if not pid:
        return False
    if IS_WINDOWS:
        try:
            import ctypes
            from ctypes import wintypes
            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x1000, False, int(pid))
            if not h:
                return False
            try:
                code = wintypes.DWORD(0)
                if not k.GetExitCodeProcess(h, ctypes.byref(code)):
                    return False
                if code.value != 259:  # STILL_ACTIVE
                    return False
            finally:
                k.CloseHandle(h)
            if expected_ct:
                return process_create_time(pid) == expected_ct
            return True
        except Exception:
            return False
    try:
        os.kill(int(pid), 0)
        if expected_ct:
            return process_create_time(pid) == expected_ct
        return True
    except OSError:
        return False


def taskkill_tree(pid: int) -> bool:
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=15)
        else:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        return True
    except Exception:
        return False


# ---------------- 状态持久化 ----------------

def _save_state():
    with _lock:
        snapshot = {k: v for k, v in _instances.items()}
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(STATE_FILE)


def _load_state():
    if not STATE_FILE.exists():
        return
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        for k, v in data.items():
            _instances[k] = v
    except Exception:
        pass


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(msg: str):
    """写宿主自身日志。

    Task Host 由计划任务以 pythonw.exe（无控制台子系统）启动，
    此时 sys.stderr 为 None，直接写会抛 AttributeError 导致进程退出。
    因此统一写日志文件，仅在 stderr 可用时额外输出一份。
    """
    line = f"{_now()} {msg}"
    try:
        with (STATE_DIR / "host.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    try:
        if sys.stderr is not None:
            sys.stderr.write(line + "\n")
            sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass


# ---------------- 进程管理 ----------------

def start_task(req: dict) -> dict:
    """启动一个真实任务进程。"""
    run_id = str(req.get("run_id") or "").strip()
    argv = req.get("argv") or []
    if not run_id:
        raise ValueError("缺少 run_id")
    if not argv or not isinstance(argv, list):
        raise ValueError("缺少 argv")

    with _lock:
        if run_id in _instances:
            inst = _instances[run_id]
            if pid_alive(inst.get("pid"), inst.get("process_create_time")):
                return {"ok": True, "already_running": True, "pid": inst["pid"]}

    log_path = req.get("log_path") or str(LOGS_DIR / f"{run_id}.log")
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    if req.get("env"):
        env.update({str(k): str(v) for k, v in req["env"].items()})
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    cwd = req.get("cwd") or None

    flags = 0
    if IS_WINDOWS:
        flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

    logf = open(log_path, "ab")
    try:
        proc = subprocess.Popen(
            argv,
            stdout=logf,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=cwd,
            env=env,
            bufsize=0,
            creationflags=flags,
            close_fds=True,
            start_new_session=(not IS_WINDOWS),
        )
    finally:
        logf.close()

    ct = process_create_time(proc.pid)
    if not ct:
        try:
            taskkill_tree(proc.pid)
        except Exception:
            pass
        raise RuntimeError("无法采集进程创建时刻，拒绝托管")

    via_shell = bool(argv and Path(str(argv[0])).name.lower() in (
        "cmd.exe", "cmd", "sh", "/bin/sh", "bash", "/bin/bash"))

    inst = {
        "run_id": run_id,
        "pid": proc.pid,
        "process_create_time": ct,
        "executable_path": str(argv[0]) if argv else "",
        "argv": [str(a) for a in argv],
        "via_shell": via_shell,
        "log_path": log_path,
        "cwd": cwd or "",
        "started_at": _now(),
        "status": "RUNNING",
        "exit_code": None,
        "ended_at": None,
        "host_pid": os.getpid(),
    }
    with _lock:
        _instances[run_id] = inst
        _procs[run_id] = proc
    _save_state()

    t = threading.Thread(target=_watch, args=(run_id, proc), daemon=True,
                         name=f"watch-{run_id}")
    t.start()

    return {"ok": True, "pid": proc.pid,
            "process_create_time": ct, "via_shell": via_shell,
            "log_path": log_path}


def _watch(run_id: str, proc: subprocess.Popen):
    """等待进程结束，记录退出码与状态。"""
    try:
        rc = proc.wait()
    except Exception:
        rc = None
    with _lock:
        inst = _instances.get(run_id)
        if inst is not None:
            inst["status"] = "EXITED"
            inst["exit_code"] = rc
            inst["ended_at"] = _now()
        _procs.pop(run_id, None)
    _save_state()


def get_status(run_id: str) -> Optional[dict]:
    with _lock:
        inst = _instances.get(run_id)
        if not inst:
            return None
        inst = dict(inst)
    alive = pid_alive(inst.get("pid"), inst.get("process_create_time"))
    inst["alive"] = alive
    if alive:
        inst["status"] = "RUNNING"
    elif inst.get("status") == "RUNNING":
        inst = dict(inst)
        inst["status"] = "EXITED"
    return inst


def list_running() -> List[dict]:
    out = []
    with _lock:
        ids = list(_instances.keys())
    for rid in ids:
        st = get_status(rid)
        if st and st.get("alive"):
            out.append(st)
    return out


def stop_task(run_id: str) -> dict:
    with _lock:
        inst = _instances.get(run_id)
        if not inst:
            return {"ok": False, "stop": False, "reason": "run_not_found"}
        inst = dict(inst)
    pid = inst.get("pid")
    if not pid:
        return {"ok": True, "stop": False, "reason": "no_pid"}

    # 身份核验：pid + 创建时间必须一致，否则拒绝（防 PID 复用误杀）
    if not pid_alive(pid, inst.get("process_create_time")):
        if pid_alive(pid):
            return {"ok": False, "stop": False, "pid": pid,
                    "reason": "身份核验失败：PID 已被其他进程复用，拒绝终止"}
        with _lock:
            cur = _instances.get(run_id, {})
            cur["status"] = "EXITED"
        _save_state()
        return {"ok": True, "stop": True, "pid": pid,
                "reason": "already_stopped"}

    ok = taskkill_tree(pid)
    # 等待确认
    for _ in range(30):
        if not pid_alive(pid, inst.get("process_create_time")):
            break
        time.sleep(0.2)
    gone = not pid_alive(pid, inst.get("process_create_time"))

    with _lock:
        cur = _instances.get(run_id)
        if cur is not None:
            cur["status"] = "KILLED"
            cur["ended_at"] = _now()
    _save_state()

    return {"ok": gone, "stop": gone, "pid": pid,
            "reason": "killed" if gone else "kill_failed"}


# ---------------- HTTP 服务 ----------------

def _token() -> str:
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    tok = os.urandom(24).hex()
    TOKEN_FILE.write_text(tok, encoding="utf-8")
    return tok


class Handler(BaseHTTPRequestHandler):
    server_version = "SCTaskHost/1.0"

    def log_message(self, fmt, *args):
        pass  # 静默，避免污染 stderr

    def _json(self, code: int, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _authed(self) -> bool:
        got = self.headers.get("X-Host-Token", "")
        return got and got == _token()

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return {}

    def do_GET(self):
        if not self._authed():
            return self._json(401, {"error": "unauthorized"})
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == "/health":
                return self._json(200, {"ok": True, "pid": os.getpid(),
                                        "host_pid": os.getpid()})
            if u.path == "/running":
                return self._json(200, {"items": list_running()})
            if u.path == "/status":
                rid = (q.get("run_id") or [""])[0]
                st = get_status(rid)
                if st is None:
                    return self._json(404, {"error": "run_not_found"})
                return self._json(200, st)
            return self._json(404, {"error": "not_found"})
        except Exception as e:
            return self._json(500, {"error": str(e)})

    def do_POST(self):
        if not self._authed():
            return self._json(401, {"error": "unauthorized"})
        u = urlparse(self.path)
        try:
            if u.path == "/start":
                return self._json(200, start_task(self._body()))
            if u.path == "/stop":
                return self._json(200, stop_task(self._body().get("run_id", "")))
            return self._json(404, {"error": "not_found"})
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:
            return self._json(500, {"error": str(e),
                                    "trace": traceback.format_exc()[-400:]})


def _pick_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def main():
    host = os.environ.get("SC_HOST_BIND", "127.0.0.1")
    port = int(os.environ.get("SC_HOST_PORT") or 0) or _pick_port()

    _load_state()
    _token()  # 确保 token 文件存在

    # 把实际端口写出来，供 Scheduler 发现（也支持固定端口）
    (STATE_DIR / "host_endpoint.json").write_text(
        json.dumps({"host": host, "port": port, "pid": os.getpid(),
                    "started_at": _now()}, ensure_ascii=False),
        encoding="utf-8")

    srv = ThreadingHTTPServer((host, port), Handler)
    _log(f"[task-host] listening on {host}:{port} pid={os.getpid()}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
