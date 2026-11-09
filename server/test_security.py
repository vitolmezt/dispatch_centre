# -*- coding: utf-8 -*-
"""阶段1 安全加固测试（执行权限调整后更新）.

运行：C:\\Users\\bzds\\miniforge3\\python.exe test_security.py
覆盖：
  - 未认证请求被拒 (401) —— 所有受保护端点
  - 公开端点（health / constants / login）无需认证
  - 登录鉴权成功/失败；logout 吊销会话
  - 危险端点（create/update/delete/enable/trigger/abort/stop/delete-history/
    service-group CRUD+start/stop / fs_pick）认证 + 授权保护
  - FILEOP 不再受安全根限制：根外路径 / .git / server/data 均可正常操作
  - SCRIPT/PROGRAM 不再受 SC_EXEC_ROOT 限制：根外程序/脚本可执行
  - fs/pick 无需任何开关即可用（认证后可达），未认证仍 401
  - SSE events/stream 需要认证（Fetch-SSE 用 Bearer，不被 401）
  - 原有功能回归：jobs CRUD + trigger + history 在认证后可用；API 契约不变

说明：本测试在导入 app.main 之前设置环境变量（SC_TOKEN / SC_DB/临时），
确保认证按预期加载；执行权限调整后不再需要 SC_SAFE_ROOT / SC_EXEC_ROOT /
SC_ENABLE_FS_PICK。
"""
import os
import sys
import tempfile

from fastapi.testclient import TestClient  # noqa: E402

# ---- 在导入 app.main 之前配置环境 ----
_TMP = tempfile.mkdtemp(prefix="sc_sec_")
_TEST_TOKEN = "test-long-token-1234567890"
_SAFE_ROOT = os.path.join(_TMP, "safe")
os.makedirs(_SAFE_ROOT, exist_ok=True)
_EXEC_ROOT = os.path.join(_TMP, "exec")
os.makedirs(_EXEC_ROOT, exist_ok=True)
# 显式确保安全根环境变量不设置，验证"不配置也能执行"（不再 fail-closed）
os.environ.pop("SC_SAFE_ROOT", None)
os.environ.pop("SC_EXEC_ROOT", None)
os.environ["SC_TOKEN"] = _TEST_TOKEN
os.environ["SC_DB"] = os.path.join(_TMP, "security.db")
os.environ["SC_ALLOW_ORIGINS"] = "http://127.0.0.1:9999"

from app.main import app  # noqa: E402
from app.models import Job, Run  # noqa: E402


def _new_client():
    return TestClient(app)


def _login(client):
    r = client.post("/api/login", json={"token": _TEST_TOKEN})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# 所有需要认证的端点（方法, 路径, 是否危险写操作）
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


def test_unauthorized_rejected(client):
    for method, path, _danger in PROTECTED:
        p = path.replace("{name}", "x").replace("{id}", "unexistent-run-id")
        r = getattr(client, method.lower())(p)
        assert r.status_code == 401, (method, p, r.status_code, r.text)
    print(f"[OK] 未认证访问 {len(PROTECTED)} 个受保护端点全部 401")


def test_public_open(client):
    for method, path in [("GET", "/api/health"), ("GET", "/api/constants")]:
        r = getattr(client, method.lower())(path)
        assert r.status_code == 200, (path, r.status_code, r.text)
    # capabilities 下发改后端能力开关（执行权限调整后 fs/pick 始终可用）
    c = client.get("/api/constants").json()
    assert c.get("capabilities", {}).get("fsPick") is True, c
    bad = client.post("/api/login", json={"token": "wrong"})
    assert bad.status_code == 401
    print("[OK] 公开端点可用；capabilities.fsPick 为 true；login 错误凭证 401")


def test_login_logout_flow(client):
    tok = _login(client)
    assert isinstance(tok, str) and len(tok) > 8
    r = client.get("/api/jobs", headers=_auth(tok))
    assert r.status_code == 200, r.text
    r = client.post("/api/logout", headers=_auth(tok))
    assert r.status_code == 200
    r = client.get("/api/jobs", headers=_auth(tok))
    assert r.status_code == 401, r.text
    print("[OK] 登录→访问→登出→会话失效 全链路通过")


def test_dangerous_endpoint_protection(client):
    tok = _login(client)
    h = _auth(tok)
    job = {"name": "sec_j1", "type": "COMMAND", "command": "echo hi"}
    r = client.post("/api/jobs", json=job, headers=h)
    assert r.status_code == 200, r.text
    r = client.post("/api/jobs/sec_j1/trigger", headers=h)
    assert r.status_code in (200, 409), (r.status_code, r.text)
    r = client.post("/api/jobs/sec_j1/trigger")
    assert r.status_code == 401
    r = client.delete("/api/jobs/sec_j1", headers=h)
    assert r.status_code == 200, r.text
    print("[OK] 危险端点在认证下可用，未认证被拒")


def test_fileop_anywhere(client):
    """执行权限调整后：不配置安全根也能 FILEOP；根外 / .git / server/data 均可操作."""
    from app.executor import Executor

    class FakeRunner:
        def on_started(self, run): pass
        def on_log(self, run, line): run.append_log(line)
        def on_finished(self, run): pass

    ex = Executor(FakeRunner())

    def _mk(name):
        p = os.path.join(_TMP, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write("x")
        return p

    # 1) 根外路径（安全根目录之外）可删除
    outside = _mk("outside_anywhere.txt")
    r1 = Run(id="fp1", job_name="t", job_type="FILEOP", status="PENDING")
    ex.run(r1, Job.from_dict({"name": "t", "type": "FILEOP",
                              "params": {"action": "delete_file", "source": outside, "recursive": False}}))
    assert r1.status == "SUCCEEDED", r1.status
    assert not os.path.exists(outside)
    print("[OK] FILEOP 未配置安全根，可删除根外路径")

    # 2) .git 目录内路径可操作（不再特殊排除）
    git_area = os.path.join(_TMP, ".git")
    os.makedirs(git_area, exist_ok=True)
    git_file = os.path.join(git_area, "objects", "x")
    os.makedirs(os.path.dirname(git_file), exist_ok=True)
    with open(git_file, "w", encoding="utf-8") as f:
        f.write("x")
    r2 = Run(id="fp2", job_name="t", job_type="FILEOP", status="PENDING")
    ex.run(r2, Job.from_dict({"name": "t", "type": "FILEOP",
                              "params": {"action": "delete_file", "source": git_file, "recursive": False}}))
    assert r2.status == "SUCCEEDED", r2.status
    assert not os.path.exists(git_file)
    print("[OK] FILEOP 可操作 .git 目录内路径")

    # 3) server/data 风格路径可操作（本测试 db 即在该目录风格下）
    data_area = os.path.join(_TMP, "server_data_style")
    os.makedirs(data_area, exist_ok=True)
    data_file = os.path.join(data_area, "d.txt")
    with open(data_file, "w", encoding="utf-8") as f:
        f.write("x")
    r3 = Run(id="fp3", job_name="t", job_type="FILEOP", status="PENDING")
    ex.run(r3, Job.from_dict({"name": "t", "type": "FILEOP",
                              "params": {"action": "delete_file", "source": data_file, "recursive": False}}))
    assert r3.status == "SUCCEEDED", r3.status
    assert not os.path.exists(data_file)
    print("[OK] FILEOP 可操作任意数据目录内路径")

    # 4) 不存在的文件 → 正常报错（交给 OS/执行器返回错误，而非安全根拒绝）
    r4 = Run(id="fp4", job_name="t", job_type="FILEOP", status="PENDING")
    ex.run(r4, Job.from_dict({"name": "t", "type": "FILEOP",
                              "params": {"action": "delete_file", "source": os.path.join(_TMP, "no_such.txt"), "recursive": False}}))
    assert r4.status == "FAILED" and "不存在" in (r4.error or ""), (r4.status, r4.error)
    print("[OK] FILEOP 文件不存在时报明确错误（非安全根拒绝）")


def test_script_program_outside_exec_root(client):
    """执行权限调整后：SCRIPT/PROGRAM 可执行安全根(SC_EXEC_ROOT)之外的程序/脚本."""
    from app.executor import Executor

    class FakeRunner:
        def on_started(self, run): pass
        def on_log(self, run, line): run.append_log(line)
        def on_finished(self, run): pass

    ex = Executor(FakeRunner())

    # PROGRAM 根外：直接执行可执行程序（用 python 解析器本身作为"程序"）
    py = sys.executable
    r1 = Run(id="prg1", job_name="t", job_type="PROGRAM", status="PENDING")
    ex.run(r1, Job.from_dict({"name": "t", "type": "PROGRAM",
                              "params": {"path": py, "args": ["-c", "print('hello-out')"]}}))
    assert r1.status in ("SUCCEEDED",), (r1.status, r1.error)
    assert r1.exit_code == 0, r1.exit_code
    print("[OK] PROGRAM 可执行 SC_EXEC_ROOT 之外的程序（python.exe）")

    # SCRIPT 根外：脚本放在安全根(_SAFE_ROOT)之外的位置
    out_script = os.path.join(_TMP, "outside_root.ps1")
    with open(out_script, "w", encoding="utf-8") as f:
        f.write("Write-Output root-outside-ok")
    r2 = Run(id="scr1", job_name="t", job_type="SCRIPT", status="PENDING")
    ex.run(r2, Job.from_dict({"name": "t", "type": "SCRIPT",
                              "params": {"path": out_script, "args": []}}))
    assert r2.status == "SUCCEEDED", (r2.status, r2.error)
    print("[OK] SCRIPT 可执行 SC_EXEC_ROOT 之外的脚本")

    # COMMAND 无目录白名单：任意命令可执行
    r3 = Run(id="cmd1", job_name="t", job_type="COMMAND", status="PENDING")
    ex.run(r3, Job.from_dict({"name": "t", "type": "COMMAND",
                              "params": {"command": "echo cmd-ok"}}))
    assert r3.status == "SUCCEEDED", (r3.status, r3.error)
    print("[OK] COMMAND 不受路径白名单限制")


def test_fs_pick_no_switch_authed(client):
    """执行权限调整后：fs/pick 无需开关，已认证即可访问；未认证仍 401."""
    from app import main as main_mod

    # 用 fake picker 替代原生对话框：立即返回一个路径，验证"认证即放开、不因开关禁用"
    main_mod._run_native_picker = lambda kind, title, initial, out: out.put({"ok": True, "path": "/tmp/fake.txt"})

    tok = _login(client)
    r = client.post("/api/fs/pick", json={}, headers=_auth(tok))
    assert r.status_code == 200, (r.status_code, r.text)
    assert r.json().get("path") == "/tmp/fake.txt"
    # 未认证仍被拒
    r = client.post("/api/fs/pick", json={})
    assert r.status_code == 401, r.text
    print("[OK] fs/pick 无需开关：认证后 200 可达，未认证 401")


def test_sse_unauthorized(client):
    # 认证是 SSE 的关键安全点：缺 Bearer 必须 401（无论 events 还是 run stream）
    r = client.get("/api/events")
    assert r.status_code == 401, r.text
    r = client.get("/api/runs/1/stream")
    assert r.status_code == 401, r.text
    print("[OK] SSE（events / run stream）未认证 401")


def test_sse_authenticated_finite(client):
    # 已结束(有库记录)的 run，其 /stream 是「有穷流」：replay 完日志后以 end:true 收尾。
    # 借此验证「带 Bearer 认证的 Fetch-SSE 能正常消费到流并读到 end 哨兵」，
    # 等价覆盖实时日志 SSE 携带 Authorization 头的握手路径。
    import time
    tok = _login(client)
    h = _auth(tok)
    job = {"name": "sse_log_1", "type": "COMMAND", "command": "echo sse-check"}
    r = client.post("/api/jobs", json=job, headers=h)
    assert r.status_code == 200, r.text
    r = client.post("/api/jobs/sse_log_1/trigger", headers=h)
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    # 等任务结束成为历史（有 demo 记录）
    deadline = time.time() + 15
    done_rec = None
    while time.time() < deadline:
        rd = client.get(f"/api/runs/{run_id}", headers=h)
        if rd.status_code == 200:
            j = rd.json()
            if j.get("status") in ("SUCCEEDED", "FAILED", "KILLED", "TIMEDOUT"):
                done_rec = j
                break
        time.sleep(0.5)
    assert done_rec, "任务未在时限内结束，无法验证有限流"
    # 带 Bearer 读取该 run 的流（有穷），应拿到 end 哨兵
    meta = done_rec
    with client.stream("GET", f"/api/runs/{run_id}/stream", headers=h) as resp:
        assert resp.status_code == 200, resp.status_code
        received_end = False
        for chunk in resp.iter_text():
            if '"end": true' in chunk or '"end":true' in chunk:
                received_end = True
                break
    assert received_end, "SSE 流未收到结束哨兵"
    # 清理
    client.delete("/api/jobs/sse_log_1", headers=h)
    print("[OK] 带 Bearer 认证的 run 日志 SSE 可消费到结束哨兵，认证握手通过")


def test_original_features_regression(client):
    tok = _login(client)
    h = _auth(tok)
    r = client.get("/api/job-schema", headers=h)
    assert r.status_code == 200 and "types" in r.json()
    r = client.get("/api/groups", headers=h)
    assert r.status_code == 200
    r = client.get("/api/history", headers=h)
    assert r.status_code == 200 and "total" in r.json()
    r = client.get("/api/running", headers=h)
    assert r.status_code == 200
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
    print("[OK] 原有功能（schema/groups/history/running/任务CRUD/审计）认证后契约不变")


def test_login_throttle(client):
    """登录失败限流：同一来源连续失败超阈值后应 401 且提示冷却."""
    from app import auth as auth_mod
    # 先清空全局限流计数，保证干净
    auth_mod.reset_login_throttle()
    # 连续用错误凭证打阈值+次数
    throttled = False
    for _ in range(8):
        r = client.post("/api/login", json={"token": "wrong-token-xyz"})
        assert r.status_code == 401, r.status_code
        if "频繁" in r.text:
            throttled = True
            break
    assert throttled, "连续失败后未触发限流提示"
    # 冷却期间正确凭证也应被限流拒绝
    r = client.post("/api/login", json={"token": _TEST_TOKEN})
    assert r.status_code == 401 and "频繁" in r.text, r.text
    print("[OK] 登录失败限流：连续错误后进入冷却，正确凭证亦被 401")
    # 复位，避免影响其它测试
    auth_mod.reset_login_throttle()


def main():
    # 每个测试用独立 client（with 触发 startup），fs_pick 无需切换开关
    test_fns = [
        test_unauthorized_rejected,
        test_public_open,
        test_login_logout_flow,
        test_dangerous_endpoint_protection,
        test_fileop_anywhere,
        test_script_program_outside_exec_root,
        test_login_throttle,
        test_fs_pick_no_switch_authed,
        test_sse_unauthorized,
        test_sse_authenticated_finite,
        test_original_features_regression,
    ]
    passed = 0

    def run(fn):
        with _new_client() as c:
            fn(c)

    for fn in test_fns:
        try:
            run(fn)
            passed += 1
            print(f"  → {fn.__name__} PASSED")
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"FAIL: {fn.__name__}: {e}")
            return 1

    print(f"\nSUCCESS: {passed}/{len(test_fns)} 安全测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())