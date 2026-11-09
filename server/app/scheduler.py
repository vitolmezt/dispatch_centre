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
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .executor import Executor
from . import identity as identity_mod
from .models import Job, Run, job_to_api, ServiceGroup
from .store import Store

logger = logging.getLogger("scheduler")


def _as_type(v) -> str:
    """归一化类型 */"""
    return str(v or "").strip().upper()


def _heartbeat_interval_sec() -> int:
    """心跳周期（秒）。默认 10s；可用 SC_HEARTBEAT_INTERVAL_SEC 覆盖."""
    try:
        return max(1, int(os.environ.get("SC_HEARTBEAT_INTERVAL_SEC", "10")))
    except (TypeError, ValueError):
        return 10


def _heartbeat_timeout_times() -> int:
    """心跳超时判定系数（连续多少次未收到心跳视为“异常需要核查”。默认 3）."""
    try:
        return max(1, int(os.environ.get("SC_HEARTBEAT_TIMEOUT_TIMES", "3")))
    except (TypeError, ValueError):
        return 3


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
        self.service_groups: Dict[str, ServiceGroup] = {}  # 服务组名 -> ServiceGroup
        self.running: Dict[str, Run] = {}  # run_id -> Run（进行中）
        self._lock = threading.RLock()
        # Phase 2：本机节点标识（默认取 hostname，可用 SC_NODE_ID 覆盖），用于身份落库归属
        # 注意：SC_NODE_ID 必须始终优先，即便平台没有 os.uname（如 Windows）。
        _env_node = os.environ.get("SC_NODE_ID")
        if _env_node:
            self.node_id = _env_node
        elif hasattr(os, "uname"):
            self.node_id = os.uname().nodename or "local"
        else:
            self.node_id = os.environ.get("COMPUTERNAME", "local") or "local"
        # Phase 2：心跳周期与超时系数（管理链路活性；OS 探针才是死亡权威）
        self.heartbeat_interval = _heartbeat_interval_sec()
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._heartbeat_stop = False
        # 已结束的总运行计数（无实际用途，仅保留位）
        self._heartbeat_runs: Dict[str, "Run"] = {}

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
        # Phase 2：STARTING -> RUNNING。身份凭据已由 executor 在 spawn 后采集，
        # 这里把"预登记"推进为"已运行并被管理"，并记录首次心跳。
        if run.status == "STARTING":
            run.status = "RUNNING"
            run.node_id = self.node_id
        run.last_heartbeat = datetime.now().astimezone().isoformat(timespec="seconds")
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

    def on_user_stopped(self, run: Run):
        """用户主动停止时，executor 读线程的专用收尾回调.

        stop/abort 已通过 _finalize_user_stop 完成状态落库、running 移除与 finished 广播（KILLED），
        因此这里只收尾日志流。注意：不能在这里 pop running，否则会抢在 _finalize_user_stop
        之前移除，导致 stop 的 in_running 判断失败而跳过 KILLED 落库。
        """
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
        self.service_groups = self.store.load_service_groups()
        # Phase 2 · 启动恢复扫描：在后台调度器启动前，先处理上次遗留下来的运行态
        # （RUNNING/STARTING/ORPHANED/UNKNOWN）。收敛死进程 / 认领孤儿 / 核查残留 STARTING。
        self.recover_on_start()
        # Phase 2 · 心跳/异常核查循环（仅登记管理态活跃运行，OS 探针才是死亡权威）
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, name="phase2-heartbeat", daemon=True)
        self._heartbeat_thread.start()
        self._register_all()
        self.aps.start()

    def shutdown(self):
        # Phase 2：停止心跳循环（避免后台线程在服务关闭后继续跑）
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_stop = True
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
            # Phase 2 · STARTING 预登记：
            #   在真正 spawn 之前先生成 run_id 并把"启动意图"落库为 STARTING，
            #   这样即使 spawn→落库间调度器崩溃，启动恢复也能扫描到这条残留 STARTING，
            #   而非静默丢失意图。node_id 先标记归属；身份凭据由 spawn 后采集填入。
            run.status = "STARTING"
            run.node_id = self.node_id
            run.detached = bool(job.params.get("detach", False))
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

    # ---------- Phase 2 · 启动恢复扫描 ----------
    def _adopt_orphan(self, run: Run):
        """detach 存活孤儿 → 四问接管判定（修订版3 §6.1，最终修正②）.

        四问（退出码不是接管前提，不能进程还跑着预取）：
          1. 进程身份 MATCH 且仍存活；
          2. 进程监控可重建（运行表登记 + 日志文件锚点）；
          3. 日志采集可恢复（日志文件存在可读 + 可靠文件尾续读）；
          4. 停止能力经安全验证（safe_kill 身份核验路径，不真的杀）。
        全过 -> (True, note)；任一不过 -> (False, note)。不做模糊认领。
        """
        # 1) 身份核验：必须是"同一进程"才谈接管。
        if not identity_mod.verify_identity(run, run.pid):
            return False, "身份核验失败(PID/创建时刻不匹配)，拒绝接管"
        # 2) 监控可重建：运行表目前不能登记 -> 恢复流程负责登记，故只要 log 锚点可写即可。
        if not run.log_path:
            return False, "缺少日志文件锚点，无法重建监控"
        # 3) 日志采集可恢复：日志文件存在且可读。
        if not os.path.isfile(run.log_path):
            return False, "日志文件不存在，无法恢复采集"
        try:
            with open(run.log_path, "a", encoding="utf-8", errors="replace"):
                pass
        except OSError as e:
            return False, f"日志文件不可追加，无法恢复采集: {e}"
        # 4) 停止能力安全验证：先核验身份；身份能证明才允许后续停止，不真的终止。
        if not identity_mod.verify_identity(run, run.pid):
            return False, "停止能力未通过身份核验，拒绝接管"
        return True, "四问全过"

    def recover_on_start(self):
        """服务(重新)启动时扫描遗留下来的运行态，做安全收敛与认领。

        处理对象：数据库里 status 属于 (STARTING, RUNNING, ORPHANED, UNKNOWN) 的记录。
        逐条：
          - 若具备有效身份凭据(process_create_time)且 OS 探针确认存活：
              + 该进程属于本节点的管理范围 -> 作业恢复为 RUNNING/ORPHANED 并继续管理。
              + detach 后台进程 -> 触发四问认领，能确认则标记 RUNNING(adopted)。
          - 若已死亡：按记录里的身份凭据核对；进程确已不在 -> 判定为中断/异常结束。
              + 曾标记 detach 且工具管理的进程异常结束 -> INTERRUPTED。
              + 其它自然收敛但未记录 exit_code 的 -> 归入历史(INTERRUPTED，原因=重启时确认已死)。
          - 残留 STARTING（spawn 前崩溃，没有任何身份凭据）：
              + 无法证明它当年曾拉起过任何进程 -> 保留 UNKNOWN 待核查，绝不盲杀。
        SAFE-FIRST：绝不自动重启、绝不模糊认领、绝不盲目 kill。无法证明归属一律按待核查处理。
        """
        candidates = self.store.running_runs()
        for run in candidates:
            try:
                self._recover_one(run)
            except Exception as e:  # noqa: BLE001
                logger.error(f"恢复 run[{run.id}] 时异常: {e}")

    def _recover_one(self, run: Run):
        # 1) 无有效身份凭据（如残留 STARTING / Phase-1 遗留 RUNNING）：
        #    STARTING：spawn 前崩溃，从未建立 run_id<->进程 绑定 -> 保留 UNKNOWN，绝不盲杀。
        #    RUNNING(Phase-1 遗留, 无 process_create_time)：身份缺失 -> 保守 UNKNOWN。
        if not run.process_create_time:
            if run.status == "STARTING":
                # STARTING 表示只有"启动意图"，没有可靠的 run_id->进程 关联。
                # 一律不按模糊匹配认领进程；转到 UNKNOWN 待人工/运维核查。
                run.status = "UNKNOWN"
                run.interrupted_reason = ("残留 STARTING 预登记，无有效身份凭据；"
                                          "启动意图已保存，无法认领具体进程，转待核查")
            elif run.status in ("RUNNING", "ORPHANED", "UNKNOWN"):
                # 无身份凭据的历史遗留 RUNNING -> 保守进入 UNKNOWN，不做任何处置。
                run.status = "UNKNOWN"
                run.interrupted_reason = ("Phase-1 遗留：缺少进程身份凭据(create_time)，"
                                          "无法安全核验，转待核查")
            self.store.save_run(run)
            return

        # 2) 具备身份凭据：用 OS 探针确认该进程目前是否存活/可复核
        alive = identity_mod.process_is_alive(run, run.pid)
        if alive:
            run.last_heartbeat = datetime.now().astimezone().isoformat(timespec="seconds")
            if run.status in ("STARTING",):
                # spawn 后崩溃残留：身份已建立且仍存活 -> 恢复到 RUNNING（同一凭据才可恢复）
                run.status = "RUNNING"
                run.interrupted_reason = ""
                self.store.save_run(run)
                logger.info(f"恢复 run[{run.id}] 存活 -> RUNNING")
                return
            if run.status == "UNKNOWN":
                # 待核查中进程仍存活且身份匹配 -> 恢复为 ORPHANED（继续管理，但不假成功）
                run.status = "ORPHANED"
                run.interrupted_reason = ("重启恢复：进程存活且身份匹配，转孤儿管理，"
                                          "待接管核查")
                self.store.save_run(run)
                logger.info(f"恢复 run[{run.id}] 存活 & 身份匹配 -> ORPHANED")
                return
            if run.status == "ORPHANED":
                # detach 存活孤儿 -> 走「四问接管」：身份/监控/日志/停止 全通过才重建 RUNNING。
                adopted, note = self._adopt_orphan(run)
                run.adopted = adopted
                if adopted:
                    run.status = "RUNNING"
                    run.interrupted_reason = ""
                else:
                    run.interrupted_reason = (run.interrupted_reason or
                                              "四问接管未全过: " + note)
                self.store.save_run(run)
                logger.info(f"恢复 run[{run.id}] ORPHANED -> {'RUNNING(接管)' if adopted else '保持ORPHANED'} {note}")
                return
            # RUNNING 且存活：维持原态（管理恢复）
            self.store.save_run(run)
            logger.info(f"恢复 run[{run.id}] RUNNING 存活 -> 保持")
            return

        # 3) 进程已不存活（OS 探针确认）。
        #    STARTING 且死：曾经 spawn 过但从未登记成功 -> 属于异常中断窗口内结束。
        #    有身份的 RUNNING/ORPHANED 死亡：确认异常/normal 结束，归入历史 INTERRUPTED。
        if run.status == "INTERRUPTED":
            return
        run.status = "INTERRUPTED"
        run.interrupted_reason = run.interrupted_reason or (
            "重启恢复：进程已确认不在运行（OS 探针核验），标记为中断并归入历史")
        run.end = run.end or datetime.now().astimezone().isoformat(timespec="seconds")
        run.last_heartbeat = datetime.now().astimezone().isoformat(timespec="seconds")
        self.store.save_run(run)
        logger.info(f"恢复 run[{run.id}] 已确认结束 -> INTERRUPTED")

    # ---------- Phase 2 · 心跳 / 异常核查循环 ----------
    def _heartbeat_loop(self):
        """后台心跳循环：周期刷新存活运行的管理态心跳；超时者触发 OS 核查。"""
        while not self._heartbeat_stop:
            try:
                self._heartbeat_tick()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"心跳 tick 异常: {e}")
            for _ in range(self.heartbeat_interval):
                if self._heartbeat_stop:
                    return
                time.sleep(1)

    def _heartbeat_tick(self):
        """周期性在管理态的活跃运行上打心跳，并对连续超时的触发 OS 核查.

        重要：心跳只反映"管理链路活性"。真正判定死亡必须 OS 探针(pid+身份凭据)。
        单次未刷新心跳绝不直接判死；连续超时也只是"进入需要 OS 核查"的候选，
        最终以探针结果为准。
        """
        runs = self.store.running_runs()
        # 只对"确认在管理/存活"的状态打心跳；UNKNOWN/INTERRUPTED 不纳入心跳刷新
        managed = [r for r in runs
                   if r.status in ("RUNNING", "ORPHANED", "STARTING")]
        now_s = datetime.now().astimezone().isoformat(timespec="seconds")
        timeout_sec = self.heartbeat_interval * _heartbeat_timeout_times()

        for r in managed:
            # 打心跳：只是把管理链路活性写盘（合并写，防写放大）
            r.last_heartbeat = now_s
            self.store.save_run(r, heartbeat_only=True)
            # 超时核查：距上次心跳超过阈值 -> 必须用 OS 探针确认进程是否真的死了
            hb = r.last_heartbeat or r.start
            if not self._within_timeout(hb, timeout_sec):
                self._check_heartbeat_timeout(r)

    @staticmethod
    def _within_timeout(ts, timeout_sec: float) -> bool:
        try:
            from datetime import datetime as _dt
            t = _dt.fromisoformat(ts.replace("Z", "+00:00"))
            return (datetime.now().astimezone() - t).total_seconds() <= timeout_sec
        except Exception:  # noqa: BLE001
            return False

    def _check_heartbeat_timeout(self, run: Run):
        """心跳连续超时 -> 用 OS 探针复核该进程是否真死.

        唯一死亡权威是 OS 探针；若探针仍确认存活（身份匹配），仅刷新心跳不判死。
        若探针确认已不在，则标记为 INTERRUPTED 并归入历史（管理链路与 OS 一致才算真死）。
        """
        if identity_mod.process_is_alive(run, run.pid):
            # 探针确认仍存活：只是管理链路心跳被合并写/网络暂停，不判死
            run.last_heartbeat = datetime.now().astimezone().isoformat(timespec="seconds")
            self.store.save_run(run, heartbeat_only=True)
            return
        # 探针确认进程已不在运行 -> 明确中断
        if run.status == "INTERRUPTED":
            return
        run.status = "INTERRUPTED"
        run.interrupted_reason = (run.interrupted_reason or
                                  "心跳连续超时且 OS 探针确认进程已不在运行，判定中断")
        run.end = run.end or datetime.now().astimezone().isoformat(timespec="seconds")
        self.store.save_run(run)
        logger.warning(f"心跳超时 + OS 探针确认死亡: run[{run.id}] -> INTERRUPTED")

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
        """停止一个以 detach 方式启动的后台进程（身份核验后安全停止）.

        返回 dict：{ok, stop, run, reason}，方便前端给出明确提示。
        与 abort() 的关键区别：这里不依赖内存中的 Popen 引用，而是从数据库
        读取该 run 存储的 PID 与身份凭据，先复核"该 PID 确实就是当初那个进程"，
        再定向 kill——即使调度中心曾重启、进程已脱离运行表，也能安全清掉孤儿后台进程。

        身份核验失败（PID 复用/无凭据）时拒绝操作，绝不盲杀；无法证明归属一律视为失败。
        """
        run = self.store.get_run(run_id)
        if run is None:
            return {"ok": False, "stop": False, "reason": "run_not_found"}
        pid = getattr(run, "pid", None)
        if not pid:
            return {"ok": True, "stop": False, "reason": "no_pid"}
        # Phase 2 · 安全停止：安全停止优先调用身份核验后的 safe_kill（默认安全优先）。
        # 失败原因统一反馈，不因"进程已不存在"假成功。
        reason = identity_mod.safe_kill(run, pid, group_id=run.process_group_id)
        if reason is not None:
            # 若是进程早已不在，属于正常停止；否则核验失败拒绝
            if identity_mod.process_is_alive(run, pid):
                return {"ok": False, "stop": False, "pid": pid, "reason": reason}
            # 进程已不存在 -> 目标"让它停"已达成
            reason = None
        # 已登记用户主动停止：读线程不得用退出码覆盖状态栏
        self.executor.mark_user_stopped(run.id)
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
        self._publish_log_end(run.id)

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

    # ---------- 服务组编排（基于现有 detach 任务） ----------
    def service_groups_view(self) -> List[dict]:
        """服务组列表，含每个成员的实时运行状态（当前 run、状态、PID）."""
        with self._lock:
            groups = list(self.service_groups.values())
        return [self._service_group_view(g) for g in groups]

    def _service_group_view(self, group: ServiceGroup) -> dict:
        """单个服务组的 API 视图：基本信息 + 每成员当前（若有）运行实例状态."""
        with self._lock:
            members = []
            for m in sorted(group.members, key=lambda x: int(x.get("order", 0))):
                jn = str(m.get("job_name") or "")
                job = self.jobs.get(jn)
                run = next((r for r in self.running.values()
                            if r.job_name == jn and r.status == "RUNNING"), None)
                members.append({
                    "job_name": jn,
                    "label": m.get("label") or jn,
                    "order": int(m.get("order", 0)),
                    "exists": job is not None,
                    "run_id": run.id if run else None,
                    "status": run.status if run else "STOPPED",
                    "pid": run.pid if run else None,
                    "start": run.start if run else None,
                    "last": self._group_member_last(jn),
                })
        return {
            "name": group.name,
            "description": group.description,
            "created_at": group.created_at,
            "members": members,
            # 组整体运行状态：任一成员在运行即视为 running
            "running": any(m["status"] == "RUNNING" for m in members),
        }

    def _group_member_last(self, job_name: str) -> Optional[dict]:
        last = self.store.get_runs(limit=1, job_name=job_name)
        return (last[0].to_dict(include_log=False).get("status") if last else None)

    def add_service_group(self, group: ServiceGroup):
        with self._lock:
            self.service_groups[group.name] = group
            self.store.upsert_service_group(group)
        logger.info(f"service_group created: {group.name} ({len(group.members)} members)")

    def update_service_group(self, group: ServiceGroup):
        with self._lock:
            self.service_groups[group.name] = group
            self.store.upsert_service_group(group)
        logger.info(f"service_group updated: {group.name}")

    def delete_service_group(self, name: str):
        with self._lock:
            self.service_groups.pop(name, None)
            self.store.delete_service_group(name)
        logger.info(f"service_group deleted: {name}")

    def start_service_group(self, name: str) -> dict:
        """一键启动服务组：按成员 order 升序，逐个触发其底层 Job（常驻 detach）.

        各成员 Job 互不相同，天然不触碰单任务并发上限。返回启动的成员 run 映射。
        """
        with self._lock:
            group = self.service_groups.get(name)
        if group is None:
            return {"ok": False, "reason": "not_found"}
        started = []
        errors = []
        for m in sorted(group.members, key=lambda x: int(x.get("order", 0))):
            jn = str(m.get("job_name") or "")
            with self._lock:
                job = self.jobs.get(jn)
                if job is None:
                    errors.append(f"{jn}: 任务不存在")
                    continue
                if any(r.job_name == jn and r.status == "RUNNING"
                       for r in self.running.values()):
                    # 已运行：视为已启动，跳过重复拉起（幂等）
                    run = next(r for r in self.running.values()
                               if r.job_name == jn and r.status == "RUNNING")
                    started.append({"job_name": jn, "run_id": run.id, "status": "already_running"})
                    continue
            run = self._trigger_job(jn, "api")
            started.append({"job_name": jn, "run_id": run.id if run else None,
                            "status": "started" if run else "failed"})
            if run is None:
                errors.append(f"{jn}: 启动失败")
        return {"ok": not errors, "name": name, "started": started, "errors": errors}

    def stop_service_group(self, name: str) -> dict:
        """一键停止服务组：遍历组内正在运行的成员 run，逐个按持久化 PID 整树 kill."""
        with self._lock:
            group = self.service_groups.get(name)
        if group is None:
            return {"ok": False, "reason": "not_found"}
        stopped = []
        errors = []
        for m in group.members:
            jn = str(m.get("job_name") or "")
            with self._lock:
                run = next((r for r in self.running.values()
                            if r.job_name == jn and r.status == "RUNNING"), None)
            if run is None:
                continue  # 未运行的不需要停
            res = self.stop(run.id)
            if res.get("ok"):
                stopped.append({"job_name": jn, "run_id": run.id,
                                "stopped": res.get("stop"),
                                "reason": res.get("reason")})
            else:
                errors.append(f"{jn}: 停止失败 ({res.get('reason')})")
        return {"ok": not errors, "name": name, "stopped": stopped, "errors": errors}

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
        """执行中/待核查视图（Phase 2）.

        数据源为 store.running_runs()：包含 STARTING / RUNNING / ORPHANED /
        UNKNOWN 分组（这些状态留在"运行/执行中"区展示；INTERRUPTED 归入历史）。
        附带 alive_total（== running_process_count，仅统计已确认存活或管理中的
        状态，UNKNOWN 不计）与按 status 的分组 counts。
        """
        runs = list(self.store.running_runs())
        if job_type:
            runs = [r for r in runs if r.job_type.upper() == job_type.upper()]
        if q:
            lq = q.lower()
            runs = [r for r in runs if lq in r.job_name.lower()]
        field = sort if sort in ("start", "name_created", "name") else "start"
        attr = {"start": "start", "name": "job_name"}.get(field, "start")
        runs.sort(key=lambda r: self._sort_key(r, attr), reverse=(order != "asc"))
        total = len(runs)
        sliced = runs[offset:offset + limit]
        counts: Dict[str, int] = {}
        for r in runs:
            counts[r.status] = counts.get(r.status, 0) + 1
        return {
            "total": total,
            "items": [r.to_dict(include_log=True) for r in sliced],
            "alive_total": self.store.running_process_count(),
            "counts": counts,
        }

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