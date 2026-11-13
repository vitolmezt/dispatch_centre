from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Optional, Dict

from fastapi import FastAPI, HTTPException, Query, Body, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from . import constants
from . import auth
from . import audit
from .scheduler import Scheduler
from .models import Job, JOB_PARAM_SCHEMA, ServiceGroup


def s_job_param_schema():
    return JOB_PARAM_SCHEMA

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


@app.get("/api/jobs", dependencies=[Depends(auth.auth_required)])
def list_jobs(type: Optional[str] = Query(None, alias="type"),
              enabled: Optional[str] = Query(None),
              q: Optional[str] = Query(None),
              sort: Optional[str] = Query(None),   # created_at | name，默认 created_at
              order: Optional[str] = Query("desc"),  # asc | desc
              page: int = Query(1, ge=1),
              page_size: int = Query(50, ge=1, le=500),
              all: bool = Query(False)):
    s = get_scheduler()
    if all:
        items = s.jobs_view(sort=sort, order=order)
        return {"total": len(items), "page": 1, "page_size": len(items), "items": items}
    offset = (page - 1) * page_size
    result = s.jobs_search(job_type=type, enabled=enabled, q=q, sort=sort, order=order,
                           offset=offset, limit=page_size)
    return {"total": result["total"], "page": page, "page_size": page_size, "items": result["items"]}

def _job_err(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


@app.post("/api/jobs", dependencies=[Depends(auth.auth_required)])
def create_job(payload: dict = Body(...)):
    s = get_scheduler()
    job = Job.from_dict(payload)
    errs = job.validate()
    if errs:
        raise _job_err("；".join(errs))
    if job.name in s.jobs:
        raise HTTPException(status_code=409, detail=f"任务名已存在: {job.name}")
    s.add_job(job)
    audit.audit("job.create", obj=job.name, detail=f"type={job.type}")
    return {"ok": True, "name": job.name}


@app.put("/api/jobs/{name}", dependencies=[Depends(auth.auth_required)])
def update_job(name: str, payload: dict = Body(...)):
    s = get_scheduler()
    old = s.jobs.get(name)
    if old is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {name}")
    data = dict(payload)
    # 允许改名：payload 里的 name 优先；未提供时沿用路径里的名字。
    # 注意不能无条件覆盖成路径名，否则改名会被静默丢弃。
    new_name = str(data.get("name") or "").strip() or name
    data["name"] = new_name
    if new_name != name and new_name in s.jobs:
        raise HTTPException(status_code=409, detail=f"任务名已存在: {new_name}")
    job = Job.from_dict(data)
    errs = job.validate()
    if errs:
        raise _job_err("；".join(errs))
    s.update_job(job, old_name=name)
    detail = f"type={job.type}" + (f" renamed_from={name}" if new_name != name else "")
    audit.audit("job.update", obj=new_name, detail=detail)
    return {"ok": True, "name": job.name, "renamed": new_name != name}


@app.delete("/api/jobs/{name}", dependencies=[Depends(auth.auth_required)])
def delete_job(name: str):
    s = get_scheduler()
    if name not in s.jobs:
        raise HTTPException(status_code=404, detail=f"任务不存在: {name}")
    s.delete_job(name)
    audit.audit("job.delete", obj=name)
    return {"ok": True, "name": name}


@app.post("/api/jobs/{name}/enable", dependencies=[Depends(auth.auth_required)])
def toggle_job(name: str, payload: dict = Body(...)):
    s = get_scheduler()
    job = s.jobs.get(name)
    if job is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {name}")
    enabled = bool(payload.get("enabled", True))
    try:
        s.set_job_enabled(name, enabled)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    audit.audit("job.enable", obj=name, detail=f"enabled={enabled}")
    return {"ok": True, "name": name, "enabled": enabled}


@app.get("/api/job-schema", dependencies=[Depends(auth.auth_required)])
def job_schema():
    return {"types": constants.JOB_TYPE, "schemas": s_job_param_schema()}


@app.get("/api/groups", dependencies=[Depends(auth.auth_required)])
def list_groups():
    return get_scheduler().groups_view()


@app.get("/api/service-groups", dependencies=[Depends(auth.auth_required)])
def list_service_groups():
    return get_scheduler().service_groups_view()


@app.post("/api/service-groups", dependencies=[Depends(auth.auth_required)])
def create_service_group(payload: dict = Body(...)):
    s = get_scheduler()
    group = ServiceGroup.from_dict(payload)
    errs = group.validate()
    if errs:
        raise _job_err("；".join(errs))
    if group.name in s.service_groups:
        raise HTTPException(status_code=409, detail=f"服务组已存在: {group.name}")
    s.add_service_group(group)
    audit.audit("service_group.create", obj=group.name, detail=f"members={len(group.members)}")
    return {"ok": True, "name": group.name}


@app.put("/api/service-groups/{name}", dependencies=[Depends(auth.auth_required)])
def update_service_group(name: str, payload: dict = Body(...)):
    s = get_scheduler()
    if name not in s.service_groups:
        raise HTTPException(status_code=404, detail=f"服务组不存在: {name}")
    data = dict(payload)
    data["name"] = name
    group = ServiceGroup.from_dict(data)
    errs = group.validate()
    if errs:
        raise _job_err("；".join(errs))
    s.update_service_group(group)
    audit.audit("service_group.update", obj=name, detail=f"members={len(group.members)}")
    return {"ok": True, "name": name}


@app.delete("/api/service-groups/{name}", dependencies=[Depends(auth.auth_required)])
def delete_service_group(name: str):
    s = get_scheduler()
    if name not in s.service_groups:
        raise HTTPException(status_code=404, detail=f"服务组不存在: {name}")
    s.delete_service_group(name)
    audit.audit("service_group.delete", obj=name)
    return {"ok": True, "name": name}


@app.post("/api/service-groups/{name}/start", dependencies=[Depends(auth.auth_required)])
def start_service_group(name: str):
    res = get_scheduler().start_service_group(name)
    if not res.get("ok"):
        raise HTTPException(status_code=404 if res.get("reason") == "not_found" else 400,
                            detail=res.get("errors") and "；".join(res["errors"]) or "启动失败")
    audit.audit("service_group.start", obj=name)
    return res


@app.post("/api/service-groups/{name}/stop", dependencies=[Depends(auth.auth_required)])
def stop_service_group(name: str):
    res = get_scheduler().stop_service_group(name)
    if not res.get("ok"):
        raise HTTPException(status_code=404 if res.get("reason") == "not_found" else 400,
                            detail=res.get("errors") and "；".join(res["errors"]) or "停止失败")
    audit.audit("service_group.stop", obj=name)
    return res


@app.get("/api/service-groups/{name}", dependencies=[Depends(auth.auth_required)])
def service_group_detail(name: str):
    s = get_scheduler()
    group = s.service_groups.get(name)
    if group is None:
        raise HTTPException(status_code=404, detail=f"服务组不存在: {name}")
    return s._service_group_view(group)


@app.get("/api/running", dependencies=[Depends(auth.auth_required)])
def list_running(type: Optional[str] = Query(None, alias="type"),
                 q: Optional[str] = Query(None),
                 status: Optional[str] = Query(None),
                 sort: Optional[str] = Query(None),
                 order: Optional[str] = Query("desc"),
                 page: int = Query(1, ge=1),
                 page_size: int = Query(100, ge=1, le=1000)):
    result = get_scheduler().running_view(job_type=type, q=q, status=status,
                                          sort=sort, order=order,
                                          offset=(page - 1) * page_size, limit=page_size)
    return {"total": result["total"], "page": page, "page_size": page_size,
            "items": result["items"], "alive_total": result["alive_total"],
            "counts": result["counts"]}

@app.get("/api/history", dependencies=[Depends(auth.auth_required)])
def history(status: Optional[str] = None, job: Optional[str] = None,
            type: Optional[str] = Query(None, alias="type"),
            q: Optional[str] = None,
            sort: Optional[str] = Query(None),
            order: Optional[str] = Query("desc"),
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


@app.get("/api/runs/{run_id}", dependencies=[Depends(auth.auth_required)])
def run_detail(run_id: str):
    s = get_scheduler()
    mem = s.get_running(run_id)
    if mem is not None:
        return mem.to_dict(include_log=True)
    run = s.store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run 不存在")
    return run.to_dict(include_log=True)


@app.post("/api/jobs/{name}/trigger", dependencies=[Depends(auth.auth_required)])
def trigger_job(name: str):
    result = get_scheduler().trigger(name)
    if result is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if result == "blocked":
        raise HTTPException(status_code=409, detail="该任务正在执行中，请等待其结束后再触发")
    if result == "disabled":
        raise HTTPException(status_code=403, detail="该任务已停用，无法触发")
    audit.audit("job.trigger", obj=name, detail=f"run={result.id}")
    return {"ok": True, "run_id": result.id, "trigger": result.trigger}


@app.post("/api/runs/{run_id}/abort", dependencies=[Depends(auth.auth_required)])
def abort_run(run_id: str, request: Request):
    ok = get_scheduler().abort(run_id)
    if not ok:
        raise HTTPException(status_code=404, detail="找不到可中止的运行实例")
    audit.audit("run.abort", operator=_bearer_token(request)[:6], obj=run_id)
    return {"ok": True}


@app.post("/api/runs/{run_id}/stop", dependencies=[Depends(auth.auth_required)])
def stop_run(run_id: str, request: Request):
    result = get_scheduler().stop(run_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail="找不到该运行记录")
    audit.audit("run.stop", operator=_bearer_token(request)[:6], obj=run_id,
                detail=result.get("reason", ""))
    return result


@app.delete("/api/history", dependencies=[Depends(auth.auth_required)])
def delete_history(payload: dict = Body(...)):
    s = get_scheduler()
    if payload.get("all"):
        rows = s.store.query_runs(limit=200000)
        n = s.store.delete_runs([r.id for r in rows])
    else:
        ids = payload.get("ids") or []
        if not isinstance(ids, list) or not ids:
            raise HTTPException(status_code=400, detail="ids 不能为空")
        n = s.store.delete_runs([str(i) for i in ids])
    audit.audit("history.delete", obj="history",
                detail=f"count={n} all={'all' if payload.get('all') else 'partial'}")
    return {"ok": True, "deleted": n}


@app.get("/api/constants")
def constants_dict():
    return {
        "groups": constants.ALL_GROUPS,
        "capabilities": {"fsPick": True},
    }


@app.get("/api/health")
def health():
    return {"status": "ok", "running": len(get_scheduler().running)}


@app.post("/api/login")
def login(payload: dict = Body(...), request: Request = None):
    ip = auth.client_ip(request) if request else "unknown"
    remaining = auth.login_locked(ip)
    if remaining > 0:
        audit.audit("login", obj=ip, detail=f"登录被限流（冷却剩余{int(remaining)}s）", ok=False)
        raise HTTPException(status_code=401, detail=f"登录尝试过于频繁，请 {int(remaining)} 秒后再试")
    provided = (payload.get("token") or "").strip()
    if not auth.token_configured():
        auth.record_login_fail(ip)
        audit.audit("login", obj=ip, detail="服务端未配置管理凭证", ok=False)
        raise HTTPException(status_code=401, detail="服务端未配置管理凭证")
    if not auth.authenticate(provided):
        auth.record_login_fail(ip)
        audit.audit("login", obj=ip, detail="登录凭证校验失败", ok=False)
        raise HTTPException(status_code=401, detail="凭证无效")
    auth.reset_login_fails(ip)
    sid = auth.issue_session()
    audit.audit("login", operator=sid[:6], obj=ip, detail="登录成功")
    return {"token": sid, "expires_in": int(auth.DEFAULT_SESSION_TTL), "configured": True}


@app.post("/api/logout", dependencies=[Depends(auth.auth_required)])
def logout(request: Request):
    sid = _bearer_token(request)
    if sid:
        auth.revoke_session(sid)
        audit.audit("logout", operator=sid[:6], detail="登出成功")
    return {"ok": True}


@app.get("/api/audit", dependencies=[Depends(auth.auth_required)])
def audit_logs(limit: int = Query(200, ge=1, le=5000)):
    return {"items": audit.read_recent(limit=limit)}


def _bearer_token(request: Request) -> str:
    h = request.headers.get("Authorization", "")
    b = "Bearer "
    if h.startswith(b):
        return h[len(b):].strip()
    return ""


@app.get("/api/events", dependencies=[Depends(auth.auth_required)])
def events():
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
@app.get("/api/runs/{run_id}/stream", dependencies=[Depends(auth.auth_required)])
def run_log_stream(run_id: str):
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
            for line in (mem.log if mem else []):
                yield "data: " + json.dumps({"type": "log", "run_id": run_id, "line": line}, ensure_ascii=False) + "\n\n"
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

def _run_native_picker(kind: str, title: str, initial: str, out: "queue.Queue"):
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
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
    except Exception as e:
        out.put({"ok": False, "error": str(e)})


@app.post("/api/fs/pick", dependencies=[Depends(auth.auth_required)])
def fs_pick(payload: dict = Body(...)):
    kind = "file" if payload.get("kind") not in ("file", "dir") else payload["kind"]
    title = payload.get("title") or ("请选择文件" if kind == "file" else "请选择文件夹")
    initial = payload.get("initial") or ""
    audit.audit("fs.pick", obj=kind, detail=f"initial={initial}")
    out: "queue.Queue" = queue.Queue()
    threading.Thread(target=_run_native_picker, args=(kind, title, initial, out), daemon=True).start()
    try:
        res = out.get(timeout=600)
    except queue.Empty:
        raise HTTPException(status_code=408, detail="选择超时")
    if not res.get("ok"):
        raise HTTPException(status_code=500, detail=f"打开选择框失败: {res.get('error')}")
    return {"path": res.get("path", ""), "cancelled": not res.get("path")}