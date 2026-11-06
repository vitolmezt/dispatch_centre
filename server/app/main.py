"""FastAPI 入口：启动调度服务，暴露 REST API.

设计：调度中心=菜单+状态看板。
  - GET  /api/jobs        可调度菜单项
  - GET  /api/running     运行中
  - GET  /api/history     执行历史（可过滤 status/job）
  - POST /api/jobs/{name}/trigger  手动触发
  - POST /api/runs/{id}/abort      中止运行
  - POST /api/runs/{id}/stop       停止 detach 后台进程（按 PID）
  - GET  /api/config      服务器启动配置信息
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Optional, Dict

from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from . import constants
from .scheduler import Scheduler
from .models import Job, JOB_PARAM_SCHEMA


def s_job_param_schema():
    return JOB_PARAM_SCHEMA

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
              sort: Optional[str] = Query(None),   # created_at | name，默认 created_at
              order: Optional[str] = Query("desc"),  # asc | desc
              page: int = Query(1, ge=1),
              page_size: int = Query(50, ge=1, le=500),
              all: bool = Query(False)):
    """菜单：按 类型(enabled 常量 key) / 启用 / 名称模糊 搜索 + 分页.

    - sort / order：排序字段与方向（默认按创建时间倒序，新任务在前）
    - all=true：忽略分页返回全部（兼容旧调用）
    返回 {total, page, page_size, items}
    """
    s = get_scheduler()
    if all:
        items = s.jobs_view(sort=sort, order=order)
        return {"total": len(items), "page": 1, "page_size": len(items), "items": items}
    offset = (page - 1) * page_size
    result = s.jobs_search(job_type=type, enabled=enabled, q=q, sort=sort, order=order,
                           offset=offset, limit=page_size)
    return {"total": result["total"], "page": page, "page_size": page_size, "items": result["items"]}


# ---------- 任务 CRUD（不再依赖 jobs.json，jobs 存于数据库 jobs 表） ----------
def _job_err(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


@app.post("/api/jobs")
def create_job(payload: dict = Body(...)):
    """新增一条任务。payload 结构同旧 jobs.json 条目：{name,type,params,schedule,timeout_s,enabled,description}."""
    s = get_scheduler()
    job = Job.from_dict(payload)
    errs = job.validate()
    if errs:
        raise _job_err("；".join(errs))
    if job.name in s.jobs:
        raise HTTPException(status_code=409, detail=f"任务名已存在: {job.name}")
    s.add_job(job)
    return {"ok": True, "name": job.name}


@app.put("/api/jobs/{name}")
def update_job(name: str, payload: dict = Body(...)):
    """更新一条任务（按完整覆盖：name 不可改名，其余字段整体替换）."""
    s = get_scheduler()
    old = s.jobs.get(name)
    if old is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {name}")
    data = dict(payload)
    data["name"] = name  # name 作为主键，不可通过 payload 改名
    job = Job.from_dict(data)
    errs = job.validate()
    if errs:
        raise _job_err("；".join(errs))
    s.update_job(job)
    return {"ok": True, "name": job.name}


@app.delete("/api/jobs/{name}")
def delete_job(name: str):
    s = get_scheduler()
    if name not in s.jobs:
        raise HTTPException(status_code=404, detail=f"任务不存在: {name}")
    s.delete_job(name)
    return {"ok": True, "name": name}


@app.post("/api/jobs/{name}/enable")
def toggle_job(name: str, payload: dict = Body(...)):
    """启用/停用一条任务。payload: {enabled: bool}."""
    s = get_scheduler()
    job = s.jobs.get(name)
    if job is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {name}")
    enabled = bool(payload.get("enabled", True))
    try:
        s.set_job_enabled(name, enabled)
    except ValueError as e:
        # 正在执行的任务不允许停用
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True, "name": name, "enabled": enabled}


@app.get("/api/job-schema")
def job_schema():
    """任务类型 -> 参数表单结构（前端据此动态渲染配置表单，无需硬编码）."""
    return {"types": constants.JOB_TYPE, "schemas": s_job_param_schema()}


@app.get("/api/groups")
def list_groups():
    """任务分组元信息（crawler 风格），供前端按分组分区展示."""
    return get_scheduler().groups_view()


# ---------- 运行中 ----------
@app.get("/api/running")
def list_running(type: Optional[str] = Query(None, alias="type"),
                 q: Optional[str] = Query(None),
                 sort: Optional[str] = Query(None),     # start | name，默认 start
                 order: Optional[str] = Query("desc"),   # asc | desc
                 page: int = Query(1, ge=1),
                 page_size: int = Query(100, ge=1, le=1000)):
    result = get_scheduler().running_view(job_type=type, q=q, sort=sort, order=order,
                                          offset=(page - 1) * page_size, limit=page_size)
    return {"total": result["total"], "page": page, "page_size": page_size, "items": result["items"]}


# ---------- 历史 ----------
@app.get("/api/history")
def history(status: Optional[str] = None, job: Optional[str] = None,
            type: Optional[str] = Query(None, alias="type"),
            q: Optional[str] = None,
            sort: Optional[str] = Query(None),     # start | name，默认 start
            order: Optional[str] = Query("desc"),   # asc | desc
            page: int = Query(1, ge=1),
            page_size: int = Query(50, ge=1, le=500),
            all: bool = Query(False)):
    if all:
        result = get_scheduler().history_view(status=status, job_name=job,
                                              job_type=type, q=q, sort=sort, order=order,
                                              limit=200000)
        return {"total": result["total"], "page": 1, "page_size": result["total"], "items": result["items"]}
    return get_scheduler().history_view(status=status, job_name=job, job_type=type, q=q,
                                        sort=sort, order=order,
                                        offset=(page - 1) * page_size, limit=page_size)


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str):
    s = get_scheduler()
    # 运行中实例优先返回内存中的实时日志；否则读取数据库。
    mem = s.get_running(run_id)
    if mem is not None:
        return mem.to_dict(include_log=True)
    run = s.store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run 不存在")
    return run.to_dict(include_log=True)


# ---------- 触发 / 中止 ----------
@app.post("/api/jobs/{name}/trigger")
def trigger_job(name: str):
    """手动触发一次。

    - 409：任务正在运行（全局并发上限=1，拒绝重复触发）
    - 403：任务已停用
    - 404：任务不存在
    """
    result = get_scheduler().trigger(name)
    if result is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if result == "blocked":
        raise HTTPException(status_code=409, detail="该任务正在执行中，请等待其结束后再触发")
    if result == "disabled":
        raise HTTPException(status_code=403, detail="该任务已停用，无法触发")
    return {"ok": True, "run_id": result.id, "trigger": result.trigger}


@app.post("/api/runs/{run_id}/abort")
def abort_run(run_id: str):
    ok = get_scheduler().abort(run_id)
    if not ok:
        raise HTTPException(status_code=404, detail="找不到可中止的运行实例")
    return {"ok": True}


@app.post("/api/runs/{run_id}/stop")
def stop_run(run_id: str):
    """停止一个以 detach 方式启动的后台进程（按记录的 PID 用 taskkill 精准 kill）.

    用于解决“调度中心关闭后 dsh web 等后台进程成为孤儿、找不到窗口关不掉”的问题：
    前端点“停止”，后端读该 run 持久化的 PID，taskkill /F /PID 精准终止。
    """
    result = get_scheduler().stop(run_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail="找不到该运行记录")
    return result


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


# ---------- 实时日志流（按 run 定向 SSE） ----------
@app.get("/api/runs/{run_id}/stream")
def run_log_stream(run_id: str):
    """Server-Sent Events：某 run 运行中的实时日志，按行推送。

    - 先推送该 run 当前已收集的日志（内存/归档文件），
    - 之后每新一行实时推一条 {type:"log", line}，
    - run 结束推 {type:"log", end:true} 后关闭连接。
    仅当该 run 运行中时，连接才有意义；历史 run 直接走 /api/runs/{id}（含 log）。
    """
    s = get_scheduler()

    # 若是历史（不在运行中），直接以结束哨兵收尾，避免空等
    mem = s.get_running(run_id)
    if mem is None:
        # 无运行实例也无日志文件 → 404
        if s.store.get_run(run_id) is None and not s.read_log_file(run_id):
            raise HTTPException(status_code=404, detail="run 不存在")
        def done():
            yield "retry: 3000\n\n"
            yield "data: " + json.dumps({"type": "log", "run_id": run_id, "end": True}, ensure_ascii=False) + "\n\n"
        return StreamingResponse(done(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "Connection": "keep-alive",
                                          "X-Accel-Buffering": "no"})

    q = s.subscribe_log(run_id)

    def gen():
        try:
            yield "retry: 3000\n\n"
            # 先回放当前已收集的日志
            for line in (mem.log if mem else []):
                yield "data: " + json.dumps({"type": "log", "run_id": run_id, "line": line}, ensure_ascii=False) + "\n\n"
            # 再持续接收新增行，直到收到结束哨兵
            while True:
                try:
                    payload = q.get(timeout=15)
                    yield payload
                    if '"end": true' in payload:
                        break
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            s.unsubscribe_log(run_id, q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "Connection": "keep-alive",
                                      "X-Accel-Buffering": "no"})


# ---------- 选择本机路径（系统原生对话框） ----------
# 浏览器出于安全无法拿到真实本地路径，故由「后端」在本机桌面弹出
# 系统原生文件/文件夹选择框（tkinter.filedialog），把选中的真实绝对
# 路径返回给前端填回表单。对话框会阻塞，故放后台线程并用队列回传。
def _run_native_picker(kind: str, title: str, initial: str, out: "queue.Queue"):
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()  # 隐藏空主窗口，只显示选择框
        root.attributes("-topmost", True)
        try:
            if kind == "dir":
                path = filedialog.askdirectory(title=title, initialdir=initial or None)
            else:
                initialdir = os.path.dirname(initial) if initial else None
                path = filedialog.askopenfilename(title=title, initialdir=initialdir or None)
        finally:
            root.destroy()
        out.put({"ok": True, "path": path or ""})
    except Exception as e:  # noqa: BLE001
        out.put({"ok": False, "error": str(e)})


@app.post("/api/fs/pick")
def fs_pick(payload: dict = Body(...)):
    """在后端主机桌面弹出系统原生「打开/选文件夹」对话框，返回真实绝对路径.

    body: { kind: "file" | "dir", title?: str, initial?: str }
    返回: { path }（用户取消时 path 为空字符串）；出错返回 500。
    """
    kind = "file" if payload.get("kind") not in ("file", "dir") else payload["kind"]
    title = payload.get("title") or ("请选择文件" if kind == "file" else "请选择文件夹")
    initial = payload.get("initial") or ""
    out: "queue.Queue" = queue.Queue()
    threading.Thread(target=_run_native_picker, args=(kind, title, initial, out), daemon=True).start()
    try:
        # 用户可能长时间在对话框内操作，最多等 10 分钟
        res = out.get(timeout=600)
    except queue.Empty:
        raise HTTPException(status_code=408, detail="选择超时")
    if not res.get("ok"):
        raise HTTPException(status_code=500, detail=f"打开选择框失败: {res.get('error')}")
    return {"path": res.get("path", ""), "cancelled": not res.get("path")}