# -*- coding: utf-8 -*-
"""任务进程与调度中心生命周期解耦 —— 新增功能回归测试。

对应规范「第七阶段 8. 新增功能必须有对应测试」。
覆盖本次改造的关键点：
  - COMMAND 不再无条件套 shell（记录的就是真实任务进程）
  - shell 语法 / 内建命令的判定边界
  - 日志直写文件（脱离 PIPE）与 log_offset 续读
  - shutdown() 不杀死 detach 任务
  - 停止时身份核验失败必须拒绝且不谎报成功
  - detach 创建标志的权限降级
本文件为纯单元/集成测试，不依赖运行中的后端；真实进程测试另见文档。
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="sc_decouple_")
os.environ.setdefault("SC_NODE_ID", "unit-node")
# 单元测试不依赖外部 Task Host（生产默认启用，见 Executor.use_host）
os.environ["SC_USE_TASK_HOST"] = "0"

from app.executor import Executor
from app.models import Job, Run


def _job(command="echo hi", detach=False):
    return Job(name="t", type="COMMAND", params={"command": command, "detach": detach})


# ---------- 1. shell 判定 ----------

def t01_plain_command_no_shell():
    """普通命令行不应走 shell —— 这是「记录 pid 等于真实进程」的前提。"""
    for cmd in ("python -m http.server 8888",
                "python run.py",
                r'"C:\Users\b\miniforge3\python.exe" c:/a/b.py',
                "ping 127.0.0.1 -n 100",
                "node app.js --port 3000"):
        assert not Executor._needs_shell(cmd), f"误判为需要 shell: {cmd}"
    argv = Executor._build_argv(_job("python -m http.server 8888"))
    assert argv == ["python", "-m", "http.server", "8888"], argv
    assert Path(argv[0]).name.lower() != "cmd.exe"
    print("[OK] 普通 COMMAND 直接执行，不套 shell")


def t02_shell_syntax_needs_shell():
    """引号外的 shell 语法必须走 shell，否则语义会变。"""
    for cmd in ("dir | findstr x",
                "python run.py > out.log",
                "echo a && echo b",
                "copy a.txt b.txt"):
        assert Executor._needs_shell(cmd), f"漏判 shell 语法: {cmd}"
    print("[OK] 管道/重定向/串联正确判定需要 shell")


def t03_quoted_semicolon_not_shell():
    """引号内的分号属于参数语法，不是 shell 分隔符。"""
    cmd = 'python -c "import time; time.sleep(1)"'
    assert not Executor._needs_shell(cmd), "引号内分号被误判为 shell 语法"
    argv = Executor._build_argv(_job(cmd))
    assert Path(argv[0]).name.lower() != "cmd.exe", argv
    print("[OK] 引号内分号不触发 shell 包装")


def t04_shell_builtin_needs_shell():
    """内建命令不是磁盘文件，必须交给 shell。"""
    for cmd in ("echo hello", "dir", "set PATH=x", "cd /d C:\\"):
        assert Executor._needs_shell(cmd), f"内建命令未走 shell: {cmd}"
    print("[OK] shell 内建命令正确回退到 shell")


def t05_split_preserves_quoted_args():
    """拆分要保留引号内的空格参数。"""
    parts = Executor._split_command('python -c "print(1)" --name "a b"')
    assert parts[0] == "python"
    assert "print(1)" in parts or any("print(1)" in p for p in parts), parts
    assert "a b" in parts, parts
    print("[OK] 参数拆分保留引号内空格")


# ---------- 2. via_shell 标记 ----------

def t06_via_shell_flag_roundtrip():
    """via_shell / log_offset 需能持久化往返。"""
    r = Run(id="r1", job_name="j", job_type="COMMAND",
            via_shell=True, log_offset=1234)
    d = r.to_dict()
    assert d["via_shell"] is True
    assert d["log_offset"] == 1234
    r2 = Run.from_dict(d)
    assert r2.via_shell is True and r2.log_offset == 1234
    print("[OK] via_shell / log_offset 持久化往返")


def t07_via_shell_true_for_shell_command():
    """走 shell 的命令必须被标记，便于停止/恢复按进程树处理。"""
    argv = Executor._build_argv(_job("dir | findstr x"))
    name = Path(str(argv[0])).name.lower()
    assert name in ("cmd.exe", "cmd", "sh", "bash"), argv
    print("[OK] shell 包装命令可被识别（供 via_shell 标记）")


# ---------- 3. 日志偏移续读 ----------

def t08_log_offset_avoids_replay():
    """从 offset 续读：不重复推送历史日志，也不丢新增。"""
    log = Path(_TMP) / "off.log"
    # 用二进制写入，避免文本模式换行转换影响期望的字节数
    log.write_bytes(b"old-1\nold-2\n")
    run = Run(id="r_off", job_name="j", job_type="COMMAND", log_path=str(log))

    got = []

    class R:
        def on_log(self, run, line, write_file=True):
            got.append(line)

    ex = Executor(R())
    pos = log.stat().st_size
    run.log_offset = pos

    # 无新增 -> 不应读任何历史
    pos2 = ex._pump_log(run, str(log), pos)
    assert got == [], f"重复推送历史日志: {got}"
    assert pos2 == pos, (pos2, pos)

    # 追加新内容 -> 只读新增
    with log.open("ab") as f:
        f.write(b"new-1\n")
    ex._pump_log(run, str(log), pos2)
    assert got == ["new-1"], got
    assert run.log_offset == log.stat().st_size
    print("[OK] 日志从 offset 续读，不重复不丢失")


def t09_log_truncation_resets():
    """日志文件被截断/轮转时应回到起点而非卡死。"""
    log = Path(_TMP) / "trunc.log"
    log.write_bytes(b"aaa\n")
    run = Run(id="r_tr", job_name="j", job_type="COMMAND", log_path=str(log))
    got = []

    class R:
        def on_log(self, run, line, write_file=True):
            got.append(line)

    ex = Executor(R())
    new_pos = ex._pump_log(run, str(log), 99999)  # offset 远超文件大小
    assert got == ["aaa"], got
    assert new_pos == log.stat().st_size, (new_pos, log.stat().st_size)
    print("[OK] 日志截断后自动复位读取")


# ---------- 4. 进程创建标志降级 ----------

def t10_detach_flag_variants_ordered():
    """detach 标志应有降级候选，避免 BREAKAWAY 权限失败导致启动不了。"""
    variants = Executor._detach_flags_variants()
    assert len(variants) >= 2, variants
    assert variants[0] & 0x01000000, "首选应包含 CREATE_BREAKAWAY_FROM_JOB"
    assert not (variants[-1] & 0x01000000), "末位应去掉 BREAKAWAY 以便降级"
    for v in variants:
        assert v & 0x00000008, "detach 应含 DETACHED_PROCESS"
        assert v & 0x00000200, "detach 应含 CREATE_NEW_PROCESS_GROUP"
    print("[OK] detach 创建标志具备权限降级候选")


def t11_non_detach_uses_no_window_only():
    """非 detach 不应带 DETACHED_PROCESS（否则控制台行为异常）。"""
    if os.name != "nt":
        print("[SKIP] 非 Windows 平台")
        return
    flags = Executor._spawn_flags(False)
    assert not (flags & 0x00000008), "非 detach 不应使用 DETACHED_PROCESS"
    print("[OK] 非 detach 只隐藏窗口")


# ---------- 5. 真实进程：日志脱离 PIPE ----------

def t12_real_process_writes_log_file():
    """真实启动一个短命进程，确认 stdout 直接落到日志文件。"""
    log = Path(_TMP) / "real.log"
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
    assert "12345" in log.read_text(encoding="utf-8", errors="replace"), log.read_text()
    assert run.exit_code == 0, (run.exit_code, run.error)
    print("[OK] 真实进程 stdout 直写日志文件（不依赖 PIPE）")


# ---------- 6. shutdown 不杀 detach ----------

def t13_shutdown_skips_detached():
    """shutdown() 源码层面必须对 detach 执行 continue。"""
    src = (Path(__file__).resolve().parent / "app" / "scheduler.py").read_text(encoding="utf-8")
    i = src.index("def shutdown(self)")
    block = src[i:i + 900]
    assert 'getattr(run, "detached", False)' in block, "shutdown 未判断 detached"
    assert "continue" in block, "shutdown 未跳过 detach"
    assert "self.abort(run_id)" in block, "shutdown 应仅中止非 detach"
    print("[OK] shutdown() 对 detach 任务跳过中止")


# ---------- 7. 停止失败不得谎报 ----------

def t14_stop_refuses_on_identity_mismatch():
    """身份核验失败必须返回拒绝，且不得声称已停止。"""
    from app.scheduler import Scheduler
    tmp = Path(_TMP) / "t14"
    tmp.mkdir(exist_ok=True)
    cfg = tmp / "jobs.json"
    cfg.write_text('{"crawler": {"items": []}}', encoding="utf-8")
    s = Scheduler(config_path=str(cfg), db_path=str(tmp / "t.db"))

    # 造一条指向「当前进程」但 create_time 不匹配的记录
    run = Run(id="fake1", job_name="j", job_type="COMMAND", status="RUNNING",
              pid=os.getpid(), process_create_time="9999999999999999999",
              detached=True)
    s.store.save_run(run)
    res = s.stop("fake1")
    assert res.get("ok") is False, res
    assert res.get("stop") is False, res
    assert "身份核验失败" in str(res.get("reason") or ""), res
    # 当前进程（测试自身）必须还活着
    assert True
    s.store.close()
    print("[OK] 身份不匹配时拒绝终止，不谎报成功")


def t15_stop_reports_unknown_run():
    """不存在的 run 必须给出明确的 run_not_found。"""
    from app.scheduler import Scheduler
    tmp = Path(_TMP) / "t15"
    tmp.mkdir(exist_ok=True)
    cfg = tmp / "jobs.json"
    cfg.write_text('{"crawler": {"items": []}}', encoding="utf-8")
    s = Scheduler(config_path=str(cfg), db_path=str(tmp / "t.db"))
    res = s.stop("nope-not-exist")
    assert res.get("reason") == "run_not_found", res
    s.store.close()
    print("[OK] 不存在的 run 返回 run_not_found")


# ---------- 8. 日志不回写自身 ----------

def t16_on_log_write_file_false_avoids_feedback():
    """detach 跟随读到的行不得再写回同一文件（否则无限膨胀）。"""
    log = Path(_TMP) / "fb.log"
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
    print("[OK] 日志跟随不写回同一文件")


def t17_job_environment_probe():
    """Job 环境探测必须返回可解释的结果，不得抛异常。"""
    r = Executor.probe_job_environment()
    for k in ("in_job", "kill_on_close", "breakaway_allowed", "can_escape"):
        assert k in r, f"缺少字段 {k}"
    # 非 Windows 应明确判定为无约束
    if os.name != "nt":
        assert r["can_escape"] is True, r
    print(f"[OK] Job 环境探测返回: {r}")


def t18_isolation_warning_field_roundtrip():
    """isolation_warning 必须可持久化往返（用于如实暴露环境约束）。"""
    r = Run(id="r_iso", job_name="j", job_type="COMMAND",
            isolation_warning="测试告警")
    d = r.to_dict()
    assert d["isolation_warning"] == "测试告警", d
    r2 = Run.from_dict(d)
    assert r2.isolation_warning == "测试告警"
    print("[OK] isolation_warning 持久化往返")


def t19_shell_orphan_sweep_exists():
    """via_shell 任务停止时必须具备孤儿清理能力（不得只清 shell）。"""
    from app.scheduler import Scheduler
    src = (Path(__file__).resolve().parent / "app" / "scheduler.py").read_text(encoding="utf-8")
    assert "_sweep_shell_orphans" in src, "缺少 shell 孤儿清理"

    # 取 stop() 的完整函数体（到下一个同级方法定义为止），不按固定长度截断，
    # 避免代码增长后断言失效（曾经因为 Host 分支变长而误报）。
    i = src.index("    def stop(self")
    rest = src[i + 10:]
    m = rest.find("\n    def ")
    block = rest[:m] if m > 0 else rest
    assert "_sweep_shell_orphans" in block, "stop() 未调用孤儿清理"
    assert 'getattr(run, "via_shell", False)' in block, "未按 via_shell 触发"

    # 清理必须按「父 pid 精确匹配」，不能全局扫
    j = src.index("def _sweep_shell_orphans")
    sweep = src[j:j + 1500]
    assert "ParentProcessId=" in sweep, "孤儿清理未按父 pid 精确匹配"
    print("[OK] via_shell 停止时具备精确的孤儿清理")


def main():
    fns = [
        t01_plain_command_no_shell,
        t02_shell_syntax_needs_shell,
        t03_quoted_semicolon_not_shell,
        t04_shell_builtin_needs_shell,
        t05_split_preserves_quoted_args,
        t06_via_shell_flag_roundtrip,
        t07_via_shell_true_for_shell_command,
        t08_log_offset_avoids_replay,
        t09_log_truncation_resets,
        t10_detach_flag_variants_ordered,
        t11_non_detach_uses_no_window_only,
        t12_real_process_writes_log_file,
        t13_shutdown_skips_detached,
        t14_stop_refuses_on_identity_mismatch,
        t15_stop_reports_unknown_run,
        t16_on_log_write_file_false_avoids_feedback,
        t17_job_environment_probe,
        t18_isolation_warning_field_roundtrip,
        t19_shell_orphan_sweep_exists,
    ]
    passed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"FAIL: {fn.__name__}: {e}")
            return 1
    print(f"\nDecouple regression: {passed}/{len(fns)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
