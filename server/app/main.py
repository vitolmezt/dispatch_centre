from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import auth as api_auth
from .api import events as api_events
from .api import groups as api_groups
from .api import jobs as api_jobs
from .api import runs as api_runs
from .api import system as api_system
from .api.dependencies import get_scheduler, peek_scheduler, set_scheduler  # noqa: F401
from .scheduler import Scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scheduler-center")

BASE_DIR = Path(__file__).resolve().parent.parent  # server/
DEFAULT_CONFIG = os.environ.get("SC_CONFIG", str(BASE_DIR / "config" / "jobs.json"))
DEFAULT_DB = os.environ.get("SC_DB", str(BASE_DIR / "data" / "scheduler.db"))

app = FastAPI(
    title="调度中心 API",
    version="0.1.0",
    description=(
        "调度中心接口一览。\n\n"
        "| 接口 | 方法 | 说明 |\n"
        "|---|---|---|\n"
        "| `/api/jobs` | GET | 任务列表（分页/类型/启用/名称搜索） |\n"
        "| `/api/jobs` | POST | 新增一条任务 |\n"
        "| `/api/jobs/{name}` | PUT | 更新一条任务 |\n"
        "| `/api/jobs/{name}` | DELETE | 删除一条任务 |\n"
        "| `/api/jobs/{name}/enable` | POST | 启用/停用一条任务 |\n"
        "| `/api/job-schema` | GET | 各任务类型的参数表单结构（前端动态表单） |\n"
        "| `/api/groups` | GET | 任务分组元信息 |\n"
        "| `/api/running` | GET | 运行中的实例 |\n"
        "| `/api/history` | GET | 执行历史（状态/类型/名称过滤） |\n"
        "| `/api/runs/{id}` | GET | 单次执行详情（含日志） |\n"
        "| `/api/jobs/{name}/trigger` | POST | 手动触发一次 |\n"
        "| `/api/runs/{id}/abort` | POST | 中止一次运行 |\n"
        "| `/api/runs/{id}/stop` | POST | 停止 detach 后台进程（按持久化 PID 定向 kill） |\n"
        "| `/api/constants` | GET | 枚举字典（状态/类型/启用） |\n"
        "| `/api/events` | GET | 实时状态推送（SSE） |\n"
        "| `/api/health` | GET | 健康检查 |\n"
        "| `/api/config` | GET | 启动配置信息 |\n"
    ),
)


def _cors_origins():
    raw = os.environ.get("SC_ALLOW_ORIGINS", "")
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    return [
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:8000", "http://127.0.0.1:8000",
        "http://localhost:3080", "http://127.0.0.1:3080",
    ]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    scheduler = Scheduler(config_path=os.environ.get("SC_CONFIG", DEFAULT_CONFIG),
                          db_path=os.environ.get("SC_DB", DEFAULT_DB))
    set_scheduler(scheduler)
    scheduler.start()


@app.on_event("shutdown")
def _shutdown():
    scheduler = peek_scheduler()
    if scheduler:
        scheduler.shutdown()


# 路由聚合
app.include_router(api_jobs.router)
app.include_router(api_runs.router)
app.include_router(api_groups.router)
app.include_router(api_auth.router)
app.include_router(api_system.router)
app.include_router(api_events.router)
