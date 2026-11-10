# -*- coding: utf-8 -*-
import json
import os
import sys
import tempfile


def main():
    from app import constants
    from app.models import Job, Run, JOB_PARAM_SCHEMA
    from app.store import Store
    from app.executor import Executor

    # 1) 常量分组齐全（含新增 FILEOP_ACTION / EXECUTER）
    groups = list(constants.ALL_GROUPS)
    expected = ["STATUS", "JOB_TYPE", "TRIGGER", "RESULT", "ENABLED", "FILEOP_ACTION", "EXECUTER"]
    assert groups == expected, groups
    print("[OK] constants groups:", groups)

    # 2) 类型化 params 解析
    prog = Job.from_dict({
        "name": "打开记事本", "type": "script",
        "params": {"path": "scripts/hello.ps1", "detach": True},
    })
    assert prog.type == "SCRIPT", prog.type
    assert prog.params.get("path") == "scripts/hello.ps1"
    assert prog.params.get("detach") is True
    print("[OK] typed params ->", prog.type, prog.params)

    legacy = Job.from_dict({
        "name": "旧命令", "type": "COMMAND",
        "command": "echo a",
    })
    assert legacy.params.get("command") == "echo a"
    print("[OK] legacy flat -> params:", legacy.params)

    r = Run(id="1", job_name="t", job_type="COMMAND", status="PENDING")
    d = r.to_dict(include_log=True)
    r2 = Run.from_dict(d)
    assert r2.id == "1"
    print("[OK] from_dict roundtrip")

    tmp = tempfile.mkdtemp(prefix="sched_fp_")
    demo = os.path.join(tmp, "demo.txt")
    with open(demo, "w", encoding="utf-8") as f:
        f.write("x")
    assert os.path.exists(demo)
    class FakeRunner:
        def on_started(self, run): pass
        def on_log(self, run, line): run.append_log(line)
        def on_finished(self, run): pass
    ex = Executor(FakeRunner())
    fp_job = Job.from_dict({
        "name": "删demo", "type": "FILEOP",
        "params": {"action": "delete_file", "source": demo, "recursive": False},
    })
    run = Run(id="fileop1", job_name="删demo", job_type="FILEOP", status="PENDING")
    ex.run(run, fp_job)
    assert run.status == "SUCCEEDED", run.status
    assert not os.path.exists(demo), "文件应已被删除"
    assert run.log and "已删除" in run.log[0], run.log
    print("[OK] FILEOP delete ->", run.status, "| log:", run.log)
    os.rmdir(tmp)

    assert set(JOB_PARAM_SCHEMA.keys()) == {"COMMAND", "SCRIPT", "PROGRAM", "FILEOP"}
    print("[OK] param schema types:", list(JOB_PARAM_SCHEMA.keys()))

    from app.scheduler import Scheduler
    s = Scheduler(config_path="config/jobs.json", db_path=tempfile.mktemp(suffix=".db"))
    errs = s.load_config()
    assert not errs, errs
    gv = s.groups_view()
    keys = [g["key"] for g in gv]
    assert "system-commands" in keys and "applications" in keys and "file-operations" in keys, keys
    assert all(g.get("count") >= 1 for g in gv), gv
    np_job = s.jobs.get("打开-记事本")
    assert np_job and np_job.group == "applications", np_job
    fp_job = s.jobs.get("示例-删除文件")
    assert fp_job and fp_job.params["action"] in constants.FILEOP_ACTION, fp_job.params
    print("[OK] grouped config ->", len(s.jobs), "jobs /", len(gv), "groups")

    r = s.jobs_search(job_type="COMMAND", q="示例", offset=0, limit=10)
    assert r["total"] >= 1, r
    assert all(j["type"] == "COMMAND" for j in r["items"]), r["items"]
    r2 = s.jobs_search(job_type="PROGRAM", offset=0, limit=10)
    assert r2["total"] >= 1, r2
    r3 = s.jobs_search(job_type="COMMAND", offset=0, limit=1)
    assert len(r3["items"]) == 1, r3
    print(f"[OK] jobs_search -> COMMAND('示例')={r['total']}, PROGRAM={r2['total']}, page-size=1 ok")
    hv = s.history_view(offset=0, limit=5)
    assert "total" in hv and isinstance(hv["items"], list), hv
    print("[OK] history_view -> total=%s items=%d" % (hv["total"], len(hv["items"])))

    print("\nSUCCESS")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\nFAILURE: {e}")
        sys.exit(1)