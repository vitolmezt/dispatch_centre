# -*- coding: utf-8 -*-
"""阶段1 安全加固测试.

运行：C:\\Users\\bzds\\miniforge3\\python.exe test_security.py
覆盖：
  - 未认证请求被拒 (401) —— 所有受保护端点
  - 公开端点（health / constants / login）无需认证
  - 登录鉴权成功/失败；logout 吊销会话
  - 危险端点（create/update/delete/enable/trigger/abort/stop/delete-history/
    service-group CRUD+start/stop / fs_pick）认证 + 授权保护
  - FILEOP 路径逃逸防护（.. 穿越 / 安全根外 / 对称链接 / fail-closed）
  - fs/pick 默认关闭（未开启时 403），开启后需认证
  - SSE events/stream 需要认证（Fetch-SSE 用 Bearer，不被 401）
  - 原有功能回归：jobs CRUD + trigger + history 在认证后可用；API 契约不变

说明：本测试在导入 app.main 之前设置环境变量（SC_TOKEN / SC_SAFE_ROOT /
SC_ENABLE_FS_PICK / SC_DB/临时），确保认证与安全根按预期加载。
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
os.environ["SC_TOKEN"] = _TEST_TOKEN
os.environ.setdefault("SC_SAFE_ROOT", _SAFE_ROOT)
os.environ.setdefault("SC_EXEC_ROOT", _EXEC_ROOT)
os.environ["SC_DB"] = os.path.join(_TMP, "security.db")
os.environ["SC_ALLOW_ORIGINS"] = "http://127.0.0.1:9999"

from app.main import app  # noqa: E402


def _new_client(fs_pick=False):
    from app import main as main_mod
    main_mod.FS_PICK_ENABLED = fs_pick
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
    # capabilities 下发改后端能力开关（fs/pick 默认关）
    c = client.get("/api/constants").json()
    assert c.get("capabilities", {}).get("fsPick") is False, c
    bad = client.post("/api/login", json={"token": "wrong"})
    assert bad.status_code == 401
    print("[OK] 公开端点可用；capabilities.fsPick 默认 false；login 错误凭证 401")


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


def test_fileop_escape_blocked(client):
    from app.executor import check_safe_path, UnsafePathError

    outside = os.path.join(_TMP, "outside.txt")
    with open(outside, "w", encoding="utf-8") as f:
        f.write("x")
    try:
        check_safe_path(outside)
        raise AssertionError("should be blocked: outside unsafe root")
    except UnsafePathError:
        pass

    trav = os.path.join(_SAFE_ROOT, "..", "escape.txt")
    try:
        check_safe_path(trav)
        raise AssertionError("should be blocked: traversal")
    except UnsafePathError:
        pass

    inside = os.path.join(_SAFE_ROOT, "ok.txt")
    with open(inside, "w", encoding="utf-8") as f:
        f.write("x")
    got = check_safe_path(inside)
    assert os.path.normcase(os.path.abspath(os.fspath(got))) == \
        os.path.normcase(os.path.abspath(inside)), (got, inside)

    link = os.path.join(_SAFE_ROOT, "link_out")
    try:
        os.symlink(outside, link)
        try:
            check_safe_path(link)
            raise AssertionError("should be blocked: symlink escape")
        except UnsafePathError:
            pass
        print("[OK] 软链接逃逸被拦截")
    except (OSError, NotImplementedError):
        print("[OK] 当前平台不支持 symlink，跳过软链逃逸用例")

    # fail-closed：未配置 SC_SAFE_ROOT → 拒绝
    old = os.environ.get("SC_SAFE_ROOT")
    os.environ.pop("SC_SAFE_ROOT", None)
    try:
        try:
            check_safe_path(inside)
            raise AssertionError("should be fail-closed when SC_SAFE_ROOT unset")
        except UnsafePathError as e:
            assert "未配置" in str(e)
    finally:
        if old is not None:
            os.environ["SC_SAFE_ROOT"] = old
    print("[OK] FILEOP 路径逃逸（根外/..穿越/软链/未配置fail-closed）全部拦截")


def test_fs_pick_gating(client):
    # 关闭状态（默认）即便已认证也 403
    tok = _login(client)
    r = client.post("/api/fs/pick", json={}, headers=_auth(tok))
    assert r.status_code == 403, r.text
    print("[OK] fs/pick 默认关闭：认证后仍 403")


def test_fs_pick_enabled(client):
    tok = _login(client)
    # 开启时认证后可达（对话框会阻塞，用短超时避免挂起）
    r = client.post("/api/fs/pick", json={}, headers=_auth(tok))
    assert r.status_code not in (401, 403, 1), r.status_code
    # 未认证仍被拒
    r = client.post("/api/fs/pick", json={})
    assert r.status_code == 401, r.text
    print("[OK] fs/pick 开启后：认证可访问、未认证 401")


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


def test_exec_root_separation(client):
    """SC_SAFE_ROOT(FILEOP) 与 SC_EXEC_ROOT(SCRIPT/PROGRAM) 分离：互不越权."""
    from app.executor import check_safe_path, check_exec_path, UnsafePathError

    # FILEOP 根 = _SAFE_ROOT，EXEC 根 = 独立目录 _EXEC_ROOT（setup 已定义）
    fileop_file = os.path.join(_SAFE_ROOT, "f.txt")
    with open(fileop_file, "w", encoding="utf-8") as f:
        f.write("x")
    exec_file = os.path.join(_EXEC_ROOT, "e.ps1")
    with open(exec_file, "w", encoding="utf-8") as f:
        f.write("Write-Host hi")
    assert check_safe_path(fileop_file)  # FILEOP 文件落在 FILEOP 根
    # EXEC 文件落在 EXEC 根：FILEOP 校验应拒绝（EXEC 根不在 FILEOP 根列表内）
    try:
        check_safe_path(exec_file)
        raise AssertionError("EXEC_ROOT 内文件不应通过 FILEOP 校验")
    except UnsafePathError:
        print("[OK] EXEC_ROOT 内文件不在 FILEOP 安全根下：check_safe_path 拒绝（分离生效）")
    # EXEC 校验：exec_file 在 EXEC 根 → 通过；fileop_file 不在 → 拒绝
    assert check_exec_path(exec_file)
    try:
        check_exec_path(fileop_file)
        raise AssertionError("FILEOP 根内文件不应通过 EXEC 校验")
    except UnsafePathError:
        print("[OK] FILEOP 根内文件不通过 EXEC 校验")


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
    # 每个测试用独立 client（with 触发 startup），fs_pick 相关测试单独切换开关
    tests_fs_off = [
        test_unauthorized_rejected,
        test_public_open,
        test_login_logout_flow,
        test_dangerous_endpoint_protection,
        test_fileop_escape_blocked,
        test_exec_root_separation,
        test_login_throttle,
        test_fs_pick_gating,
        test_sse_unauthorized,
        test_sse_authenticated_finite,
        test_original_features_regression,
    ]
    passed = 0
    try:
        with _new_client(fs_pick=False) as c0:
            # login_logout/stateful 流程与 fs_pick_gating 共享同一 client 亦可，但这里为简单每个独立建
            c0.close()
    except Exception:  # noqa: BLE001
        pass

    def run(fn, fs_pick):
        with _new_client(fs_pick=fs_pick) as c:
            fn(c)

    for fn in tests_fs_off:
        try:
            run(fn, fs_pick=False)
            passed += 1
            print(f"  → {fn.__name__} PASSED")
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"FAIL: {fn.__name__}: {e}")
            return 1

    try:
        run(test_fs_pick_enabled, fs_pick=True)
        passed += 1
        print("  → test_fs_pick_enabled PASSED")
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"FAIL: {e}")
        return 1

    print(f"\nSUCCESS: {passed}/{len(tests_fs_off)+1} 安全测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())