# -*- coding: utf-8 -*-
"""Phase 2 · 状态一致性 & 异常恢复 —— 核心模块单测（Module 1-4）.

(完整 10 项验收在 Module 5/6/7 之后补齐到本文件的 ''acceptance'' 段)

运行：
    C:\\Users\\bzds\\miniforge3\\python.exe test_phase2_recovery.py

覆盖：
  M1 身份字段 / STARTING 预登记：
     - Run 新增字段 to_dict/from_dict 往返（含旧库缺省回填）
     - running_runs() 分组（含 UNKNOWN、排除 INTERRUPTED）
     - running_process_count() 只计 STARTING/RUNNING/ORPHANED，排除 UNKNOWN
     - _trigger_job 先写 STARTING 意图、on_started 推进 RUNNING
  M2 启动恢复：
     - recover_on_start：无身份凭据的残留 STARTING -> UNKNOWN（绝不盲杀）
     - Phase-1 遗留无凭据 RUNNING -> UNKNOWN
     - 有身份且仍存活的 ORPHANED -> 保持/接管；STARTING(有身份存活) -> RUNNING
     - 进程已确认死亡的 RUNNING/ORPHANED -> INTERRUPTED 归历史
  M3 心跳/核查：
     - _within_timeout 边界判定
     - 连续超时 + OS 探针确认死亡 -> INTERRUPTED
     - 超时但 OS 探针仍存活（身份匹配）-> 仅刷新心跳，不判死
  M4 安全停止：
     - safe_kill：PID/身份不匹配时拒绝（绝不盲杀）
     - 无 PID/无凭据时拒绝
"""
import os
import sys
import tempfile
import time
from datetime import datetime

# 在导入 app 之前配置临时库，避免污染真实 data/
_TMP = tempfile.mkdtemp(prefix="sc_ph2_")
os.environ.setdefault("SC_NODE_ID", "unit-node")
os.environ["SC_HEARTBEAT_INTERVAL_SEC"] = "2"
os.environ["SC_HEARTBEAT_TIMEOUT_TIMES"] = "2"
os.environ["SC_HEARTBEAT_BATCH_SEC"] = "60"  # 测试里手动落库为准，合并写不干扰断言

from app import identity as identity_mod  # noqa: E402
from app.constants import STATUS, RUN_STATES, ALL_GROUPS  # noqa: E402
from app.models import Run, Job  # noqa: E402
from app.store import Store  # noqa: E402
from app.scheduler import Scheduler  # noqa: E402


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


# ============================================================
# M1 · 身份字段 / STARTING 预登记
# ============================================================
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
    # 模拟 Phase-1 旧数据：没有新字段的 dict
    old = {"id": "old1", "job_name": "t", "job_type": "COMMAND", "status": "RUNNING",
           "pid": "42", "timeout_s": "5"}  # 旧的字符串 pid/timeout 也要被归一化
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
    assert st.running_process_count() == 3  # UNKNOWN 不计
    print("[OK] M1 running_runs 分组 + 进程数计 UNKNOWN 除外")


def t05_trigger_starting_pre_registration():
    db = os.path.join(_TMP, "t05.db")
    s = Scheduler(config_path="config/jobs.json", db_path=db)
    job = _job()
    s.jobs[job.name] = job
    # 不真正起动后台 aps/heartbeat；直接走触发注册路径
    s.running.clear()
    # 直接调 on_started，模拟 executor spawn 后登记：先改 STARTING 再推进
    run = _mk_run(status="STARTING")
    s.on_started(run)
    assert run.status == "RUNNING"
    assert run.node_id == "unit-node"
    assert run.last_heartbeat
    from_store = s.store.get_run(run.id)
    assert from_store.status == "RUNNING"
    print("[OK] M1 STARTING 预登记 -> on_started 推进 RUNNING")


# ============================================================
# M2 · 启动恢复
# ============================================================
def _new_sched():
    db = os.path.join(_TMP, "m2_" + str(time.time_ns()) + ".db")
    return Scheduler(config_path="config/jobs.json", db_path=db)


def t10_residual_starting_no_identity_unknown():
    s = _new_sched()
    run = _mk_run("st", "STARTING")  # 无 process_create_time
    s.store.save_run(run)
    orig = _patch_alive(False)
    try:
        s.recover_on_start()
    finally:
        identity_mod.process_is_alive = orig
    after = s.store.get_run("st")
    # 绝不盲杀、绝不算存活，转 UNKNOWN 待核查
    assert after.status == "UNKNOWN", after.status
    assert after.interrupted_reason
    print("[OK] M2 残留 STARTING(无身份) -> UNKNOWN")


def t11_phase1_leftover_running_unknown():
    s = _new_sched()
    run = _mk_run("legacy", "RUNNING")  # Phase-1 遗留，无身份凭据
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
    orig = _patch_alive(False)  # 探针确认已死
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
    orig = _patch_alive(True)  # 身份匹配 & 存活
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


# ============================================================
# M3 · 心跳 / 核查
# ============================================================
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
    # 制造"最后心跳"极旧，触发超时
    run.last_heartbeat = datetime.fromtimestamp(
        datetime.now().timestamp() - 9999).astimezone().isoformat(timespec="seconds")
    s.store.save_run(run)
    orig = _patch_alive(False)  # 探针确认死亡
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
    orig = _patch_alive(True)  # 探针仍确认存活
    try:
        s._check_heartbeat_timeout(run)
    finally:
        identity_mod.process_is_alive = orig
    after = s.store.get_run("h2")
    # 只弹心跳，不判死、不改状态
    assert after.status == "RUNNING", after.status
    print("[OK] M3 心跳超时但探针存活 -> 不判死")


# ============================================================
# M4 · 安全停止
# ============================================================
def t30_safe_kill_identity_mismatch_refusal():
    run = _mk_run("sk1", "RUNNING", pid=999999, process_create_time="nope-ct")
    # 任何 pid 上都不可能匹配到 "nope-ct"，必须拒绝，绝不盲杀
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


# ============================================================
# M5 · detach 四问接管（修订版3 §6.1）
# ============================================================
def t40_adopt_orphan_all_questions_pass():
    s = _new_sched()
    logp = os.path.join(_TMP, "t40.log")
    with open(logp, "w", encoding="utf-8") as f:
        f.write("running...\n")
    run = _mk_run("adp", "ORPHANED", pid=555, process_create_time="adopt-ct",
                  detached=True, log_path=logp)
    s.store.save_run(run)
    # 身份核验要"匹配"：monkeypatch 该 run 的自有 pid 创建时刻为已存值
    # 方案：让 get_process_create_time 对 pid 返回 已存 token，verify_identity 成立
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
    # 无 log_path -> 问题3(日志锚点)不过 -> 保持 ORPHANED
    run = _mk_run("adp2", "ORPHANED", pid=556, process_create_time="adopt-ct2",
                  detached=True)  # log_path 缺省 None
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
    # 身份不匹配（PID 复用）：拒绝接管，保持 ORPHANED
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


# ============================================================
# M7 · SAFE-FIRST（§8.2）：绝不幂等自动重试，异常保持终态
# ============================================================
def t50_safe_first_no_auto_restart():
    s = _new_sched()
    # 已 INTERRUPTED 的历史记录卷入 running_runs? 不会（running_runs 排除 INTERRUPTED）。
    # 这里验证：恢复扫描只收敛不重启，且 retryable 本轮不生效(默认 False)。
    run = _mk_run("sf1", "INTERRUPTED", process_create_time="ct-sf", pid=1,
                  interrupted_reason="旧中断")
    s.store.save_run(run)
    orig = _patch_alive(True)  # 即便探针谎报存活，INTERRUPTED 也绝不复活为 RUNNING
    try:
        # INTERRUPTED 本就不在 running_runs 候选；recover 只针对 running 态
        s.recover_on_start()
    finally:
        identity_mod.process_is_alive = orig
    assert s.store.get_run("sf1").status == "INTERRUPTED"
    assert s.store.get_run("sf1").retryable is False
    assert "sf1" not in {r.id for r in s.store.running_runs()}
    print("[OK] M7 SAFE-FIRST：INTERRUPTED 不复活/不计运行/retryable 预留不生效")


def t51_safe_first_unknown_not_counted():
    s = _new_sched()
    # UNKNOWN 只是"待核查"，绝不能凭心跳/记录断言它"在跑"
    s.store.save_run(_mk_run("unk1", "UNKNOWN"))
    assert s.store.running_process_count() == 0
    print("[OK] M7 SAFE-FIRST：UNKNOWN 不计入实际运行进程数")


def t60_running_view_grouping():
    s = _new_sched()
    s.store.save_run(_mk_run("g_o", "ORPHANED"))
    s.store.save_run(_mk_run("g_r", "RUNNING"))
    s.store.save_run(_mk_run("g_s", "STARTING"))
    s.store.save_run(_mk_run("g_u", "UNKNOWN"))
    s.store.save_run(_mk_run("g_i", "INTERRUPTED"))  # 应归历史，不进运行表
    v = s.running_view(limit=100)
    assert v["alive_total"] == 3, v["alive_total"]  # RUNNING+ORPHANED+STARTING，UNKNOWN 不计
    counts = v["counts"]
    assert counts.get("RUNNING") == 1 and counts.get("ORPHANED") == 1
    assert counts.get("UNKNOWN") == 1 and counts.get("STARTING") == 1
    assert counts.get("INTERRUPTED", 0) == 0
    ids = {d["id"] for d in v["items"]}
    assert "g_i" not in ids and "g_o" in ids and "g_u" in ids
    # 返回给前端的 items 必须含 Phase2 身份字段
    sample = next(d for d in v["items"] if d["id"] == "g_r")
    assert "node_id" in sample and "adopted" in sample
    print("[OK] running_view 分组 + alive_total 口径 + Phase2 字段透出")


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
    except Exception:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        sys.exit(1)