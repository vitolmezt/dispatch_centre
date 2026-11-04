"""调度服务核心：加载配置 -> 注册定时任务 -> 分发执行 -> 记录状态.

对外暴露的职责（菜单 / 运行中 / 已完成 / 失败）：
  - jobs():                 当前可调度菜单项（含运行计数、最近一次结果）
  - running():              正在执行的实例
  - history(status?):       历史执行记录
  - trigger(name):          手动触发一次（api/manual）
  - abort(run_id):          中止一个运行实例
"""
from __future__ import annotations

import logging
import os
import queue
import threading
from datetime import datetime
from typing import Dict, List, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .executor import Executor
from .models import Job, Run, job_to_api
from .store import Store

logger = logging.getLogger("scheduler")


def _as_type(v) -> str:
    """归一化类型 */"""
    return str(v or "").strip().upper()


def _collect_job(job: Job, jobs: List[Job], errors: List[str]):
    """收集一个 Job，做校验与查重."""
    errs = job.validate()
    if errs:
        errors.append(f"[{job.name}] " + "; ".join(errs))
    elif any(j.name == job.name for j in jobs):
        errors.append(f"重复的任务名: {job.name}")
    else:
        jobs.append(job)


class Scheduler:
    def __init__(self, config_path: str, db_path: str):
        self.config_path = config_path
        self.store = Store(db_path)
        self.executor = Executor(self)
        self.jobs: Dict[str, Job] = {}
        self.groups: Dict[str, dict] = {}  # group_key -> {key,name,remark,task_type}
        self.running: Dict[str, Run] = {}  # run_id -> Run（进行中）
        self._lock = threading.RLock()
        # SSE 实时事件订阅者队列（每个连接一个 Queue）
        self._subscribers: List[queue.Queue] = []
        self._sub_lock = threading.Lock()

        # 后台调度器（定时触发 cron Job）
        self.aps = BackgroundScheduler()
        self.aps.add_listener(self._job_listener)

    # ---------- 回调（由 Executor 触发） ----------
    def on_started(self, run: Run):
        with self._lock:
            self.running[run.id] = run
        self.store.save_run(run)
        self.broadcast({"type": "run", "event": "started", "run_id": run.id,
                        "job_name": run.job_name, "status": run.status})

    def on_log(self, run: Run, line: str):
        # 为避免高频写库，日志先只在内存，运行结束后随 run 一起落库
        pass

    def on_finished(self, run: Run):
        with self._lock:
            self.running.pop(run.id, None)
        self.store.save_run(run)
        logger.info(f"Job[{run.job_name}] run[{run.id}] -> {run.status}")
        self.broadcast({"type": "run", "event": "finished", "run_id": run.id,
                        "job_name": run.job_name, "status": run.status})

    # ---------- SSE 实时事件推送 ----------
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=100)
        with self._sub_lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._sub_lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def broadcast(self, data: dict):
        """把一条事件推送(序列化后)分发到所有 SSE 订阅者. 队列满则丢弃最旧."""
        try:
            import json
            payload = f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
        except Exception:  # noqa: BLE001
            return
        with self._sub_lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                if q.full():
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        pass
                q.put_nowait(payload)
            except queue.Full:
                pass

    # ---------- 调度器事件 ----------
    def _job_listener(self, event):
        # apscheduler 会发 run_status 事件
        if event.code == 4096:  # EVENT_JOB_EXECUTED
            pass

    # ---------- 配置加载 ----------
    def load_config(self) -> List[str]:
        """读取并校验 config/jobs.json，返回校验错误列表.

        支持两种结构：
        A) crawler 风格分组结构（推荐）：
           { "groups": [ { key/name/remark/task_type, items: [ {key,name,type,params,...}, ... ] }, ... ] }
           items 里的每项会继承所在分组的 group / task_type。
        B) 旧平铺结构：{ "jobs": [ {...Job...} ] } 或裸数组。兼容迁移。
        """
        import json

        with open(self.config_path, "r", encoding="utf-8") as f:
            raw = f.read()
        raw = _strip_json_comments(raw)
        data = json.loads(raw)

        jobs: List[Job] = []
        errors: List[str] = []
        self.groups = {}

        if isinstance(data, dict):
            groups = data.get("groups")
            if groups is not None:
                # ---- A) 分组结构 ----
                for g in groups:
                    g_key = str(g.get("key") or g.get("name") or "")
                    g_task_type = _as_type(g.get("task_type"))
                    self.groups[g_key] = {
                        "key": g_key,
                        "name": str(g.get("name") or g_key),
                        "remark": str(g.get("remark") or ""),
                        "task_type": g_task_type,
                    }
                    for item in g.get("items", []):
                        entry = dict(item)
                        entry.setdefault("group", g_key)
                        # 组级 task_type 兜底到 item 的 type
                        if not entry.get("type") and g_task_type:
                            entry["type"] = g_task_type
                        job = Job.from_dict(entry)
                        _collect_job(job, jobs, errors)
            else:
                flat = data.get("jobs", [])
                if isinstance(flat, list):
                    for entry in flat:
                        job = Job.from_dict(entry)
                        _collect_job(job, jobs, errors)
                else:
                    errors.append("cfg 顶层需是 groups 或 jobs 数组")
        elif isinstance(data, list):
            for entry in data:
                job = Job.from_dict(entry)
                _collect_job(job, jobs, errors)
        else:
            errors.append("配置根节点必须是对象或数组")

        if not errors:
            self.jobs = {j.name: j for j in jobs}
            self.store.upsert_jobs(jobs)
        return errors

    def _register_all(self):
        self.aps.remove_all_jobs()
        for name, job in self.jobs.items():
            if job.enabled and job.schedule:
                try:
                    self.aps.add_job(
                        self._trigger_job,
                        CronTrigger.from_crontab(job.schedule),
                        args=[name, "cron"],
                        id=f"job_{name}",
                        replace_existing=True,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.error(f"Job[{name}] cron 注册失败: {e}")

    def start(self):
        # 先加载配置，注册定时任务
        errs = self.load_config()
        for e in errs:
            logger.error(f"配置错误: {e}")
        self._register_all()
        self.aps.start()

    def shutdown(self):
        # 中止所有运行中的实例
        for run_id in list(self.running.keys()):
            run = self.running.get(run_id)
            if run:
                self.abort(run_id)
        self.aps.shutdown(wait=False)

    # ---------- 内部触发 ----------
    def _trigger_job(self, job_name: str, trigger: str = "manual"):
        with self._lock:
            job = self.jobs.get(job_name)
            if job is None:
                return None
            run = Run(
                id=self.store.new_run_id(),
                job_name=job.name,
                job_type=job.type,
                trigger=trigger,
                timeout_s=job.timeout_s,
            )
        # 一个任务若已有一个在跑的新实例，可以简单释放（允许并发，多个进程无冲突）
        self.store.save_run(run)
        t = threading.Thread(target=self._run_worker, args=(run, job), daemon=True)
        t.start()
        return run

    def _run_worker(self, run: Run, job: Job):
        try:
            self.executor.run(run, job)
        except Exception as e:  # noqa: BLE001
            run.status = "FAILED"
            run.error = str(e)
            self.on_finished(run)

    # ---------- 对外 API ----------
    def trigger(self, job_name: str) -> Optional[Run]:
        with self._lock:
            if job_name not in self.jobs:
                return None
        return self._trigger_job(job_name, "api")

    def abort(self, run_id: str) -> bool:
        with self._lock:
            run = self.running.get(run_id)
            if run is None:
                return False
            return True
        # 通过 executor 中止底层进程（需要记录 Popen 引用）
        return self.executor.abort(run)

    def jobs_view(self) -> List[dict]:
        with self._lock:
            items = []
            for job in self.jobs.values():
                running_count = sum(1 for r in self.running.values() if r.job_name == job.name)
                last = self.store.get_runs(limit=1, job_name=job.name)
                last_run = last[0] if last else None
                items.append(job_to_api(job, running_count, last_run, running_count > 0))
            return items

    def jobs_search(self, *, job_type: Optional[str] = None, enabled: Optional[str] = None,
                    q: Optional[str] = None, offset: int = 0, limit: int = 50) -> dict:
        """按类型 / 启用 / 名称模糊 过滤菜单项并分页. 返回 {total, items}."""
        with self._lock:
            filtered = list(self.jobs.values())
        if job_type:
            filtered = [j for j in filtered if j.type.upper() == job_type.upper()]
        if enabled is not None and enabled != "":
            want = str(enabled).upper() in ("1", "YES", "ON", "TRUE")
            filtered = [j for j in filtered if j.enabled == want]
        if q:
            lq = q.lower()
            filtered = [j for j in filtered if lq in j.name.lower()]
        total = len(filtered)
        items = filtered[offset:offset + limit]
        out = []
        for job in items:
            running_count = sum(1 for r in self.running.values() if r.job_name == job.name)
            last = self.store.get_runs(limit=1, job_name=job.name)
            last_run = last[0] if last else None
            out.append(job_to_api(job, running_count, last_run, running_count > 0))
        return {"total": total, "items": out}

    def groups_view(self) -> List[dict]:
        """分组元信息列表（含每组任务计数）. 空分组(无任务)也可单独列出."""
        out = []
        for key, g in self.groups.items():
            count = sum(1 for j in self.jobs.values() if j.group == key)
            out.append({"key": key, **g, "count": count})
        return out

    def running_view(self, *, job_type: Optional[str] = None, q: Optional[str] = None,
                     offset: int = 0, limit: int = 100) -> dict:
        with self._lock:
            runs = list(self.running.values())
        if job_type:
            runs = [r for r in runs if r.job_type.upper() == job_type.upper()]
        if q:
            lq = q.lower()
            runs = [r for r in runs if lq in r.job_name.lower()]
        total = len(runs)
        # 运行中通常无分页，但保持与历史一致返回 total/items
        sliced = runs[offset:offset + limit]
        return {"total": total, "items": [r.to_dict(include_log=True) for r in sliced]}

    def history_view(self, status: Optional[str] = None, job_name: Optional[str] = None,
                     job_type: Optional[str] = None, q: Optional[str] = None,
                     offset: int = 0, limit: int = 50) -> dict:
        total = self.store.count_runs(status=status, job_name=job_name, job_type=job_type, name_like=q)
        runs = self.store.query_runs(status=status, job_name=job_name, job_type=job_type,
                                     name_like=q, offset=offset, limit=limit)
        return {"total": total, "items": [r.to_dict(include_log=True) for r in runs]}


def _strip_json_comments(text: str) -> str:
    """简易去除 // 行注释与 /* */ 块注释（适合 jsonc 配置）. 不做字符串内特殊处理足够本场景."""
    import re
    # 去掉 // 到行尾（避免误伤 http:// 等，这里放宽）
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return text