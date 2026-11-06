"""调度服务核心：加载配置 -> 注册定时任务 -> 分发执行 -> 记录状态.

对外暴露的职责（菜单 / 运行中 / 已完成 / 失败）：
  - jobs():                 当前可调度菜单项（含运行计数、最近一次结果）
  - running():              正在执行的实例
  - history(status?):       历史执行记录
  - trigger(name):          手动触发一次（api/manual）
  - abort(run_id):          中止一个运行实例
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
from datetime import datetime
from pathlib import Path
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
        # 已广播过 finished 的 run_id 集合：保证中止/结束只推送一次 finished 事件
        self._finished: set = set()
        # SSE 实时事件订阅者队列（每个连接一个 Queue）
        self._subscribers: List[queue.Queue] = []
        self._sub_lock = threading.Lock()
        # 按 run_id 定向的「日志流」订阅者：run 运行中每行日志实时推给订阅该 run 的连接.
        self._log_subscribers: Dict[str, List[queue.Queue]] = {}
        # 完整日志文件目录（在 db 同级的 data/logs 下），每条 run 一个 <run_id>.log 归档全量输出
        self._logs_dir = Path(db_path).parent / "logs"
        try:
            self._logs_dir.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            self._logs_dir = None

        # 后台调度器（定时触发 cron Job）
        self.aps = BackgroundScheduler()
        self.aps.add_listener(self._job_listener)

    # ---------- 回调（由 Executor 触发） ----------
    def on_started(self, run: Run):
        # 给 run 绑定归档日志文件路径（data/logs/<run_id>.log），供实时写文件与全量回读
        if self._logs_dir and not run.log_path:
            run.log_path = str(self._logs_dir / f"{run.id}.log")
        with self._lock:
            self.running[run.id] = run
        self.store.save_run(run)
        self.broadcast({"type": "run", "event": "started", "run_id": run.id,
                        "job_name": run.job_name, "status": run.status})

    def on_log(self, run: Run, line: str):
        # 实时日志：进内存（有界）+ 写归档文件 + 定向推给订阅该 run 的 SSE 连接
        run.append_log(line)
        self._append_log_file(run, line)
        self.publish_log(run.id, line)

    # ---------- 按 run 定向的日志流订阅（SSE） ----------
    def subscribe_log(self, run_id: str) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=500)
        with self._sub_lock:
            self._log_subscribers.setdefault(run_id, []).append(q)
        return q

    def unsubscribe_log(self, run_id: str, q: queue.Queue):
        with self._sub_lock:
            subs = self._log_subscribers.get(run_id)
            if subs:
                try:
                    subs.remove(q)
                except ValueError:
                    pass
                if not subs:
                    self._log_subscribers.pop(run_id, None)

    def publish_log(self, run_id: str, line: str):
        """把一行日志推给订阅了该 run 的所有连接；队列满则丢弃最旧，不阻塞."""
        with self._sub_lock:
            subs = list(self._log_subscribers.get(run_id) or [])
        if not subs:
            return
        payload = f"data: {json.dumps({'type': 'log', 'run_id': run_id, 'line': line}, ensure_ascii=False)}\n\n"
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

    def _append_log_file(self, run: Run, line: str):
        """把一行追加到 <run_id>.log 归档文件（UTF-8，环形 2000 行覆盖最旧）."""
        if not self._logs_dir or not run.log_path:
            return
        try:
            path = Path(run.log_path)
            with path.open("a", encoding="utf-8", errors="replace") as f:
                f.write(line + "\n")
        except Exception:  # noqa: BLE001
            pass

    def on_finished(self, run: Run):
        with self._lock:
            self.running.pop(run.id, None)
        self.store.save_run(run)
        logger.info(f"Job[{run.job_name}] run[{run.id}] -> {run.status}")
        self.publish_finished(run)
        self._publish_log_end(run.id)

    def _publish_log_end(self, run_id: str):
        """通知订阅该 run 日志流的连接：日志已结束（run 结束），流式连接可收尾."""
        with self._sub_lock:
            subs = list(self._log_subscribers.get(run_id) or [])
        if not subs:
            return
        payload = (f"data: "
                   f"{json.dumps({'type': 'log', 'run_id': run_id, 'end': True}, ensure_ascii=False)}\n\n")
        for q in subs:
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass

    def publish_finished(self, run: "Run"):
        """幂等推送 finished 事件：同一 run 只广播一次，避免 abort/超时与 worker 结束
        并发时重复推送，导致前端弹两次提示。"""
        with self._lock:
            if run.id in self._finished:
                return
            self._finished.add(run.id)
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
        # 数据源：数据库 jobs 表为唯一权威。首次升级时若库为空，则从 jobs.json 迁移一次。
        db_jobs = self.store.load_jobs()
        if db_jobs:
            self.jobs = db_jobs
        else:
            # 迁移：库中没有任务时，从旧的 jobs.json 导入（方便升级，不清空已有数据）
            errs = self.load_config()  # 仅首次迁移用：读取 jobs.json -> upsert 到 DB
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
            if not job.enabled:
                return None
            # 并发控制：同一任务同一时刻最多一个运行实例。
            # 在持锁状态下「同步预定」运行位，避免多个并发 HTTP 请求在 worker
            # 线程尚未执行 on_started 时都通过检查、导致重复触发（一次点一次又点一次）。
            if any(r.job_name == job_name for r in self.running.values()):
                return None
            run = Run(
                id=self.store.new_run_id(),
                job_name=job.name,
                job_type=job.type,
                trigger=trigger,
                timeout_s=job.timeout_s,
            )
            # 同步占位：立即标记为运行中，防止并发重复触发。
            self.running[run.id] = run
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
    def trigger(self, job_name: str):
        """触发一个任务。

        返回 None 表示任务不存在；返回 "blocked" 表示任务正在运行（全局并发上限=1，拒绝重复触发）；
        否则返回 Run 实例。前端据此给出不同提示：blocked 时提示「该任务正在执行…」。
        """
        with self._lock:
            if job_name not in self.jobs:
                return None
            if not self.jobs[job_name].enabled:
                return "disabled"
            if any(r.job_name == job_name for r in self.running.values()):
                return "blocked"
        return self._trigger_job(job_name, "api")

    def abort(self, run_id: str) -> bool:
        """中止一个运行中的实例。结束其进程并把该 run 标记为 KILLED.

        即使 worker 线程因阻塞读(stdout 等待)而粘住、无法立刻返回，这里也会主动把
        run 从运行表移除、落库并广播 finished(KILLED)，保证前端状态及时刷新。
        """
        with self._lock:
            run = self.running.get(run_id)
            if run is None:
                return False
        # 杀进程（优先 Popen 引用，若已脱离跟踪则退回按持久化 PID 杀，兼容阻塞读卡住的情况）
        self.executor.abort(run)
        self._finalize_user_stop(run, "用户中止", "KILLED")
        return True

    def stop(self, run_id: str) -> dict:
        """停止一个以 detach 方式启动的后台进程（按记录的 PID 定向 kill）.

        返回 dict：{ok, stop, run}，方便前端给出明确提示。
        与 abort() 的关键区别：这里不依赖内存中的 Popen 引用，而是从数据库
        读取该 run 存储的 PID，用 taskkill /PID 精准终止——即使调度中心曾重启、
        进程已脱离运行表，也能兜底清掉孤儿后台进程。
        """
        run = self.store.get_run(run_id)
        if run is None:
            return {"ok": False, "stop": False, "reason": "run_not_found"}
        pid = getattr(run, "pid", None)
        if not pid:
            return {"ok": True, "stop": False, "reason": "no_pid"}
        ok = self.executor.kill_by_pid(pid)
        # 只要进程已停（本次杀死 / 之前已不存在），都视为停止成功，并把仍在运行的 run 标记为 KILLED
        if not ok:
            return {"ok": True, "stop": False, "pid": pid, "reason": "kill_failed"}
        with self._lock:
            in_running = run.id in self.running
        if in_running:
            self._finalize_user_stop(run, "用户停止", "KILLED")
        return {"ok": True, "stop": True, "pid": pid,
                "reason": "killed" if in_running else "already_stopped"}

    def _finalize_user_stop(self, run: Run, reason: str, status: str):
        """把 run 结束为 status，移除运行表、落库、幂等广播 finished."""
        with self._lock:
            if run.id in self.running:
                cur = self.running[run.id]
            else:
                cur = run
            cur.status = status
            cur.error = reason
            cur.end = cur.end or datetime.now().astimezone().isoformat(timespec="seconds")
            self.running.pop(run.id, None)
        self.store.save_run(cur)
        self.publish_finished(cur)

    # ---------- 任务 CRUD（持久化到数据库 jobs 表，替代 jobs.json 热改） ----------
    def add_job(self, job: Job):
        with self._lock:
            self.jobs[job.name] = job
            self.store.upsert_jobs([job])
        self._register_job(job)

    def update_job(self, job: Job):
        with self._lock:
            existed = job.name in self.jobs
            self.jobs[job.name] = job
            self.store.upsert_jobs([job])
        self._register_job(job)
        if not existed:
            logger.info(f"created job {job.name} via update (formerly absent)")

    def delete_job(self, name: str):
        with self._lock:
            self.jobs.pop(name, None)
            self.store.delete_job(name)
        self._unregister_job(name)
        logger.info(f"deleted job {name}")

    def set_job_enabled(self, name: str, enabled: bool):
        with self._lock:
            job = self.jobs.get(name)
            if job is None:
                return False
            # 正在执行的任务不允许停用，避免调度语义混乱
            if not enabled and any(r.job_name == name for r in self.running.values()):
                raise ValueError(f"任务「{name}」正在执行，不能停用")
            job.enabled = enabled
            self.store.upsert_jobs([job])
        if enabled:
            self._register_job(job)
        else:
            self._unregister_job(name)
        logger.info(f"job {name} enabled={enabled}")
        return True

    def _register_job(self, job: Job):
        """注册/更新单个 Job 的 cron（若启用且配置了 schedule）."""
        try:
            self.aps.remove_job(f"job_{job.name}")
        except Exception:  # noqa: BLE001
            pass
        if job.enabled and job.schedule:
            try:
                self.aps.add_job(
                    self._trigger_job,
                    CronTrigger.from_crontab(job.schedule),
                    args=[job.name, "cron"],
                    id=f"job_{job.name}",
                    replace_existing=True,
                )
            except Exception as e:  # noqa: BLE001
                logger.error(f"Job[{job.name}] cron 注册失败: {e}")

    def _unregister_job(self, name: str):
        try:
            self.aps.remove_job(f"job_{name}")
        except Exception:  # noqa: BLE001
            pass

    def jobs_view(self, *, sort: Optional[str] = None, order: str = "desc") -> List[dict]:
        with self._lock:
            items = []
            for job in self.jobs.values():
                running_count = sum(1 for r in self.running.values() if r.job_name == job.name)
                last = self.store.get_runs(limit=1, job_name=job.name)
                last_run = last[0] if last else None
                items.append(job_to_api(job, running_count, last_run, running_count > 0))
        return self._sort_jobs_api(items, sort, order)

    def _sort_jobs_api(self, items: List[dict], sort: Optional[str], order: str) -> List[dict]:
        """对已组装的 job API 视图按字段排序（默认创建时间倒序，新任务在前）."""
        field = sort if sort in ("created_at", "name") else "created_at"
        reverse = order != "asc"
        if field == "name":
            items.sort(key=lambda d: (d.get("name") or ""), reverse=reverse)
        else:
            items.sort(key=lambda d: (d.get("created_at") or d.get("updated_at") or ""), reverse=reverse)
        return items

    def jobs_search(self, *, job_type: Optional[str] = None, enabled: Optional[str] = None,
                    q: Optional[str] = None, sort: Optional[str] = None, order: str = "desc",
                    offset: int = 0, limit: int = 50) -> dict:
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
        # 先组装 API 视图再排序（含全部，排序不受分页影响）
        out = []
        for job in filtered:
            running_count = sum(1 for r in self.running.values() if r.job_name == job.name)
            last = self.store.get_runs(limit=1, job_name=job.name)
            last_run = last[0] if last else None
            out.append(job_to_api(job, running_count, last_run, running_count > 0))
        out = self._sort_jobs_api(out, sort, order)
        return {"total": total, "items": out[offset:offset + limit]}

    def groups_view(self) -> List[dict]:
        """分组元信息列表（含每组任务计数）. 空分组(无任务)也可单独列出."""
        out = []
        for key, g in self.groups.items():
            count = sum(1 for j in self.jobs.values() if j.group == key)
            out.append({"key": key, **g, "count": count})
        return out

    def get_running(self, run_id: str):
        """返回内存中某运行实例（实时日志），不存在返回 None."""
        with self._lock:
            return self.running.get(run_id)

    def read_log_file(self, run_id: str) -> Optional[List[str]]:
        """读取某 run 的完整日志归档文件（若存在）为行列表；无文件返回 None."""
        if not self._logs_dir:
            return None
        path = self._logs_dir / f"{run_id}.log"
        try:
            if not path.exists():
                return None
            with path.open("r", encoding="utf-8", errors="replace") as f:
                return f.read().splitlines()
        except Exception:  # noqa: BLE001
            return None

    def _sort_key(self, obj, field: str):
        """按字段取排序键：时间字段优先按可比较字符串，缺失值排到末尾."""
        v = getattr(obj, field, None)
        if v is None:
            return ""
        return v  # ISO 时间字符串 / 名称字符串均可直接比较

    def running_view(self, *, job_type: Optional[str] = None, q: Optional[str] = None,
                     sort: Optional[str] = None, order: str = "desc",
                     offset: int = 0, limit: int = 100) -> dict:
        with self._lock:
            runs = list(self.running.values())
        if job_type:
            runs = [r for r in runs if r.job_type.upper() == job_type.upper()]
        if q:
            lq = q.lower()
            runs = [r for r in runs if lq in r.job_name.lower()]
        # 排序：默认按开始时间倒序（新启动的在最前）
        field = sort if sort in ("start", "name_created", "name") else "start"
        attr = {"start": "start", "name": "job_name"}.get(field, "start")
        runs.sort(key=lambda r: self._sort_key(r, attr), reverse=(order != "asc"))
        total = len(runs)
        # 运行中通常无分页，但保持与历史一致返回 total/items
        sliced = runs[offset:offset + limit]
        return {"total": total, "items": [r.to_dict(include_log=True) for r in sliced]}

    def history_view(self, status: Optional[str] = None, job_name: Optional[str] = None,
                     job_type: Optional[str] = None, q: Optional[str] = None,
                     sort: Optional[str] = None, order: str = "desc",
                     offset: int = 0, limit: int = 50) -> dict:
        total = self.store.count_runs(status=status, job_name=job_name, job_type=job_type, name_like=q)
        runs = self.store.query_runs(status=status, job_name=job_name, job_type=job_type,
                                     name_like=q, sort=sort, order=order, offset=offset, limit=limit)
        return {"total": total, "items": [r.to_dict(include_log=True) for r in runs]}


def _strip_json_comments(text: str) -> str:
    """简易去除 // 行注释与 /* */ 块注释（适合 jsonc 配置）. 不做字符串内特殊处理足够本场景."""
    import re
    # 去掉 // 到行尾（避免误伤 http:// 等，这里放宽）
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return text