# -*- coding: utf-8 -*-
"""任务进程与调度中心生命周期解耦 —— 回归测试。

覆盖「任务进程为什么能不受调度中心影响」的每一项技术前提：
  - COMMAND 不再无条件套 shell（记录的 PID 即真实任务进程）
  - shell 语法 / 内建命令 / 引号内分隔符的判定边界
  - 日志直写文件（脱离 PIPE）与 log_offset 续读、防自循环
  - detach 创建标志的权限降级
  - shutdown() 不杀死 detach 任务
  - 停止时身份核验失败必须拒绝且不谎报成功
  - 每实例命名 Job 不启用 KILL_ON_JOB_CLOSE
  - run_headless.py 提供脱离受限 Job 的启动方式

说明：部分用例为「源码契约断言」——直接读取 app/*.py 检查关键实现存在，
这类断言对重构敏感（改变量名/缩进可能误报），标注了 source_contract。
"""
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
    """取出某个方法体（到下一个同级 def 为止），避免按固定长度截断。"""
    i = src.index(signature)
    rest = src[i + len(signature):]
    m = rest.find("\n    def ")
    return rest[:m] if m > 0 else rest


# ===========================================================================
# 1. shell 判定
# ===========================================================================

@pytest.mark.parametrize("cmd", [
    "python -m http.server 8888",
    "python run.py",
    r'"C:\Users\b\miniforge3\python.exe" c:/a/b.py',
    "ping 127.0.0.1 -n 100",
    "node app.js --port 3000",
])
def test_plain_command_no_shell(cmd):
    """普通命令行不应走 shell —— 这是「记录 pid 等于真实进程」的前提。"""
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
    """引号外的 shell 语法必须走 shell，否则语义会变。"""
    assert Executor._needs_shell(cmd), f"漏判 shell 语法: {cmd}"


def test_quoted_semicolon_not_shell():
    """引号内的分号属于参数语法，不是 shell 分隔符。"""
    cmd = 'python -c "import time; time.sleep(1)"'
    assert not Executor._needs_shell(cmd), "引号内分号被误判为 shell 语法"
    argv = Executor._build_argv(_job(cmd))
    assert Path(argv[0]).name.lower() != "cmd.exe", argv


@pytest.mark.parametrize("cmd", ["echo hello", "dir", "set PATH=x", "cd /d C:\\"])
def test_shell_builtin_needs_shell(cmd):
    """内建命令不是磁盘文件，必须交给 shell。"""
    assert Executor._needs_shell(cmd), f"内建命令未走 shell: {cmd}"


def test_split_preserves_quoted_args():
    """拆分要保留引号内的空格参数。"""
    parts = Executor._split_command('python -c "print(1)" --name "a b"')
    assert parts[0] == "python"
    assert any("print(1)" in p for p in parts), parts
    assert "a b" in parts, parts


# ===========================================================================
# 2. via_shell 标记
# ===========================================================================

def test_via_shell_flag_roundtrip():
    """via_shell / log_offset 需能持久化往返。"""
    r = Run(id="r1", job_name="j", job_type="COMMAND",
            via_shell=True, log_offset=1234)
    d = r.to_dict()
    assert d["via_shell"] is True
    assert d["log_offset"] == 1234
    r2 = Run.from_dict(d)
    assert r2.via_shell is True and r2.log_offset == 1234


def test_via_shell_true_for_shell_command():
    """走 shell 的命令必须能被识别，便于停止/恢复按进程树处理。"""
    argv = Executor._build_argv(_job("dir | findstr x"))
    name = Path(str(argv[0])).name.lower()
    assert name in ("cmd.exe", "cmd", "sh", "bash"), argv


# ===========================================================================
# 3. 日志偏移续读
# ===========================================================================

class _CollectRunner:
    """只收集日志行的最小 runner。"""

    def __init__(self):
        self.lines = []

    def on_started(self, run):
        pass

    def on_log(self, run, line, write_file=True):
        self.lines.append(line)

    def on_finished(self, run):
        pass


def test_log_offset_avoids_replay(tmp_dir):
    """从 offset 续读：不重复推送历史日志，也不丢新增。"""
    log = tmp_dir / "off.log"
    log.write_bytes(b"old-1\nold-2\n")
    run = Run(id="r_off", job_name="j", job_type="COMMAND", log_path=str(log))

    runner = _CollectRunner()
    ex = Executor(runner)
    pos = log.stat().st_size
    run.log_offset = pos

    # 无新增 -> 不应读任何历史
    pos2 = ex._pump_log(run, str(log), pos)
    assert runner.lines == [], f"重复推送历史日志: {runner.lines}"
    assert pos2 == pos

    # 追加新内容 -> 只读新增
    with log.open("ab") as f:
        f.write(b"new-1\n")
    ex._pump_log(run, str(log), pos2)
    assert runner.lines == ["new-1"], runner.lines
    assert run.log_offset == log.stat().st_size


def test_log_truncation_resets(tmp_dir):
    """日志文件被截断/轮转时应回到起点而非卡死。"""
    log = tmp_dir / "trunc.log"
    log.write_bytes(b"aaa\n")
    run = Run(id="r_tr", job_name="j", job_type="COMMAND", log_path=str(log))

    runner = _CollectRunner()
    ex = Executor(runner)
    ex._pump_log(run, str(log), 0)
    assert runner.lines == ["aaa"]

    # 模拟轮转：文件变短
    log.write_bytes(b"b\n")
    runner.lines.clear()
    pos = ex._pump_log(run, str(log), 999)
    assert "b" in runner.lines, (runner.lines, pos)


def test_on_log_write_file_false_avoids_feedback(tmp_dir):
    """detach 跟随读到的行不得再写回同一文件（否则无限膨胀）。"""
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


# ===========================================================================
# 4. Windows 进程创建标志
# ===========================================================================

def test_detach_flag_variants_ordered():
    """detach 标志应有降级候选，避免 BREAKAWAY 权限失败导致启动不了。"""
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


# ===========================================================================
# 5. 真实进程：日志脱离 PIPE
# ===========================================================================

def test_real_process_writes_log_file(tmp_dir):
    """真实启动一个短命进程，确认 stdout 直接落到日志文件。"""
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


# ===========================================================================
# 6. shutdown 不杀 detach
# ===========================================================================

@pytest.mark.source_contract
def test_shutdown_skips_detached_source():
    """shutdown() 源码层面必须对 detach 执行 continue。"""
    src = _read_src("scheduler.py")
    block = _method_block(src, "def shutdown(self)")
    assert 'getattr(run, "detached", False)' in block, "shutdown 未判断 detached"
    assert "continue" in block, "shutdown 未跳过 detach"
    assert "self.abort(run_id)" in block, "shutdown 应仅中止非 detach"


# ===========================================================================
# 7. 停止失败不得谎报
# ===========================================================================

def test_stop_refuses_on_identity_mismatch(sched):
    """身份核验失败必须返回拒绝，且不得声称已停止。"""
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


# ===========================================================================
# 8. 环境探测与字段往返
# ===========================================================================

def test_job_environment_probe():
    """Job 环境探测必须返回可解释的结果，不得抛异常。"""
    r = Executor.probe_job_environment()
    for k in ("in_job", "kill_on_close", "breakaway_allowed", "can_escape"):
        assert k in r, f"缺少字段 {k}"
    if os.name != "nt":
        assert r["can_escape"] is True, r


def test_isolation_warning_field_roundtrip():
    """isolation_warning 必须可持久化往返（用于如实暴露环境约束）。"""
    r = Run(id="r_iso", job_name="j", job_type="COMMAND",
            isolation_warning="测试告警")
    d = r.to_dict()
    assert d["isolation_warning"] == "测试告警", d
    r2 = Run.from_dict(d)
    assert r2.isolation_warning == "测试告警"


# ===========================================================================
# 9. 源码契约：孤儿清理 / 命名 Job / 启动方式 / 停止途径 / 一致性核对
# ===========================================================================

@pytest.mark.source_contract
def test_shell_orphan_sweep_exists():
    """via_shell 任务停止时必须具备孤儿清理能力，且按父 pid 精确匹配。"""
    src = _read_src("scheduler.py")
    assert "_sweep_shell_orphans" in src, "缺少 shell 孤儿清理"

    block = _method_block(src, "    def stop(self")
    assert "_sweep_shell_orphans" in block, "stop() 未调用孤儿清理"
    assert 'getattr(run, "via_shell", False)' in block, "未按 via_shell 触发"

    j = src.index("def _sweep_shell_orphans")
    sweep = src[j:j + 1500]
    assert "ParentProcessId=" in sweep, "孤儿清理未按父 pid 精确匹配"


@pytest.mark.source_contract
def test_instance_job_no_kill_on_close():
    """每实例命名 Job 必须不启用 KILL_ON_JOB_CLOSE。

    这是「任务不依赖调度中心存活」的技术前提：调度中心退出时其 Job 句柄关闭，
    若设了 KILL_ON_JOB_CLOSE 会把任务一起带走。
    """
    src = _read_src("executor.py")
    assert "_job_attach" in src, "缺少每实例 Job 建立逻辑"
    assert "info.Basic.LimitFlags = 0" in src, "Job 未显式清零 LimitFlags"
    seg = src.split("def _job_attach")[1].split("def _job_terminate")[0]
    assert "0x00002000" not in seg, "Job 建立处出现了 KILL_ON_JOB_CLOSE 常量"


def test_headless_launcher_exists():
    """必须提供让调度中心脱离受限 Job 的启动方式。"""
    lh = SERVER_DIR / "run_headless.py"
    assert lh.exists(), "缺少 run_headless.py"
    src = lh.read_text(encoding="utf-8")
    assert "schtasks" in src, "未通过计划任务启动"
    assert "SCHTASKS" in src.upper(), "缺少计划任务常量"

    run_py = (SERVER_DIR / "run.py").read_text(encoding="utf-8")
    assert "SC_PORT" in run_py, "run.py 未支持 SC_PORT"


@pytest.mark.source_contract
def test_stop_uses_job_then_tree():
    """停止应优先用 Job 句柄终止整组，无句柄时回退进程树并记录途径。"""
    src = _read_src("scheduler.py")
    block = _method_block(src, "    def stop(self")
    assert "_job_terminate" in block, "stop 未尝试用 Job 终止"
    assert "safe_kill" in block, "stop 未保留进程树回退"
    assert "stop_via" in block, "未记录停止途径"


@pytest.mark.source_contract
def test_terminal_run_reconcile_exists():
    """Scheduler 必须核对终态记录与真实进程状态的一致性。"""
    src = _read_src("scheduler.py")
    assert "_reconcile_terminal_runs" in src, "缺少终态记录一致性核对"

    block = _method_block(src, "def recover_on_start")
    assert "_reconcile_terminal_runs" in block, "recover_on_start 未调用一致性核对"
    assert "recent_runs" in src, "未查询终态记录"

    j = src.index("def _reconcile_terminal_runs")
    seg = src[j:j + 2000]
    assert "verify_identity" in seg, "核对未做身份核验"
