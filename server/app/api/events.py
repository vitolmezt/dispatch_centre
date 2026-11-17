# -*- coding: utf-8 -*-
"""事件流与文件选择器路由（3 个端点）。

GET  /api/events              全局 SSE 事件流（任务开始 / 结束 / 状态收敛）
GET  /api/runs/{run_id}/stream 单次执行的日志 SSE 流
POST /api/fs/pick             原生文件 / 目录选择器

SSE 行为约束（不得改动）：
  - 首帧发送 retry: 3000
  - 15s 无数据发送 ": keepalive"
  - 结束时发送 {"type":"log","run_id":...,"end":true}
"""
from __future__ import annotations

import json
import os
import queue
import threading

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse

from .. import audit
from .. import auth
from .dependencies import get_scheduler

router = APIRouter()

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@router.get("/api/events", dependencies=[Depends(auth.auth_required)])
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
                             headers=_SSE_HEADERS)


@router.get("/api/runs/{run_id}/stream", dependencies=[Depends(auth.auth_required)])
def run_log_stream(run_id: str):
    s = get_scheduler()

    mem = s.get_running(run_id)
    if mem is None:
        if s.store.get_run(run_id) is None and not s.read_log_file(run_id):
            raise HTTPException(status_code=404, detail="run 不存在")

        def done():
            yield "retry: 3000\n\n"
            yield "data: " + json.dumps(
                {"type": "log", "run_id": run_id, "end": True}, ensure_ascii=False) + "\n\n"
        return StreamingResponse(done(), media_type="text/event-stream",
                                 headers=_SSE_HEADERS)

    q = s.subscribe_log(run_id)

    def gen():
        try:
            yield "retry: 3000\n\n"
            for line in (mem.log if mem else []):
                yield "data: " + json.dumps(
                    {"type": "log", "run_id": run_id, "line": line},
                    ensure_ascii=False) + "\n\n"
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
                             headers=_SSE_HEADERS)


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
    except Exception as e:  # noqa: BLE001
        out.put({"ok": False, "error": str(e)})


@router.post("/api/fs/pick", dependencies=[Depends(auth.auth_required)])
def fs_pick(payload: dict = Body(...)):
    kind = "file" if payload.get("kind") not in ("file", "dir") else payload["kind"]
    title = payload.get("title") or ("请选择文件" if kind == "file" else "请选择文件夹")
    initial = payload.get("initial") or ""
    audit.audit("fs.pick", obj=kind, detail=f"initial={initial}")
    out: "queue.Queue" = queue.Queue()
    threading.Thread(target=_run_native_picker, args=(kind, title, initial, out),
                     daemon=True).start()
    try:
        res = out.get(timeout=600)
    except queue.Empty:
        raise HTTPException(status_code=408, detail="选择超时")
    if not res.get("ok"):
        raise HTTPException(status_code=500, detail=f"打开选择框失败: {res.get('error')}")
    return {"path": res.get("path", ""), "cancelled": not res.get("path")}
