# -*- coding: utf-8 -*-
"""任务进程与调度中心生命周期解耦 —— 回归测试。"""
import os
import sys
from pathlib import Path

import pytest

from app.executor import Executor
from app.models import Job, Run

pytestmark = pytest.mark.decouple

APP_DIR = Path(__file__).resolve().parent.parent / "app"
SERVER_DIR = Path(__file__).resolve().parent.parent


def _job(command="echo hi", detach=False):
    return Job(name="t", type="COMMAND", params={"command": command, "detach": detach})


def _read_src(name):
    return (APP_DIR / name).read_text(encoding="utf-8")


def _method_block(src, signature):
    i = src.index(signature)
    rest = src[i + len(signature):]
    m = rest.find("\n    def ")
    return rest[:m] if m > 0 else rest


@pytest.mark.parametrize("cmd", [
    "python -m http.server 8888",
    "python run.py",
    r'"C:\Users\b\miniforge3\python.exe" c:/a/b.py',
    "ping 127.0.0.1 -n 100",
    "node app.js --port 3000",
])
def test_plain_command_no_shell(cmd):
    assert not Executor._needs_shell(cmd), f"误判为需要 shell: {cmd}"
    argv = Executor._build_argv(_job(cmd))
    assert Path(argv[0]).name.lower() != "cmd.exe", argv


@pytest.mark.parametrize("cmd", [
    "dir | findstr x",
    "python run.py > out.log",
    "echo a && echo b",
    "copy a.txt b.txt",
])
def test_shell_syntax_needs_shell(cmd):
    assert Executor._needs_shell(cmd), f"漏判 shell 语法: {cmd}"


def test_quoted_semicolon_not_shell():
    cmd = 'python -c "import time; time.sleep(1)"'
    assert not Executor._needs_shell(cmd), "引号内分号被误判为 shell 语法"
    argv = Executor._build_argv(_job(cmd))
    assert Path(argv[0]).name.lower() != "cmd.exe", argv


@pytest.mark.parametrize("cmd", ["echo hello", "dir", "set PATH=x", "cd /d C:\\"])
def test_shell_builtin_needs_shell(cmd):
    assert Executor._needs_shell(cmd), f"内建命令未走 shell: {cmd}"


def test_split_preserves_quoted_args():
    parts = Executor._split_command('python -c "print(1)" --name "a b"')
    assert parts[0] == "python"
    assert any("print(1)" in p for p in parts), parts
    assert "a b" in parts, parts

def test_via_shell_flag_roundtrip():
    r = Run(id="r1", job_name="j", job_type="COMMAND",
            via_shell=True, log_offset=1234)
    d = r.to_dict()
    assert d["via_shell"] is True
    assert d["log_offset"] == 1234
    r2 = Run.from_dict(d)
    assert r2.via_shell is True and r2.log_offset == 1234


def test_via_shell_true_for_shell_command():
    argv = Executor._build_argv(_job("dir | findstr x"))
    name = Path(str(argv[0])).name.lower()
    assert name in ("cmd.exe", "cmd", "sh", "bash"), argv

class _CollectRunner:

    def __init__(self):
        self.lines = []

    def on_started(self, run):
        pass

    def on_log(self, run, line, write_file=True):
        self.lines.append(line)

    def on_finished(self, run):
        pass


def test_log_offset_avoids_replay(tmp_dir):
    log = tmp_dir / "off.log"
    log.write_bytes(b"old-1\nold-2\n")
    run = Run(id="r_off", job_name="j", job_type="COMMAND", log_path=str(log))

    runner = _CollectRunner()
    ex = Executor(runner)
    pos = log.stat().st_size
    run.log_offset = pos

    pos2 = ex._pump_log(run, str(log), pos)
    assert runner.lines == [], f"重复推送历史日志: {runner.lines}"
    assert pos2 == pos

    with log.open("ab") as f:
        f.write(b"new-1\n")
    ex._pump_log(run, str(log), pos2)
    assert runner.lines == ["new-1"], runner.lines
    assert run.log_offset == log.stat().st_size


def test_log_truncation_resets(tmp_dir):
    log = tmp_dir / "trunc.log"
    log.write_bytes(b"aaa\n")
    run = Run(id="r_tr", job_name="j", job_type="COMMAND", log_path=str(log))

    runner = _CollectRunner()
    ex = Executor(runner)
    ex._pump_log(run, str(log), 0)
    assert runner.lines == ["aaa"]

    log.write_bytes(b"b\n")
    runner.lines.clear()
    pos = ex._pump_log(run, str(log), 999)
    assert "b" in runner.lines, (runner.lines, pos)


def test_on_log_write_file_false_avoids_feedback(tmp_dir):
    log = tmp_dir / "fb.log"
    log.write_bytes(b"line-1\n")
    run = Run(id="r_fb", job_name="j", job_type="COMMAND", log_path=str(log))

    class R:
        appended = 0

        def on_log(self, r, line, write_file=True):
            if write_file:
                R.appended += 1

    ex = Executor(R())
    ex._pump_log(run, str(log), 0)
    assert R.appended == 0, "跟随读取时发生了写回，会自我循环"

def test_detach_flag_variants_ordered():
    variants = Executor._detach_flags_variants()
    assert len(variants) >= 2, variants
    assert variants[0] & 0x01000000, "首选应包含 CREATE_BREAKAWAY_FROM_JOB"
    assert not (variants[-1] & 0x01000000), "末位应去掉 BREAKAWAY 以便降级"
    for v in variants:
        assert v & 0x00000008, "detach 应含 DETACHED_PROCESS"
        assert v & 0x00000200, "detach 应含 CREATE_NEW_PROCESS_GROUP"


@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 有 DETACHED_PROCESS 语义")
def test_non_detach_uses_no_window_only():
    """非 detach 不应带 DETACHED_PROCESS（否则控制台行为异常）。"""
    flags = Executor._spawn_flags(False)
    assert not (flags & 0x00000008), "非 detach 不应使用 DETACHED_PROCESS"


def test_real_process_writes_log_file(tmp_dir):
    log = tmp_dir / "real.log"
    run = Run(id="r_real", job_name="j", job_type="COMMAND", log_path=str(log))

    class R:
        def __init__(self):
            self.lines = []
            self.finished = []

        def prepare_log_path(self, r):
            return r.log_path

        def on_started(self, r):
            pass

        def on_log(self, r, line, write_file=True):
            self.lines.append(line)

        def on_finished(self, r):
            self.finished.append(r)

        def on_user_stopped(self, r):
            pass

    runner = R()
    ex = Executor(runner)
    job = Job(name="j", type="COMMAND",
              params={"command": 'python -c "print(12345)"', "detach": True})
    ex.run(run, job)

    assert log.exists(), "日志文件未创建"
    content = log.read_text(encoding="utf-8", errors="replace")
    assert "12345" in content, content
    assert run.exit_code == 0, (run.exit_code, run.error)


def test_shutdown_skips_detached(sched):
    d = Run(id="det1", job_name="j", job_type="COMMAND", status="RUNNING",
            detached=True, pid=9011, process_create_time="ct-d")
    n = Run(id="nor1", job_name="j", job_type="COMMAND", status="RUNNING",
            detached=False, pid=9012, process_create_time="ct-n")
    sched.running["det1"] = d
    sched.running["nor1"] = n

    aborted = []
    sched.abort = lambda rid: aborted.append(rid)
    sched.aps.shutdown = lambda wait=False: None

    sched.shutdown()

    assert "det1" not in aborted, f"独立进程(detach)不应在 shutdown 被中止: {aborted}"
    assert "nor1" in aborted, f"普通任务应在 shutdown 被中止: {aborted}"


def test_stop_refuses_on_identity_mismatch(sched):
    run = Run(id="fake1", job_name="j", job_type="COMMAND", status="RUNNING",
              pid=os.getpid(), process_create_time="9999999999999999999",
              detached=True)
    sched.store.save_run(run)
    res = sched.stop("fake1")
    assert res.get("ok") is False, res
    assert res.get("stop") is False, res
    assert "身份核验失败" in str(res.get("reason") or ""), res


def test_stop_reports_unknown_run(sched):
    """不存在的 run 必须给出明确的 run_not_found。"""
    res = sched.stop("nope-not-exist")
    assert res.get("reason") == "run_not_found", res

def test_job_environment_probe():
    """Job 环境探测必须返回可解释的结果，不得抛异常。"""
    r = Executor.probe_job_environment()
    for k in ("in_job", "kill_on_close", "breakaway_allowed", "can_escape"):
        assert k in r, f"缺少字段 {k}"
    if os.name != "nt":
        assert r["can_escape"] is True, r


def test_isolation_warning_field_roundtrip():
    r = Run(id="r_iso", job_name="j", job_type="COMMAND",
            isolation_warning="测试告警")
    d = r.to_dict()
    assert d["isolation_warning"] == "测试告警", d
    r2 = Run.from_dict(d)
    assert r2.isolation_warning == "测试告警"

def test_shell_orphan_sweep_on_stop(sched, monkeypatch):

    from app import identity as identity_mod

    run = Run(id="sw1", job_name="j", job_type="COMMAND", status="RUNNING",
              pid=424242, process_create_time="ct-sw", via_shell=True)
    sched.store.save_run(run)
    sched.running["sw1"] = run

    monkeypatch.setattr(identity_mod, "verify_identity", lambda r, pid=None: True)
    monkeypatch.setattr(identity_mod, "process_is_alive", lambda r, pid=None: False)

    calls = []
    monkeypatch.setattr(sched._control, "sweep_shell_orphans",
                        lambda r: calls.append(r.id) or "cleaned_children=1")
    monkeypatch.setattr(sched, "_sweep_shell_orphans",
                        lambda r: calls.append(r.id) or "cleaned_children=1")

    res = sched.stop("sw1")

    assert calls == ["sw1"], f"via_shell 停止未触发孤儿清理: {calls}"
    assert res.get("orphan_sweep") == "cleaned_children=1", res


@pytest.mark.source_contract
def test_instance_job_no_kill_on_close():

    # 探测条件须同时接受两种命名：早期的 _job_attach 与拆分后的 job_attach
    for candidate in ("executor/win_job.py", "executor/core.py", "executor.py"):
        path = APP_DIR / candidate
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if "def job_attach" in text or "def _job_attach" in text:
            src = text
            break
    else:
        raise AssertionError("找不到含 job_attach 实现的 executor 文件")

    assert "_job_attach" in src or "def job_attach" in src, "缺少每实例 Job 建立逻辑"
    assert "info.Basic.LimitFlags = 0" in src, "Job 未显式清零 LimitFlags"
    marker = "def _job_attach" if "def _job_attach" in src else "def job_attach"
    tail = src.split(marker, 1)[1]
    nxt = tail.find("\ndef ")
    seg = tail[:nxt] if nxt > 0 else tail
    assert "0x00002000" not in seg, "Job 建立处出现了 KILL_ON_JOB_CLOSE 常量"


def test_headless_launcher_exists():

    lh = SERVER_DIR / "run_headless.py"
    assert lh.exists(), "缺少 run_headless.py"
    src = lh.read_text(encoding="utf-8")
    assert "schtasks" in src, "未通过计划任务启动"
    assert "SCHTASKS" in src.upper(), "缺少计划任务常量"

    run_py = (SERVER_DIR / "run.py").read_text(encoding="utf-8")
    assert "SC_PORT" in run_py, "run.py 未支持 SC_PORT"


def test_stop_uses_job_then_tree(sched, monkeypatch):

    from app import identity as identity_mod

    def _mk(run_id):
        r = Run(id=run_id, job_name="j", job_type="COMMAND", status="RUNNING",
                pid=555000, process_create_time="ct-" + run_id)
        sched.store.save_run(r)
        sched.running[run_id] = r
        return r

    monkeypatch.setattr(identity_mod, "verify_identity", lambda r, pid=None: True)
    monkeypatch.setattr(identity_mod, "process_is_alive", lambda r, pid=None: False)
    monkeypatch.setattr(identity_mod, "safe_kill", lambda r, pid=None, group_id=None: None)
    monkeypatch.setattr(sched, "_finalize_user_stop", lambda r, reason, status: None)
    monkeypatch.setattr(sched.executor, "_job_close", lambda rid: None)
    monkeypatch.setattr(sched.executor, "mark_user_stopped", lambda rid: None)
    monkeypatch.setattr(sched, "_sweep_shell_orphans", lambda r: "")

    _mk("via_job")
    monkeypatch.setattr(sched.executor, "_job_terminate", lambda rid: True)
    res = sched.stop("via_job")
    assert res.get("stop_via") == "job", res

    _mk("via_tree")
    monkeypatch.setattr(sched.executor, "_job_terminate", lambda rid: False)
    res = sched.stop("via_tree")
    assert res.get("stop_via") == "tree", res


def test_terminal_run_reconcile_fixes_inconsistent(sched, monkeypatch):

    from app import identity as identity_mod

    run = Run(id="rec1", job_name="j", job_type="COMMAND", status="KILLED",
              pid=777888, process_create_time="ct-rec", detached=True,
              end="2026-11-17T00:00:00+08:00", exit_code=1)
    sched.store.save_run(run)

    monkeypatch.setattr(identity_mod, "verify_identity", lambda r, pid=None: True)
    monkeypatch.setattr(identity_mod, "process_is_alive", lambda r, pid=None: True)
    monkeypatch.setattr(sched, "_detached_watch_loop", lambda r: None)

    sched.recover_on_start()

    after = sched.store.get_run("rec1")
    assert after.status == "RUNNING", f"终态不一致未被修正: {after.status}"
    assert after.end is None, after.end
    assert after.exit_code is None, after.exit_code
    assert "状态不一致已修正" in (after.interrupted_reason or ""), after.interrupted_reason
