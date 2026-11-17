# -*- coding: utf-8 -*-
"""APScheduler 任务注册与注销，以及启动配置加载。"""
from __future__ import annotations

import json
import logging

from apscheduler.triggers.cron import CronTrigger

from ..models import Job

logger = logging.getLogger("scheduler")


def _as_type(v) -> str:
    return str(v or "").strip().upper()


def _collect_job(job: Job, jobs: list, errors: list) -> None:
    errs = job.validate()
    if errs:
        errors.append(f"[{job.name}] " + "; ".join(errs))
    elif any(j.name == job.name for j in jobs):
        errors.append(f"重复的任务名: {job.name}")
    else:
        jobs.append(job)


def strip_json_comments(text: str) -> str:
    import re
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return text


class JobRegistration:
    """任务配置加载与 cron 注册。"""

    def __init__(self, sched) -> None:
        self._sched = sched

    def load_config(self) -> list:
        s = self._sched
        with open(s.config_path, "r", encoding="utf-8") as f:
            raw = f.read()
        raw = strip_json_comments(raw)
        data = json.loads(raw)

        jobs: list = []
        errors: list = []
        s.groups = {}

        if isinstance(data, dict):
            groups = data.get("groups")
            if groups is not None:
                for g in groups:
                    g_key = str(g.get("key") or g.get("name") or "")
                    g_task_type = _as_type(g.get("task_type"))
                    s.groups[g_key] = {
                        "key": g_key,
                        "name": str(g.get("name") or g_key),
                        "remark": str(g.get("remark") or ""),
                        "task_type": g_task_type,
                    }
                    for item in g.get("items", []):
                        entry = dict(item)
                        entry.setdefault("group", g_key)
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
            s.jobs = {j.name: j for j in jobs}
            s.store.upsert_jobs(jobs)
        return errors

    def register_all(self) -> None:
        s = self._sched
        s.aps.remove_all_jobs()
        for name, job in s.jobs.items():
            if job.enabled and job.schedule:
                try:
                    s.aps.add_job(
                        s._trigger_job,
                        CronTrigger.from_crontab(job.schedule),
                        args=[name, "cron"],
                        id=f"job_{name}",
                        replace_existing=True,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.error(f"Job[{name}] cron 注册失败: {e}")

    def register_job(self, job: Job) -> None:
        s = self._sched
        try:
            s.aps.remove_job(f"job_{job.name}")
        except Exception:  # noqa: BLE001
            pass
        if job.enabled and job.schedule:
            try:
                s.aps.add_job(
                    s._trigger_job,
                    CronTrigger.from_crontab(job.schedule),
                    args=[job.name, "cron"],
                    id=f"job_{job.name}",
                    replace_existing=True,
                )
            except Exception as e:  # noqa: BLE001
                logger.error(f"Job[{job.name}] cron 注册失败: {e}")

    def unregister_job(self, name: str) -> None:
        try:
            self._sched.aps.remove_job(f"job_{name}")
        except Exception:  # noqa: BLE001
            pass
