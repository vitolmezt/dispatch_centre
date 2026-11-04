"""FastAPI 入口：启动调度服务，暴露 REST API.

设计：调度中心=菜单+状态看板。
  - GET  /api/jobs        可调度菜单项
  - GET  /api/running     运行中
  - GET  /api/history     执行历史（可过滤 status/job）
  - POST /api/jobs/{name}/trigger  手动触发
  - POST /api/runs/{id}/abort      中止运行
  - GET  /api/config      服务器启动配置信息
"""
from __future__ import annotations

import logging
import os
import queue
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from . import constants
from .scheduler import Scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scheduler-center")

# 默认路径
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
        "| `/api/jobs` | GET | 指令列表（分页/类型/启用/名称搜索） |\n"
        "| `/api/groups` | GET | 任务分组元信息 |\n"
        "| `/api/running` | GET | 运行中的实例 |\n"
        "| `/api/history` | GET | 执行历史（状态/类型/名称过滤） |\n"
        "| `/api/runs/{id}` | GET | 单次执行详情（含日志） |\n"
        "| `/api/jobs/{name}/trigger` | POST | 手动触发一次 |\n"
        "| `/api/runs/{id}/abort` | POST | 中止一次运行 |\n"
        "| `/api/constants` | GET | 枚举字典（状态/类型/启用） |\n"
        "| `/api/events` | GET | 实时状态推送（SSE） |\n"
        "| `/api/health` | GET | 健康检查 |\n"
        "| `/api/config` | GET | 启动配置信息 |\n"
    ),
)

# 允许前端开发服务器跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_scheduler: Optional[Scheduler] = None


def get_scheduler() -> Scheduler:
    if _scheduler is None:
        raise HTTPException(status_code=503, detail="调度服务尚未初始化")
    return _scheduler


@app.on_event("startup")
def _startup():
    global _scheduler
    _scheduler = Scheduler(config_path=os.environ.get("SC_CONFIG", DEFAULT_CONFIG),
                           db_path=os.environ.get("SC_DB", DEFAULT_DB))
    _scheduler.start()


@app.on_event("shutdown")
def _shutdown():
    if _scheduler:
        _scheduler.shutdown()


# ---------- 菜单 ----------
@app.get("/api/jobs")
def list_jobs(type: Optional[str] = Query(None, alias="type"),
              enabled: Optional[str] = Query(None),
              q: Optional[str] = Query(None),
              page: int = Query(1, ge=1),
              page_size: int = Query(50, ge=1, le=500),
              all: bool = Query(False)):
    """菜单：按 类型(enabled 常量 key) / 启用 / 名称模糊 搜索 + 分页.

    - page / page_size：分页（第 1 页起）
    - all=true：忽略分页返回全部（兼容旧调用）
    返回 {total, page, page_size, items}
    """
    s = get_scheduler()
    if all:
        items = s.jobs_view()
        return {"total": len(items), "page": 1, "page_size": len(items), "items": items}
    offset = (page - 1) * page_size
    result = s.jobs_search(job_type=type, enabled=enabled, q=q, offset=offset, limit=page_size)
    return {"total": result["total"], "page": page, "page_size": page_size, "items": result["items"]}


@app.get("/api/groups")
def list_groups():
    """任务分组元信息（crawler 风格），供前端按分组分区展示."""
    return get_scheduler().groups_view()


# ---------- 运行中 ----------
@app.get("/api/running")
def list_running(type: Optional[str] = Query(None, alias="type"),
                 q: Optional[str] = Query(None),
                 page: int = Query(1, ge=1),
                 page_size: int = Query(100, ge=1, le=1000)):
    result = get_scheduler().running_view(job_type=type, q=q,
                                          offset=(page - 1) * page_size, limit=page_size)
    return {"total": result["total"], "page": page, "page_size": page_size, "items": result["items"]}


# ---------- 历史 ----------
@app.get("/api/history")
def history(status: Optional[str] = None, job: Optional[str] = None,
            type: Optional[str] = Query(None, alias="type"),
            q: Optional[str] = None,
            page: int = Query(1, ge=1),
            page_size: int = Query(50, ge=1, le=500),
            all: bool = Query(False)):
    if all:
        result = get_scheduler().history_view(status=status, job_name=job,
                                              job_type=type, q=q, limit=200000)
        return {"total": result["total"], "page": 1, "page_size": result["total"], "items": result["items"]}
    return get_scheduler().history_view(status=status, job_name=job, job_type=type, q=q,
                                        offset=(page - 1) * page_size, limit=page_size)


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str):
    s = get_scheduler()
    run = s.store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run 不存在")
    return run.to_dict(include_log=True)


# ---------- 触发 / 中止 ----------
@app.post("/api/jobs/{name}/trigger")
def trigger_job(name: str):
    run = get_scheduler().trigger(name)
    if run is None:
        raise HTTPException(status_code=404, detail="Job 不存在或未定义")
    return {"ok": True, "run_id": run.id, "trigger": run.trigger}


@app.post("/api/runs/{run_id}/abort")
def abort_run(run_id: str):
    ok = get_scheduler().abort(run_id)
    if not ok:
        raise HTTPException(status_code=404, detail="找不到可中止的运行实例")
    return {"ok": True}


# ---------- 全局常量字典 ----------
@app.get("/api/constants")
def constants_dict():
    """一次性下发全部分组的枚举字典，前端拉取后作为全局常量映射.

    返回形如 {"groups": {"STATUS": {"PENDING": "待开始", ...}, ...}}
    """
    return {"groups": constants.ALL_GROUPS}


# ---------- 健康 / 配置信息 ----------
@app.get("/api/health")
def health():
    return {"status": "ok", "running": len(get_scheduler().running)}


# ---------- 实时事件推送（SSE） ----------
@app.get("/api/events")
def events():
    """Server-Sent Events：任务开始/结束/中止时实时推送，前端据此即时刷新状态.

    返回 Content-Type: text/event-stream。用普通生成器在线程池运行，
    避免阻塞事件循环影响其它 API 请求。客户端断开则停止迭代并清理订阅。
    """
    s = get_scheduler()
    q = s.subscribe()

    def gen():
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    payload = q.get(timeout=15)
                    yield payload
                except queue.Empty:
                    yield ": keepalive\n\n"  # 心跳，保持连接存活
        finally:
            s.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "Connection": "keep-alive",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/config")
def config_info():
    return {"config_path": str(get_scheduler().config_path),
            "jobs_count": len(get_scheduler().jobs)}