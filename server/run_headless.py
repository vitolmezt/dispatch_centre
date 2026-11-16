# -*- coding: utf-8 -*-
"""以计划任务方式启动调度中心，使其脱离终端 / IDE 的受限 Job Object。

背景（实测结论）：
  - 从终端 / VS Code / DSH 后台任务启动时，进程会处于宿主的受限 Job 中
    （`LimitFlags` 含 `KILL_ON_JOB_CLOSE`）。此时它派生的任务进程会继承该 Job，
    宿主退出（关终端、关 IDE、取消后台任务）会把任务一起带走。
  - `DETACHED_PROCESS` 无法改变 Job 继承，实测无效。
  - 由计划任务启动的进程，其父进程为 `svchost.exe`（Task Scheduler 服务），
    所属 Job 的 `LimitFlags = 0`（不含 KILL_ON_JOB_CLOSE）。
    因此它可以派生真正独立的任务进程，且自身退出不会连带杀任务。

用法：
    python run_headless.py            # 以计划任务方式启动（默认 8000 端口）
    python run_headless.py --stop     # 停止由本脚本启动的实例
    python run_headless.py --status   # 查看状态

说明：本方案不引入任何常驻的额外管理层，调度中心本身即唯一管理者。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent          # server/
SERVER_DIR = HERE
DATA_DIR = SERVER_DIR / "data"
RUN_DIR = DATA_DIR / "run"
RUN_DIR.mkdir(parents=True, exist_ok=True)

STATUS_FILE = RUN_DIR / "server_status.json"
STDOUT_LOG = RUN_DIR / "server_stdout.log"
TASK_NAME = "SchedulerCenterServer"
IS_WINDOWS = os.name == "nt"

DEFAULT_PORT = int(os.environ.get("SC_PORT", "8000"))

# 运行日志轮转策略：
#   实测约 73 字节/请求；前端每秒轮询时约 6MB/天。
#   单文件上限 10MB、保留 3 份历史 => 总量恒定在约 40MB。
LOG_MAX_BYTES = int(os.environ.get("SC_LOG_MAX_BYTES", str(10 * 1024 * 1024)))
LOG_BACKUP_COUNT = int(os.environ.get("SC_LOG_BACKUPS", "3"))


# ---------------- 进程与端口工具 ----------------

def _pid_alive(pid: int) -> bool:
    if not pid:
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


def _port_ok(port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/constants", timeout=3)
        return True
    except Exception:
        return False


def _read_status() -> dict:
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_status(d: dict):
    STATUS_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2),
                           encoding="utf-8")


def _pythonw() -> str:
    """优先无控制台解释器，避免计划任务弹出黑窗。"""
    p = Path(sys.executable)
    if p.suffix.lower() == ".exe":
        cand = p.with_name("pythonw.exe")
        if cand.exists():
            return str(cand)
    return sys.executable


# ---------------- 计划任务管理 ----------------

def _delete_task():
    subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
                   capture_output=True)


def _create_and_run(port: int, extra_env: dict) -> bool:
    """创建计划任务并以 pythonw 启动 run.py。"""
    py = _pythonw()
    run_py = SERVER_DIR / "run.py"

    # 计划任务无法直接设置环境变量，用 boot.py 引导：
    # 设置环境后以 runpy 执行 run.py。
    #
    # 关键 1：pythonw.exe 没有控制台，sys.stdout/sys.stderr 为 None，
    #         uvicorn 的访问日志和错误会全部丢失，所以这里重定向到文件。
    # 关键 2：访问日志会随请求持续增长（实测约 73 字节/请求，
    #         前端每秒轮询时约 6MB/天）。若长期不重启会无限膨胀，
    #         因此使用按大小轮转的写入器：单文件超过阈值即切分，
    #         只保留最近 N 份，总量恒定。
    boot_file = RUN_DIR / "boot.py"
    boot_file.write_text(
        "import os, runpy, sys\n"
        # runpy.run_path 不会把脚本目录加入 sys.path，
        # 必须显式插入并切换工作目录，否则 `app` 包无法导入。
        f"sys.path.insert(0, r'{SERVER_DIR}')\n"
        f"os.chdir(r'{SERVER_DIR}')\n"
        "\n"
        "# ---- 按大小轮转的日志写入器 ----\n"
        f"_LOG_PATH = r'{STDOUT_LOG}'\n"
        f"_MAX_BYTES = {LOG_MAX_BYTES}\n"
        f"_BACKUPS = {LOG_BACKUP_COUNT}\n"
        "\n"
        "class _RotatingLog:\n"
        "    \"\"\"行缓冲 + 超过阈值自动轮转；保留 _BACKUPS 份历史。\"\"\"\n"
        "    def __init__(self, path, max_bytes, backups):\n"
        "        self.path = path\n"
        "        self.max_bytes = max_bytes\n"
        "        self.backups = backups\n"
        "        self._fh = None\n"
        "        self._open()\n"
        "\n"
        "    def _open(self):\n"
        "        self._fh = open(self.path, 'a', encoding='utf-8',\n"
        "                        errors='replace', buffering=1)\n"
        "\n"
        "    def _rotate(self):\n"
        "        try:\n"
        "            self._fh.close()\n"
        "        except Exception:\n"
        "            pass\n"
        "        # 依次后移： .N-1 -> .N，最后删掉最旧的\n"
        "        for i in range(self.backups - 1, 0, -1):\n"
        "            src = f'{self.path}.{i}'\n"
        "            dst = f'{self.path}.{i + 1}'\n"
        "            if os.path.exists(src):\n"
        "                try:\n"
        "                    if os.path.exists(dst):\n"
        "                        os.remove(dst)\n"
        "                    os.replace(src, dst)\n"
        "                except OSError:\n"
        "                    pass\n"
        "        try:\n"
        "            if os.path.exists(self.path):\n"
        "                os.replace(self.path, f'{self.path}.1')\n"
        "        except OSError:\n"
        "            pass\n"
        "        self._open()\n"
        "\n"
        "    def write(self, s):\n"
        "        try:\n"
        "            if self._fh.tell() >= self.max_bytes:\n"
        "                self._rotate()\n"
        "        except Exception:\n"
        "            pass\n"
        "        return self._fh.write(s)\n"
        "\n"
        "    def flush(self):\n"
        "        try:\n"
        "            self._fh.flush()\n"
        "        except Exception:\n"
        "            pass\n"
        "\n"
        "    def close(self):\n"
        "        try:\n"
        "            self._fh.close()\n"
        "        except Exception:\n"
        "            pass\n"
        "\n"
        "    # uvicorn / logging 可能查询这些属性\n"
        "    def isatty(self):\n"
        "        return False\n"
        "\n"
        "    def fileno(self):\n"
        "        return self._fh.fileno()\n"
        "\n"
        "    @property\n"
        "    def encoding(self):\n"
        "        return 'utf-8'\n"
        "\n"
        "\n"
        "_log = _RotatingLog(_LOG_PATH, _MAX_BYTES, _BACKUPS)\n"
        "sys.stdout = _log\n"
        "sys.stderr = _log\n"
        f"print('=== scheduler-center start port={port} "
        f"max={LOG_MAX_BYTES}B backups={LOG_BACKUP_COUNT} ===', flush=True)\n"
        f"os.environ['SC_PORT'] = '{port}'\n"
        + "".join(f"os.environ['{k}'] = '{v}'\n" for k, v in extra_env.items())
        + f"sys.argv = ['run.py']\n"
        f"runpy.run_path(r'{run_py}', run_name='__main__')\n",
        encoding="utf-8")

    _delete_task()
    tr = f'"{py}" "{boot_file}"'
    r = subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", tr,
                        "/SC", "ONCE", "/ST", "23:59", "/F", "/RL", "LIMITED"],
                       capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        print(f"[启动失败] 无法创建计划任务: {(r.stderr or r.stdout).strip()[:200]}")
        return False
    r2 = subprocess.run(["schtasks", "/Run", "/TN", TASK_NAME],
                        capture_output=True, text=True, errors="replace")
    if r2.returncode != 0:
        print(f"[启动失败] 无法运行计划任务: {(r2.stderr or r2.stdout).strip()[:200]}")
        return False
    return True


def _find_server_pids() -> list:
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | "
          "Where-Object { $_.CommandLine -like '*boot.py*' -or "
          "$_.CommandLine -like '*run.py*' } | "
          "Select-Object -ExpandProperty ProcessId")
    o = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, errors="replace").stdout
    return [int(t) for t in o.split() if t.isdigit()]


# ---------------- 对外命令 ----------------

def start(port: int = DEFAULT_PORT, wait: float = 40.0) -> int:
    st = _read_status()
    if st and _pid_alive(st.get("pid", 0)) and _port_ok(port):
        print(f"调度中心已在运行 (pid={st['pid']}, port={port})")
        return 0
    if _port_ok(port):
        print(f"端口 {port} 已被其他进程占用，请先释放")
        return 1

    print(f"以计划任务方式启动调度中心（端口 {port}）…")
    if not _create_and_run(port, {}):
        return 1

    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(0.5)
        if _port_ok(port):
            pids = _find_server_pids()
            pid = pids[0] if pids else None
            _write_status({"pid": pid, "port": port,
                           "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                           "launch": "schtasks", "task_name": TASK_NAME})
            print(f"启动成功：pid={pid} port={port}")
            print(f"提示：该实例不在终端/IDE 的受限 Job 中，关闭本终端不会影响它。")
            return 0
    print("启动超时，请检查 data/run/server_stdout.log")
    return 1


def stop() -> int:
    st = _read_status()
    pid = st.get("pid")
    _delete_task()
    if pid and _pid_alive(pid):
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True)
        time.sleep(2)
    for p in _find_server_pids():
        if _pid_alive(p):
            subprocess.run(["taskkill", "/F", "/PID", str(p)],
                           capture_output=True)
    STATUS_FILE.unlink(missing_ok=True)
    # 日志大小已由轮转机制控制（单文件上限 + 保留份数），
    # 这里不再搬移文件，只清理早期版本遗留的
    # server_<时间戳>.log 归档，避免新旧两套策略叠加堆积。
    removed = 0
    for old in RUN_DIR.glob("server_[0-9]*-[0-9]*.log"):
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass
    if removed:
        print(f"已清理历史归档 {removed} 份（现由轮转机制管理日志）")
    files = _log_files()
    if files:
        total = sum(p.stat().st_size for p in files)
        print(f"当前日志 {len(files)} 份，合计 {total / 1024 / 1024:.2f} MB")
    print("已停止调度中心（由本脚本启动的实例）")
    return 0


def status() -> int:
    st = _read_status()
    pid = st.get("pid")
    port = st.get("port", DEFAULT_PORT)
    alive = pid and _pid_alive(pid)
    print(f"状态文件: {st if st else '(无)'}")
    print(f"进程存活: {bool(alive)}")
    print(f"端口可访问: {_port_ok(port)}")
    r = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST"],
                       capture_output=True, text=True, errors="replace")
    print(f"计划任务: {'存在' if r.returncode == 0 else '不存在'}")
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            if line.strip().startswith(("Status", "状态")):
                print(f"  {line.strip()}")
    return 0


def _log_files() -> list:
    """按时间顺序返回当前日志及轮转备份（旧 -> 新）。"""
    out = []
    for i in range(LOG_BACKUP_COUNT, 0, -1):
        p = STDOUT_LOG.with_name(f"{STDOUT_LOG.name}.{i}")
        if p.exists():
            out.append(p)
    if STDOUT_LOG.exists():
        out.append(STDOUT_LOG)
    return out


def logs(follow: bool = False, lines: int = 100) -> int:
    """查看调度中心运行日志（等价于前台运行时终端里的输出）。

    轮转后内容分散在 server_stdout.log 与 .1/.2/.3 中，
    这里按时间顺序拼接后取尾部，保证切换文件时上下文连续。
    """
    files = _log_files()
    if not files:
        print(f"日志文件尚无内容: {STDOUT_LOG}")
        print("提示：只有在用 run_headless.py 启动后才会产生。")
        return 1

    print(f"日志文件: {STDOUT_LOG}")
    if len(files) > 1:
        print(f"含轮转备份 {len(files) - 1} 份（阈值 "
              f"{LOG_MAX_BYTES // 1024 // 1024}MB，保留 {LOG_BACKUP_COUNT} 份）")
    print()

    if follow:
        print("（Ctrl+C 退出跟踪）\n")
        try:
            # 先展示历史尾部，再从当前文件尾部开始跟踪
            all_lines = []
            for p in files:
                all_lines.extend(
                    p.read_text(encoding="utf-8", errors="replace").splitlines())
            for line in all_lines[-lines:]:
                print(line)
            pos = STDOUT_LOG.stat().st_size if STDOUT_LOG.exists() else 0
            while True:
                # 轮转检测：当前文件变小说明刚切过分，位置归零
                try:
                    cur = STDOUT_LOG.stat().st_size if STDOUT_LOG.exists() else 0
                except OSError:
                    cur = 0
                if cur < pos:
                    pos = 0
                with STDOUT_LOG.open("r", encoding="utf-8",
                                     errors="replace") as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos = f.tell()
                if chunk:
                    print(chunk, end="")
                else:
                    time.sleep(0.5)
        except KeyboardInterrupt:
            print("\n已停止跟踪")
        return 0

    all_lines = []
    for p in files:
        all_lines.extend(
            p.read_text(encoding="utf-8", errors="replace").splitlines())
    for line in all_lines[-lines:]:
        print(line)
    return 0


def main():
    ap = argparse.ArgumentParser(description="以计划任务方式启动调度中心")
    ap.add_argument("--stop", action="store_true", help="停止实例")
    ap.add_argument("--status", action="store_true", help="查看状态")
    ap.add_argument("--logs", action="store_true", help="查看运行日志")
    ap.add_argument("-f", "--follow", action="store_true", help="配合 --logs 实时跟踪")
    ap.add_argument("-n", "--lines", type=int, default=100, help="日志行数（默认 100）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口")
    a = ap.parse_args()
    if a.stop:
        sys.exit(stop())
    if a.status:
        sys.exit(status())
    if a.logs:
        sys.exit(logs(follow=a.follow, lines=a.lines))
    sys.exit(start(a.port))


if __name__ == "__main__":
    main()
