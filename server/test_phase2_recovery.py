# -*- coding: utf-8 -*-

import os
import sys
import tempfile
import time
from datetime import datetime

_TMP = tempfile.mkdtemp(prefix="sc_ph2_")
os.environ.setdefault("SC_NODE_ID", "unit-node")
os.environ["SC_HEARTBEAT_INTERVAL_SEC"] = "2"
os.environ["SC_HEARTBEAT_TIMEOUT_TIMES"] = "2"
os.environ["SC_HEARTBEAT_BATCH_SEC"] = "60"

from app import identity as identity_mod
from app.constants import STATUS, RUN_STATES, ALL_GROUPS
from app.models import Run, Job
from app.store import Store
from app.scheduler import Scheduler


def _job(name="t1"):
    return Job(name=name, type="COMMAND", params={"command": "echo hi"})


def _mk_run(run_id="r1", status="RUNNING", **kw):
    d = dict(id=run_id, job_name="t1", job_type="COMMAND", status=status)
    d.update(kw)
    return Run(**d)


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _patch_alive(v):
    """monkeypatch 模块级探针（scheduler 以 identity_mod.process_is_alive 属性访问）."""
    orig = identity_mod.process_is_alive
    identity_mod.process_is_alive = lambda run, pid=None: v
    return orig

def t01_roundtrip_new_fields():
    run = _mk_run(status="STARTING", detached=True, pid=1234,
                  process_create_time="FILETIME-x", executable_path="C:/a.exe",
                  command_line_fingerprint="abc123", process_group_id=9,
                  last_heartbeat=_now(), interrupted_reason="测试", adopted=True)
    d = run.to_dict(include_log=True)
    for k in ("node_id", "process_create_time", "executable_path",
              "command_line_fingerprint", "process_group_id", "detached",
              "last_heartbeat", "interrupted_reason", "adopted", "retryable"):
        assert k in d, f"to_dict 缺字段 {k}"
    r2 = Run.from_dict(d)
    assert r2.process_create_time == run.process_create_time
    assert r2.process_group_id == 9
    assert r2.detached is True and r2.adopted is True
    assert r2.interrupted_reason == "测试"
    print("[OK] M1 新字段 roundtrip")


def t02_old_row_defaults():
    old = {"id": "old1", "job_name": "t", "job_type": "COMMAND", "status": "RUNNING",
           "pid": "42", "timeout_s": "5"}
    r = Run.from_dict(old)
    assert r.process_create_time is None
    assert r.node_id == "" and r.detached is False and r.adopted is False
    assert r.interrupted_reason == ""
    assert r.pid == 42 and r.timeout_s == 5
    print("[OK] M1 旧行缺省回填 + 兼容归一化")


def t03_run_states_contain_new():
    for s in ("STARTING", "INTERRUPTED", "ORPHANED", "UNKNOWN"):
        assert s in STATUS and s in RUN_STATES
    assert "RUNNING" in RUN_STATES
    print("[OK] M1 新状态已入 STATUS / RUN_STATES")


def t04_running_runs_grouping():
    db = os.path.join(_TMP, "t04.db")
    st = Store(db)
    st.save_run(_mk_run("o1", "ORPHANED"))
    st.save_run(_mk_run("r1", "RUNNING"))
    st.save_run(_mk_run("s1", "STARTING"))
    st.save_run(_mk_run("u1", "UNKNOWN"))
    st.save_run(_mk_run("i1", "INTERRUPTED"))
    got = {r.id for r in st.running_runs()}
    assert got == {"o1", "r1", "s1", "u1"}, got
    assert st.running_process_count() == 3
    print("[OK] M1 running_runs 分组 + 进程数计 UNKNOWN 除外")


def t05_trigger_starting_pre_registration():
    db = os.path.join(_TMP, "t05.db")
    s = Scheduler(config_path="config/jobs.json", db_path=db)
    job = _job()
    s.jobs[job.name] = job
    s.running.clear()
    run = _mk_run(status="STARTING")
    s.on_started(run)
    assert run.status == "RUNNING"
    assert run.node_id == "unit-node"
    assert run.last_heartbeat
    from_store = s.store.get_run(run.id)
    assert from_store.status == "RUNNING"
    print("[OK] M1 STARTING 预登记 -> on_started 推进 RUNNING")

def _new_sched():
    db = os.path.join(_TMP, "m2_" + str(time.time_ns()) + ".db")
    return Scheduler(config_path="config/jobs.json", db_path=db)


def t10_residual_starting_no_identity_unknown():
    s = _new_sched()
    run = _mk_run("st", "STARTING")
    s.store.save_run(run)
    orig = _patch_alive(False)
    try:
        s.recover_on_start()
    finally:
        identity_mod.process_is_alive = orig
    after = s.store.get_run("st")
    assert after.status == "UNKNOWN", after.status
    assert after.interrupted_reason
    print("[OK] M2 残留 STARTING(无身份) -> UNKNOWN")


def t11_phase1_leftover_running_unknown():
    s = _new_sched()
    run = _mk_run("legacy", "RUNNING")
    s.store.save_run(run)
    orig = _patch_alive(False)
    try:
        s.recover_on_start()
    finally:
        identity_mod.process_is_alive = orig
    assert s.store.get_run("legacy").status == "UNKNOWN"
    print("[OK] M2 无身份凭据 RUNNING 遗留 -> UNKNOWN")


def t12_dead_proc_interrupted():
    s = _new_sched()
    run = _mk_run("dead1", "RUNNING", process_create_time="ct-1", pid=777, detached=True)
    s.store.save_run(run)
    orig = _patch_alive(False)
    try:
        s.recover_on_start()
    finally:
        identity_mod.process_is_alive = orig
    after = s.store.get_run("dead1")
    assert after.status == "INTERRUPTED", after.status
    assert after.end and after.interrupted_reason
    assert after.id not in {r.id for r in s.store.running_runs()}
    print("[OK] M2 探针确认已死 -> INTERRUPTED 归历史")


def t13_alive_orphan_resume():
    s = _new_sched()
    run = _mk_run("orph", "ORPHANED", process_create_time="ct-2", pid=888, detached=True)
    s.store.save_run(run)
    orig = _patch_alive(True)
    try:
        s.recover_on_start()
    finally:
        identity_mod.process_is_alive = orig
    after = s.store.get_run("orph")
    assert after.status == "ORPHANED", after.status
    assert after.last_heartbeat
    print("[OK] M2 存活 ORPHANED -> 继续管理(ORPHANED)")


def t14_identity_alive_starting_running():
    s = _new_sched()
    run = _mk_run("s2", "STARTING", process_create_time="ct-3", pid=901, detached=True)
    s.store.save_run(run)
    orig = _patch_alive(True)
    try:
        s.recover_on_start()
    finally:
        identity_mod.process_is_alive = orig
    assert s.store.get_run("s2").status == "RUNNING"
    print("[OK] M2 有身份且存活的 STARTING -> RUNNING")

def t20_within_timeout():
    S = Scheduler
    old = (datetime.now().astimezone().timestamp() - 1)
    ts = datetime.fromtimestamp(old).astimezone().isoformat(timespec="seconds")
    assert S._within_timeout(ts, 5.0) is True
    ts2 = datetime.fromtimestamp(old - 100).astimezone().isoformat(timespec="seconds")
    assert S._within_timeout(ts2, 5.0) is False
    assert S._within_timeout("not-a-date", 5.0) is False
    print("[OK] M3 _within_timeout 边界")


def t21_heartbeat_timeout_dead_interrupted():
    s = _new_sched()
    run = _mk_run("h1", "RUNNING", process_create_time="ct-h", pid=111)
    run.last_heartbeat = datetime.fromtimestamp(
        datetime.now().timestamp() - 9999).astimezone().isoformat(timespec="seconds")
    s.store.save_run(run)
    orig = _patch_alive(False)
    try:
        s._check_heartbeat_timeout(run)
    finally:
        identity_mod.process_is_alive = orig
    after = s.store.get_run("h1")
    assert after.status == "INTERRUPTED", after.status
    assert after.interrupted_reason and after.end
    print("[OK] M3 心跳超时+探针死亡 -> INTERRUPTED")


def t22_heartbeat_timeout_alive_no_kill():
    s = _new_sched()
    run = _mk_run("h2", "RUNNING", process_create_time="ct-h2", pid=222)
    run.last_heartbeat = datetime.fromtimestamp(
        datetime.now().timestamp() - 9999).astimezone().isoformat(timespec="seconds")
    s.store.save_run(run)
    orig = _patch_alive(True)
    try:
        s._check_heartbeat_timeout(run)
    finally:
        identity_mod.process_is_alive = orig
    after = s.store.get_run("h2")
    assert after.status == "RUNNING", after.status
    print("[OK] M3 心跳超时但探针存活 -> 不判死")

def t30_safe_kill_identity_mismatch_refusal():
    run = _mk_run("sk1", "RUNNING", pid=999999, process_create_time="nope-ct")
    old = identity_mod.get_process_create_time
    identity_mod.get_process_create_time = lambda pid: "different-ct"
    try:
        reason = identity_mod.safe_kill(run, 999999)
    finally:
        identity_mod.get_process_create_time = old
    assert reason is not None, "身份不匹配必须拒绝"
    assert "核验失败" in reason or "拒绝" in reason, reason
    print("[OK] M4 身份不匹配 -> 拒绝盲杀")


def t31_safe_kill_no_credential():
    run = _mk_run("sk2", "RUNNING", pid=5, process_create_time=None)
    reason = identity_mod.safe_kill(run, 5)
    assert reason is not None, "无凭据必须拒绝"
    print("[OK] M4 无身份凭据 -> 拒绝")


def t32_safe_kill_no_pid():
    run = _mk_run("sk3", "RUNNING", pid=None)
    reason = identity_mod.safe_kill(run)
    assert reason is not None and "PID" in reason
    print("[OK] M4 无 PID -> 拒绝")


def t40_adopt_orphan_all_questions_pass():
    s = _new_sched()
    logp = os.path.join(_TMP, "t40.log")
    with open(logp, "w", encoding="utf-8") as f:
        f.write("running...\n")
    run = _mk_run("adp", "ORPHANED", pid=555, process_create_time="adopt-ct",
                  detached=True, log_path=logp)
    s.store.save_run(run)
    orig_ct = identity_mod.get_process_create_time
    identity_mod.get_process_create_time = lambda pid: "adopt-ct"
    orig_alive = identity_mod.process_is_alive
    identity_mod.process_is_alive = lambda run, pid=None: True
    try:
        s.recover_on_start()
    finally:
        identity_mod.get_process_create_time = orig_ct
        identity_mod.process_is_alive = orig_alive
    after = s.store.get_run("adp")
    assert after.status == "RUNNING", after.status
    assert after.adopted is True
    print("[OK] M5 四问全过 -> ORPHANED 接管为 RUNNING(adopted)")


def t41_adopt_orphan_no_log_anchor():
    s = _new_sched()
    run = _mk_run("adp2", "ORPHANED", pid=556, process_create_time="adopt-ct2",
                  detached=True)
    s.store.save_run(run)
    orig_ct = identity_mod.get_process_create_time
    identity_mod.get_process_create_time = lambda pid: "adopt-ct2"
    orig_alive = identity_mod.process_is_alive
    identity_mod.process_is_alive = lambda run, pid=None: True
    try:
        s.recover_on_start()
    finally:
        identity_mod.get_process_create_time = orig_ct
        identity_mod.process_is_alive = orig_alive
    after = s.store.get_run("adp2")
    assert after.status == "ORPHANED", after.status
    assert after.adopted is False
    assert after.interrupted_reason and "四问" in after.interrupted_reason
    print("[OK] M5 问题3(日志锚点)不过 -> 保持 ORPHANED(adopted=False)")


def t42_adopt_orphan_identity_mismatch():
    s = _new_sched()
    logp = os.path.join(_TMP, "t42.log")
    with open(logp, "w", encoding="utf-8") as f:
        f.write("x\n")
    run = _mk_run("adp3", "ORPHANED", pid=557, process_create_time="expected-ct",
                  detached=True, log_path=logp)
    s.store.save_run(run)
    orig_ct = identity_mod.get_process_create_time
    identity_mod.get_process_create_time = lambda pid: "OTHER-ct"
    orig_alive = identity_mod.process_is_alive
    identity_mod.process_is_alive = lambda run, pid=None: True
    try:
        s.recover_on_start()
    finally:
        identity_mod.get_process_create_time = orig_ct
        identity_mod.process_is_alive = orig_alive
    after = s.store.get_run("adp3")
    assert after.status == "ORPHANED", after.status
    assert after.adopted is False
    print("[OK] M5 身份不匹配(PID 复用) -> 拒绝接管，保持 ORPHANED")

def t50_safe_first_no_auto_restart():
    s = _new_sched()
    run = _mk_run("sf1", "INTERRUPTED", process_create_time="ct-sf", pid=1,
                  interrupted_reason="旧中断")
    s.store.save_run(run)
    orig = _patch_alive(True)
    try:
        s.recover_on_start()
    finally:
        identity_mod.process_is_alive = orig
    assert s.store.get_run("sf1").status == "INTERRUPTED"
    assert s.store.get_run("sf1").retryable is False
    assert "sf1" not in {r.id for r in s.store.running_runs()}
    print("[OK] M7 SAFE-FIRST：INTERRUPTED 不复活/不计运行/retryable 预留不生效")


def t51_safe_first_unknown_not_counted():
    s = _new_sched()
    s.store.save_run(_mk_run("unk1", "UNKNOWN"))
    assert s.store.running_process_count() == 0
    print("[OK] M7 SAFE-FIRST：UNKNOWN 不计入实际运行进程数")


def t60_running_view_grouping():
    s = _new_sched()
    s.store.save_run(_mk_run("g_o", "ORPHANED"))
    s.store.save_run(_mk_run("g_r", "RUNNING"))
    s.store.save_run(_mk_run("g_s", "STARTING"))
    s.store.save_run(_mk_run("g_u", "UNKNOWN"))
    s.store.save_run(_mk_run("g_i", "INTERRUPTED"))
    v = s.running_view(limit=100)
    assert v["alive_total"] == 3, v["alive_total"]
    counts = v["counts"]
    assert counts.get("RUNNING") == 1 and counts.get("ORPHANED") == 1
    assert counts.get("UNKNOWN") == 1 and counts.get("STARTING") == 1
    assert counts.get("INTERRUPTED", 0) == 0
    ids = {d["id"] for d in v["items"]}
    assert "g_i" not in ids and "g_o" in ids and "g_u" in ids
    sample = next(d for d in v["items"] if d["id"] == "g_r")
    assert "node_id" in sample and "adopted" in sample
    print("[OK] running_view 分组 + alive_total 口径 + Phase2 字段透出")


def t70_status_change_broadcast_fires():
    s = _new_sched()
    events = []
    orig_broadcast = Scheduler.broadcast
    Scheduler.broadcast = lambda self, data: events.append(data)
    try:
        run = _mk_run("ss1", "RUNNING", process_create_time="ct-ss", pid=333)
        run.last_heartbeat = datetime.fromtimestamp(
            datetime.now().timestamp() - 9999).astimezone().isoformat(timespec="seconds")
        s.store.save_run(run)
        orig = _patch_alive(False)
        try:
            s._check_heartbeat_timeout(run)
        finally:
            identity_mod.process_is_alive = orig
        assert s.store.get_run("ss1").status == "INTERRUPTED"
        sc = [e for e in events if e.get("event") == "status_changed"]
        assert sc, "INTERRUPTED 收敛应广播 status_changed"
        assert sc[-1]["run_id"] == "ss1" and sc[-1]["status"] == "INTERRUPTED"
        print("[OK] M-SSE 状态迁移广播 status_changed(INTERRUPTED) 触发")
    finally:
        Scheduler.broadcast = orig_broadcast


def t71_starting_no_cred_broadcast():
    s = _new_sched()
    events = []
    orig_broadcast = Scheduler.broadcast
    Scheduler.broadcast = lambda self, data: events.append(data)
    try:
        run = _mk_run("ss2", "STARTING")
        s.store.save_run(run)
        orig = _patch_alive(False)
        try:
            s.recover_on_start()
        finally:
            identity_mod.process_is_alive = orig
        assert s.store.get_run("ss2").status == "UNKNOWN"
        sc = [e for e in events if e.get("event") == "status_changed"]
        assert any(e["run_id"] == "ss2" for e in sc)
        print("[OK] M-SSE 状态迁移广播 status_changed(UNKNOWN) 触发")
    finally:
        Scheduler.broadcast = orig_broadcast


def _main():
    checks = [
        t01_roundtrip_new_fields, t02_old_row_defaults, t03_run_states_contain_new,
        t04_running_runs_grouping, t05_trigger_starting_pre_registration,
        t10_residual_starting_no_identity_unknown, t11_phase1_leftover_running_unknown,
        t12_dead_proc_interrupted, t13_alive_orphan_resume, t14_identity_alive_starting_running,
        t20_within_timeout, t21_heartbeat_timeout_dead_interrupted,
        t22_heartbeat_timeout_alive_no_kill,
        t30_safe_kill_identity_mismatch_refusal, t31_safe_kill_no_credential,
        t32_safe_kill_no_pid,
        t40_adopt_orphan_all_questions_pass, t41_adopt_orphan_no_log_anchor,
        t42_adopt_orphan_identity_mismatch,
        t50_safe_first_no_auto_restart, t51_safe_first_unknown_not_counted,
        t60_running_view_grouping,
        t70_status_change_broadcast_fires, t71_starting_no_cred_broadcast,
    ]
    passed = 0
    for fn in checks:
        fn()
        passed += 1
        print(f"    ├─ PASS {fn.__name__}")
    print(f"\nPhase2-M1~M7: {passed}/{len(checks)} checks passed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(_main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)