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


SERVER_DIR = Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

SESSION_TMP = tempfile.mkdtemp(prefix="sc_pytest_")

TEST_TOKEN = "test-long-token-1234567890"
os.environ["SC_TOKEN"] = TEST_TOKEN
os.environ["SC_DB"] = os.path.join(SESSION_TMP, "security.db")
os.environ["SC_ALLOW_ORIGINS"] = "http://127.0.0.1:9999"
os.environ.setdefault("SC_NODE_ID", "unit-node")

os.environ["SC_HEARTBEAT_INTERVAL_SEC"] = "2"
os.environ["SC_HEARTBEAT_TIMEOUT_TIMES"] = "2"
os.environ["SC_HEARTBEAT_BATCH_SEC"] = "60"

os.environ.pop("SC_SAFE_ROOT", None)
os.environ.pop("SC_EXEC_ROOT", None)

from app.models import Job, Run
from app.scheduler import Scheduler 

def make_run(run_id="r1", status="RUNNING", **kw):
    data = dict(id=run_id, job_name="t1", job_type="COMMAND", status=status)
    data.update(kw)
    return Run(**data)


def make_job(name="t1", command="echo hi", detach=False):
    return Job(name=name, type="COMMAND", params={"command": command, "detach": detach})


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")

@pytest.fixture(scope="session", autouse=True)
def _session_cleanup():
    yield
    shutil.rmtree(SESSION_TMP, ignore_errors=True)


@pytest.fixture
def tmp_dir(tmp_path):
    d = tmp_path / "work"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def sched(tmp_path):
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
    from app import identity as identity_mod
    monkeypatch.setattr(identity_mod, "process_is_alive", lambda run, pid=None: False)


@pytest.fixture
def alive_true(monkeypatch):
    from app import identity as identity_mod
    monkeypatch.setattr(identity_mod, "process_is_alive", lambda run, pid=None: True)
