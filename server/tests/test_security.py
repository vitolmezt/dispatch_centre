# -*- coding: utf-8 -*-
"""认证鉴权边界与业务契约回归。"""
import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Job, Run

pytestmark = pytest.mark.security

TEST_TOKEN = "test-long-token-1234567890"

PROTECTED = [
    ("GET", "/api/jobs", False),
    ("POST", "/api/jobs", False),
    ("PUT", "/api/jobs/{name}", True),
    ("DELETE", "/api/jobs/{name}", True),
    ("POST", "/api/jobs/{name}/enable", True),
    ("GET", "/api/job-schema", False),
    ("GET", "/api/groups", False),
    ("GET", "/api/service-groups", False),
    ("POST", "/api/service-groups", True),
    ("PUT", "/api/service-groups/{name}", True),
    ("DELETE", "/api/service-groups/{name}", True),
    ("POST", "/api/service-groups/{name}/start", True),
    ("POST", "/api/service-groups/{name}/stop", True),
    ("GET", "/api/running", False),
    ("GET", "/api/history", False),
    ("GET", "/api/runs/{id}", False),
    ("POST", "/api/jobs/{name}/trigger", True),
    ("POST", "/api/runs/{id}/abort", True),
    ("POST", "/api/runs/{id}/stop", True),
    ("DELETE", "/api/history", True),
    ("GET", "/api/events", False),
    ("GET", "/api/runs/{id}/stream", False),
    ("POST", "/api/fs/pick", True),
]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _login(client):
    r = client.post("/api/login", json={"token": TEST_TOKEN})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture
def client():
    """独立的 TestClient（进程内，不占端口）。"""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_client(client):
    """已登录的 client，返回 (client, headers)。"""
    tok = _login(client)
    return client, _auth(tok)


class _FakeRunner:
    """Executor 需要的最小 runner 接口。"""

    def on_started(self, run):
        pass

    def on_log(self, run, line):
        run.append_log(line)

    def on_finished(self, run):
        pass


@pytest.mark.parametrize("method,path,danger", PROTECTED,
                         ids=[f"{m} {p}" for m, p, _ in PROTECTED])
def test_unauthorized_rejected(client, method, path, danger):
    p = path.replace("{name}", "x").replace("{id}", "unexistent-run-id")
    r = getattr(client, method.lower())(p)
    assert r.status_code == 401, (method, p, r.status_code, r.text)


def test_public_open(client):
    for path in ("/api/health", "/api/constants"):
        r = client.get(path)
        assert r.status_code == 200, (path, r.status_code, r.text)

    c = client.get("/api/constants").json()
    assert c.get("capabilities", {}).get("fsPick") is True, c

    bad = client.post("/api/login", json={"token": "wrong"})
    assert bad.status_code == 401


def test_login_logout_flow(client):
    tok = _login(client)
    assert isinstance(tok, str) and len(tok) > 8

    assert client.get("/api/jobs", headers=_auth(tok)).status_code == 200
    assert client.post("/api/logout", headers=_auth(tok)).status_code == 200
    assert client.get("/api/jobs", headers=_auth(tok)).status_code == 401


def test_dangerous_endpoint_protection(client):
    tok = _login(client)
    h = _auth(tok)

    job = {"name": "sec_j1", "type": "COMMAND", "command": "echo hi"}
    assert client.post("/api/jobs", json=job, headers=h).status_code == 200
    assert client.post("/api/jobs/sec_j1/trigger", headers=h).status_code in (200, 409)
    assert client.post("/api/jobs/sec_j1/trigger").status_code == 401
    assert client.delete("/api/jobs/sec_j1", headers=h).status_code == 200


def test_fs_pick_no_switch_authed(client, monkeypatch):

    from app.api import events as events_mod

    monkeypatch.setattr(
        events_mod, "_run_native_picker",
        lambda kind, title, initial, out: out.put({"ok": True, "path": "/tmp/fake.txt"}))

    tok = _login(client)
    r = client.post("/api/fs/pick", json={}, headers=_auth(tok))
    assert r.status_code == 200, (r.status_code, r.text)
    assert r.json().get("path") == "/tmp/fake.txt"

    assert client.post("/api/fs/pick", json={}).status_code == 401


def test_sse_unauthorized(client):
    assert client.get("/api/events").status_code == 401
    assert client.get("/api/runs/1/stream").status_code == 401


@pytest.mark.slow
def test_sse_authenticated_finite(auth_client):
    client, h = auth_client

    job = {"name": "sse_log_1", "type": "COMMAND", "command": "echo sse-check"}
    assert client.post("/api/jobs", json=job, headers=h).status_code == 200
    r = client.post("/api/jobs/sse_log_1/trigger", headers=h)
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]

    deadline = time.time() + 15
    done = None
    while time.time() < deadline:
        rd = client.get(f"/api/runs/{run_id}", headers=h)
        if rd.status_code == 200:
            j = rd.json()
            if j.get("status") in ("SUCCEEDED", "FAILED", "KILLED", "TIMEDOUT"):
                done = j
                break
        time.sleep(0.5)
    assert done, "任务未在时限内结束，无法验证有限流"

    received_end = False
    with client.stream("GET", f"/api/runs/{run_id}/stream", headers=h) as resp:
        assert resp.status_code == 200, resp.status_code
        for chunk in resp.iter_text():
            if '"end": true' in chunk or '"end":true' in chunk:
                received_end = True
                break
    assert received_end, "SSE 流未收到结束哨兵"

    client.delete("/api/jobs/sse_log_1", headers=h)


def test_login_throttle(client):
    from app import auth as auth_mod

    auth_mod.reset_login_throttle()

    throttled = False
    for _ in range(8):
        r = client.post("/api/login", json={"token": "wrong-token-xyz"})
        assert r.status_code == 401, r.status_code
        if "频繁" in r.text:
            throttled = True
            break
    assert throttled, "连续失败后未触发限流提示"

    r = client.post("/api/login", json={"token": TEST_TOKEN})
    assert r.status_code == 401 and "频繁" in r.text, r.text

    auth_mod.reset_login_throttle()


def test_fileop_anywhere(tmp_path):
    from app.executor import Executor

    ex = Executor(_FakeRunner())

    def _mk(name):
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
        return str(p)

    outside = _mk("outside_anywhere.txt")
    r1 = Run(id="fp1", job_name="t", job_type="FILEOP", status="PENDING")
    ex.run(r1, Job.from_dict({"name": "t", "type": "FILEOP",
                              "params": {"action": "delete_file",
                                         "source": outside, "recursive": False}}))
    assert r1.status == "SUCCEEDED", r1.status
    assert not os.path.exists(outside)

    git_file = _mk(".git/objects/x")
    r2 = Run(id="fp2", job_name="t", job_type="FILEOP", status="PENDING")
    ex.run(r2, Job.from_dict({"name": "t", "type": "FILEOP",
                              "params": {"action": "delete_file",
                                         "source": git_file, "recursive": False}}))
    assert r2.status == "SUCCEEDED", r2.status
    assert not os.path.exists(git_file)

    data_file = _mk("server_data_style/d.txt")
    r3 = Run(id="fp3", job_name="t", job_type="FILEOP", status="PENDING")
    ex.run(r3, Job.from_dict({"name": "t", "type": "FILEOP",
                              "params": {"action": "delete_file",
                                         "source": data_file, "recursive": False}}))
    assert r3.status == "SUCCEEDED", r3.status
    assert not os.path.exists(data_file)

    r4 = Run(id="fp4", job_name="t", job_type="FILEOP", status="PENDING")
    ex.run(r4, Job.from_dict({"name": "t", "type": "FILEOP",
                              "params": {"action": "delete_file",
                                         "source": str(tmp_path / "no_such.txt"),
                                         "recursive": False}}))
    assert r4.status == "FAILED" and "不存在" in (r4.error or ""), (r4.status, r4.error)


def test_script_program_outside_exec_root(tmp_path):
    """SCRIPT / PROGRAM / COMMAND 均可作用于执行根之外。"""
    from app.executor import Executor

    ex = Executor(_FakeRunner())

    r1 = Run(id="prg1", job_name="t", job_type="PROGRAM", status="PENDING")
    ex.run(r1, Job.from_dict({"name": "t", "type": "PROGRAM",
                              "params": {"path": sys.executable,
                                         "args": ["-c", "print('hello-out')"]}}))
    assert r1.status == "SUCCEEDED", (r1.status, r1.error)
    assert r1.exit_code == 0, r1.exit_code

    out_script = tmp_path / "outside_root.ps1"
    out_script.write_text("Write-Output root-outside-ok", encoding="utf-8")
    r2 = Run(id="scr1", job_name="t", job_type="SCRIPT", status="PENDING")
    ex.run(r2, Job.from_dict({"name": "t", "type": "SCRIPT",
                              "params": {"path": str(out_script), "args": []}}))
    assert r2.status == "SUCCEEDED", (r2.status, r2.error)

    r3 = Run(id="cmd1", job_name="t", job_type="COMMAND", status="PENDING")
    ex.run(r3, Job.from_dict({"name": "t", "type": "COMMAND",
                              "params": {"command": "echo cmd-ok"}}))
    assert r3.status == "SUCCEEDED", (r3.status, r3.error)


def test_nonzero_exit_interrupted():

    from app.executor import Executor

    ex = Executor(_FakeRunner())
    cmd = "exit /b 3" if os.name == "nt" else "exit 3"

    r = Run(id="nz1", job_name="t", job_type="COMMAND", status="PENDING")
    ex.run(r, Job.from_dict({"name": "t", "type": "COMMAND",
                             "params": {"command": cmd}}))

    assert r.exit_code != 0, r.exit_code
    assert r.status == "INTERRUPTED", (r.status, r.error)
    assert "退出码" in r.error and r.error.startswith(f"退出码 {r.exit_code}"), r.error
    assert "外部终止或异常退出" in r.error, r.error
    assert not any("[identity]" in ln for ln in (r.log or [])), r.log


@pytest.mark.regression
def test_original_features_regression(auth_client):
    client, h = auth_client

    r = client.get("/api/job-schema", headers=h)
    assert r.status_code == 200 and "types" in r.json()

    assert client.get("/api/groups", headers=h).status_code == 200

    r = client.get("/api/history", headers=h)
    assert r.status_code == 200 and "total" in r.json()

    assert client.get("/api/running", headers=h).status_code == 200

    job = {"name": "reg_1", "type": "COMMAND", "command": "echo ok"}
    assert client.post("/api/jobs", json=job, headers=h).status_code == 200

    lst = client.get("/api/jobs?all=true", headers=h).json()
    names = [j["name"] for j in (lst if isinstance(lst, list) else lst.get("items", []))]
    assert "reg_1" in names, names

    assert client.put("/api/jobs/reg_1",
                      json={"name": "reg_1", "type": "COMMAND", "command": "echo ok2"},
                      headers=h).status_code == 200
    assert client.delete("/api/jobs/reg_1", headers=h).status_code == 200

    r = client.get("/api/audit", headers=h)
    assert r.status_code == 200 and "items" in r.json()
