# -*- coding: utf-8 -*-
"""异常恢复与状态收敛"""
import os
from datetime import datetime

import pytest

from app import identity as identity_mod
from app.constants import STATUS, RUN_STATES, ALL_GROUPS
from app.models import Run, Job
from app.store import Store
from app.scheduler import Scheduler

pytestmark = pytest.mark.recovery


def _mk_run(run_id="r1", status="RUNNING", **kw):
    data = dict(id=run_id, job_name="t1", job_type="COMMAND", status=status)
    data.update(kw)
    return Run(**data)


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _old_ts(seconds_ago):
    return datetime.fromtimestamp(
        datetime.now().timestamp() - seconds_ago
    ).astimezone().isoformat(timespec="seconds")


def _new_sched(tmp_path):
    """独立 Scheduler（原测试用递增 db 名，pytest 下用 tmp_path 隔离）。"""
    cfg = tmp_path / "jobs.json"
    cfg.write_text('{"crawler": {"items": []}}', encoding="utf-8")
    return Scheduler(config_path=str(cfg), db_path=str(tmp_path / "m.db"))



def test_roundtrip_new_fields():
    """Phase 2 新增字段必须能完整序列化/反序列化。"""
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


def test_old_row_defaults():
    """Phase 1 的旧数据行缺少新字段，必须有合理默认值并做类型归一化。"""
    old = {"id": "old1", "job_name": "t", "job_type": "COMMAND", "status": "RUNNING",
           "pid": "42", "timeout_s": "5"}
    r = Run.from_dict(old)
    assert r.process_create_time is None
    assert r.node_id == "" and r.detached is False and r.adopted is False
    assert r.interrupted_reason == ""
    assert r.pid == 42 and r.timeout_s == 5


@pytest.mark.parametrize("state", ["STARTING", "INTERRUPTED", "ORPHANED", "UNKNOWN"])
def test_run_states_contain_new(state):
    """Phase 2 新增状态必须进入 STATUS 字典与 RUN_STATES 枚举。"""
    assert state in STATUS, state
    assert state in RUN_STATES, state


def test_running_runs_grouping(sched):
    """running_runs() 应返回运行类状态；running_process_count 不计 UNKNOWN。"""
    sched.store.save_run(_mk_run("o1", "ORPHANED"))
    sched.store.save_run(_mk_run("r1", "RUNNING"))
    sched.store.save_run(_mk_run("s1", "STARTING"))
    sched.store.save_run(_mk_run("u1", "UNKNOWN"))
    sched.store.save_run(_mk_run("i1", "INTERRUPTED"))

    got = {r.id for r in sched.store.running_runs()}
    assert got == {"o1", "r1", "s1", "u1"}, got
    assert sched.store.running_process_count() == 3


def test_trigger_starting_pre_registration(sched):
    """触发后应先落 STARTING 预登记，on_started 再推进 RUNNING。"""
    job = Job(name="t1", type="COMMAND", params={"command": "echo hi"})
    sched.jobs[job.name] = job
    sched.running.clear()

    run = _mk_run(status="STARTING")
    sched.on_started(run)
    assert run.status == "RUNNING"
    assert run.node_id == "unit-node"
    assert run.last_heartbeat

    assert sched.store.get_run(run.id).status == "RUNNING"


def test_residual_starting_no_identity_unknown(sched, alive_false):
    sched.store.save_run(_mk_run("st", "STARTING"))
    sched.recover_on_start()

    after = sched.store.get_run("st")
    assert after.status == "UNKNOWN", after.status
    assert after.interrupted_reason


def test_phase1_leftover_running_unknown(sched, alive_false):
    sched.store.save_run(_mk_run("legacy", "RUNNING"))
    sched.recover_on_start()
    assert sched.store.get_run("legacy").status == "UNKNOWN"


def test_dead_proc_interrupted(sched, alive_false):
    sched.store.save_run(
        _mk_run("dead1", "RUNNING", process_create_time="ct-1", pid=777, detached=True))
    sched.recover_on_start()

    after = sched.store.get_run("dead1")
    assert after.status == "INTERRUPTED", after.status
    assert after.end and after.interrupted_reason
    assert after.id not in {r.id for r in sched.store.running_runs()}


def test_alive_orphan_resume(sched, alive_true):
    sched.store.save_run(
        _mk_run("orph", "ORPHANED", process_create_time="ct-2", pid=888, detached=True))
    sched.recover_on_start()

    after = sched.store.get_run("orph")
    assert after.status == "ORPHANED", after.status
    assert after.last_heartbeat


def test_identity_alive_starting_running(sched, alive_true):
    sched.store.save_run(
        _mk_run("s2", "STARTING", process_create_time="ct-3", pid=901, detached=True))
    sched.recover_on_start()
    assert sched.store.get_run("s2").status == "RUNNING"


def test_recover_alive_running_registered_in_running_register(sched, alive_true):
    sched.store.save_run(
        _mk_run("live1", "RUNNING", process_create_time="ct-live", pid=902,
                detached=True, job_name="keepalive"))
    job = Job(name="keepalive", type="COMMAND",
              params={"command": "ping -t 127.0.0.1"}, enabled=True)
    sched.jobs[job.name] = job

    sched.recover_on_start()

    assert run_in(sched, "live1"), f"存活 RUNNING 未登记进 running（当前={list(sched.running.keys())}）"
    assert sched.running["live1"].status == "RUNNING"
    assert sched.trigger("keepalive") == "blocked", "恢复的存活实例未拦截重复触发"


def run_in(sched, run_id):
    return run_id in sched.running


def test_shutdown_skips_detached(sched):
    d = _mk_run("det1", "RUNNING", detached=True, pid=9011, process_create_time="ct-d")
    n = _mk_run("nor1", "RUNNING", detached=False, pid=9012, process_create_time="ct-n")
    sched.running["det1"] = d
    sched.running["nor1"] = n

    aborted = []
    sched.abort = lambda rid: aborted.append(rid)  # 记录 shutdown 实际调用了 abort 的 run_id
    sched.aps.shutdown = lambda wait=False: None
    sched.shutdown()

    assert "det1" not in aborted, f"独立进程(detach)不应在 shutdown 被中止: {aborted}"
    assert "nor1" in aborted, f"普通任务应在 shutdown 被中止: {aborted}"



def test_within_timeout_boundary():
    assert Scheduler._within_timeout(_old_ts(1), 5.0) is True
    assert Scheduler._within_timeout(_old_ts(100), 5.0) is False
    assert Scheduler._within_timeout("not-a-date", 5.0) is False


def test_heartbeat_timeout_dead_interrupted(sched, alive_false):
    run = _mk_run("h1", "RUNNING", process_create_time="ct-h", pid=111)
    run.last_heartbeat = _old_ts(9999)
    sched.store.save_run(run)

    sched._check_heartbeat_timeout(run)

    after = sched.store.get_run("h1")
    assert after.status == "INTERRUPTED", after.status
    assert after.interrupted_reason and after.end


def test_heartbeat_timeout_alive_no_kill(sched, alive_true):
    run = _mk_run("h2", "RUNNING", process_create_time="ct-h2", pid=222)
    run.last_heartbeat = _old_ts(9999)
    sched.store.save_run(run)

    sched._check_heartbeat_timeout(run)

    assert sched.store.get_run("h2").status == "RUNNING"


def test_safe_kill_identity_mismatch_refusal(monkeypatch):
    run = _mk_run("sk1", "RUNNING", pid=999999, process_create_time="nope-ct")
    monkeypatch.setattr(identity_mod, "get_process_create_time", lambda pid: "different-ct")

    reason = identity_mod.safe_kill(run, 999999)
    assert reason is not None, "身份不匹配必须拒绝"
    assert "核验失败" in reason or "拒绝" in reason, reason


def test_safe_kill_no_credential():
    run = _mk_run("sk2", "RUNNING", pid=5, process_create_time=None)
    reason = identity_mod.safe_kill(run, 5)
    assert reason is not None, "无凭据必须拒绝"


def test_safe_kill_no_pid():
    run = _mk_run("sk3", "RUNNING", pid=None)
    reason = identity_mod.safe_kill(run)
    assert reason is not None and "PID" in reason

def test_adopt_orphan_all_questions_pass(sched, tmp_path, monkeypatch):
    logp = tmp_path / "t40.log"
    logp.write_text("running...\n", encoding="utf-8")
    sched.store.save_run(
        _mk_run("adp", "ORPHANED", pid=555, process_create_time="adopt-ct",
                detached=True, log_path=str(logp)))

    monkeypatch.setattr(identity_mod, "get_process_create_time", lambda pid: "adopt-ct")
    monkeypatch.setattr(identity_mod, "process_is_alive", lambda run, pid=None: True)

    sched.recover_on_start()

    after = sched.store.get_run("adp")
    assert after.status == "RUNNING", after.status
    assert after.adopted is True


def test_adopt_orphan_no_log_anchor(sched, monkeypatch):
    sched.store.save_run(
        _mk_run("adp2", "ORPHANED", pid=556, process_create_time="adopt-ct2",
                detached=True))

    monkeypatch.setattr(identity_mod, "get_process_create_time", lambda pid: "adopt-ct2")
    monkeypatch.setattr(identity_mod, "process_is_alive", lambda run, pid=None: True)

    sched.recover_on_start()

    after = sched.store.get_run("adp2")
    assert after.status == "ORPHANED", after.status
    assert after.adopted is False
    assert after.interrupted_reason and "四问" in after.interrupted_reason


def test_adopt_orphan_identity_mismatch(sched, tmp_path, monkeypatch):
    logp = tmp_path / "t42.log"
    logp.write_text("x\n", encoding="utf-8")
    sched.store.save_run(
        _mk_run("adp3", "ORPHANED", pid=557, process_create_time="expected-ct",
                detached=True, log_path=str(logp)))

    monkeypatch.setattr(identity_mod, "get_process_create_time", lambda pid: "OTHER-ct")
    monkeypatch.setattr(identity_mod, "process_is_alive", lambda run, pid=None: True)

    sched.recover_on_start()

    after = sched.store.get_run("adp3")
    assert after.status == "ORPHANED", after.status
    assert after.adopted is False


def test_safe_first_no_auto_restart(sched, alive_true):
    sched.store.save_run(
        _mk_run("sf1", "INTERRUPTED", process_create_time="ct-sf", pid=1,
                interrupted_reason="旧中断"))

    sched.recover_on_start()

    after = sched.store.get_run("sf1")
    assert after.status == "INTERRUPTED"
    assert after.retryable is False
    assert "sf1" not in {r.id for r in sched.store.running_runs()}


def test_safe_first_unknown_not_counted(sched):
    sched.store.save_run(_mk_run("unk1", "UNKNOWN"))
    assert sched.store.running_process_count() == 0


def test_running_view_grouping(sched):
    sched.store.save_run(_mk_run("g_o", "ORPHANED"))
    sched.store.save_run(_mk_run("g_r", "RUNNING"))
    sched.store.save_run(_mk_run("g_s", "STARTING"))
    sched.store.save_run(_mk_run("g_u", "UNKNOWN"))
    sched.store.save_run(_mk_run("g_i", "INTERRUPTED"))

    v = sched.running_view(limit=100)

    assert v["alive_total"] == 3, v["alive_total"]
    counts = v["counts"]
    assert counts.get("RUNNING") == 1 and counts.get("ORPHANED") == 1
    assert counts.get("UNKNOWN") == 1 and counts.get("STARTING") == 1
    assert counts.get("INTERRUPTED", 0) == 0

    ids = {d["id"] for d in v["items"]}
    assert "g_i" not in ids and "g_o" in ids and "g_u" in ids

    sample = next(d for d in v["items"] if d["id"] == "g_r")
    assert "node_id" in sample and "adopted" in sample


@pytest.fixture
def capture_broadcast(monkeypatch):
    from app.scheduler.events import EventBus

    events = []

    def _capture(self, data):
        events.append(data)

    monkeypatch.setattr(EventBus, "broadcast", _capture)
    monkeypatch.setattr(Scheduler, "broadcast", lambda self, data: events.append(data))
    return events


def test_status_change_broadcast_fires(sched, capture_broadcast, alive_false):
    run = _mk_run("ss1", "RUNNING", process_create_time="ct-ss", pid=333)
    run.last_heartbeat = _old_ts(9999)
    sched.store.save_run(run)

    sched._check_heartbeat_timeout(run)

    assert sched.store.get_run("ss1").status == "INTERRUPTED"
    sc = [e for e in capture_broadcast if e.get("event") == "status_changed"]
    assert sc, "INTERRUPTED 收敛应广播 status_changed"
    assert sc[-1]["run_id"] == "ss1" and sc[-1]["status"] == "INTERRUPTED"


def test_starting_no_cred_broadcast(sched, capture_broadcast, alive_false):
    sched.store.save_run(_mk_run("ss2", "STARTING"))

    sched.recover_on_start()

    assert sched.store.get_run("ss2").status == "UNKNOWN"
    sc = [e for e in capture_broadcast if e.get("event") == "status_changed"]
    assert any(e["run_id"] == "ss2" for e in sc)
