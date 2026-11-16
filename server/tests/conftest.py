# -*- coding: utf-8 -*-
"""pytest 共享配置：环境初始化 + 通用 fixture。

重要：app 包中多个模块在 **导入时** 就读取环境变量
（app/audit.py、app/auth.py、app/main.py 的 SC_DB），
而 app/auth.py 的 SC_TOKEN 是调用时读取。
因此本文件必须在任何 app 模块被导入前先把环境变量设好 ——
pytest 会先加载 conftest.py，故在模块顶层设置即可生效。
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 1. 工作目录 / 导入路径
# ---------------------------------------------------------------------------
# tests/ 的父目录是 server/，业务包 app 就在其中。
SERVER_DIR = Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

# ---------------------------------------------------------------------------
# 2. 会话级临时目录（整个测试会话共用，退出时清理）
# ---------------------------------------------------------------------------
SESSION_TMP = tempfile.mkdtemp(prefix="sc_pytest_")

# ---------------------------------------------------------------------------
# 3. 环境变量（必须在导入 app 之前）
# ---------------------------------------------------------------------------
# 认证：固定长期凭证，避免依赖 data/sc_token 文件
TEST_TOKEN = "test-long-token-1234567890"
os.environ["SC_TOKEN"] = TEST_TOKEN
os.environ["SC_DB"] = os.path.join(SESSION_TMP, "security.db")
os.environ["SC_ALLOW_ORIGINS"] = "http://127.0.0.1:9999"
os.environ.setdefault("SC_NODE_ID", "unit-node")

# 心跳参数：缩短轮询，便于测试超时逻辑
os.environ["SC_HEARTBEAT_INTERVAL_SEC"] = "2"
os.environ["SC_HEARTBEAT_TIMEOUT_TIMES"] = "2"
os.environ["SC_HEARTBEAT_BATCH_SEC"] = "60"

# 明确清除可选的安全根，验证「不设路径白名单」的行为
os.environ.pop("SC_SAFE_ROOT", None)
os.environ.pop("SC_EXEC_ROOT", None)

# ---------------------------------------------------------------------------
# 4. 导入业务模块（此时环境变量已就绪）
# ---------------------------------------------------------------------------
from app.models import Job, Run  # noqa: E402
from app.scheduler import Scheduler  # noqa: E402


# ---------------------------------------------------------------------------
# 5. 公共辅助
# ---------------------------------------------------------------------------
def make_run(run_id="r1", status="RUNNING", **kw):
    """构造一条 Run，未给出的字段用默认值。"""
    data = dict(id=run_id, job_name="t1", job_type="COMMAND", status=status)
    data.update(kw)
    return Run(**data)


def make_job(name="t1", command="echo hi", detach=False):
    return Job(name=name, type="COMMAND", params={"command": command, "detach": detach})


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 6. fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def _session_cleanup():
    """整个会话结束后删除共享临时目录。"""
    yield
    shutil.rmtree(SESSION_TMP, ignore_errors=True)


@pytest.fixture
def tmp_dir(tmp_path):
    """单项测试独立的临时目录（pytest 自动清理）。"""
    d = tmp_path / "work"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def sched(tmp_path):
    """独立的 Scheduler 实例，测试结束自动关闭 store。

    每个用例用独立 db，避免相互污染。
    """
    cfg = tmp_path / "jobs.json"
    cfg.write_text('{"crawler": {"items": []}}', encoding="utf-8")
    s = Scheduler(config_path=str(cfg), db_path=str(tmp_path / "test.db"))
    yield s
    try:
        s.store.close()
    except Exception:
        pass


@pytest.fixture
def alive_false(monkeypatch):
    """把 OS 探针改为「进程已死」。"""
    from app import identity as identity_mod
    monkeypatch.setattr(identity_mod, "process_is_alive", lambda run, pid=None: False)


@pytest.fixture
def alive_true(monkeypatch):
    """把 OS 探针改为「进程存活」。"""
    from app import identity as identity_mod
    monkeypatch.setattr(identity_mod, "process_is_alive", lambda run, pid=None: True)
