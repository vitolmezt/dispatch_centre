# -*- coding: utf-8 -*-
"""Store 门面"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..models import Job, Run, ServiceGroup
from .database import Database
from .group_repository import GroupRepository
from .job_repository import JobRepository
from .run_repository import RunRepository


class Store:

    def __init__(self, db_path: str) -> None:
        self._db = Database(db_path)
        self._jobs = JobRepository(self._db)
        self._runs = RunRepository(self._db)
        self._groups = GroupRepository(self._db)

    def upsert_jobs(self, jobs: List[Job]) -> None:
        return self._jobs.upsert_jobs(jobs)

    def load_jobs(self) -> Dict[str, Job]:
        return self._jobs.load_jobs()

    def delete_job(self, name: str) -> None:
        return self._jobs.delete_job(name)

    def rename_job(self, old_name: str, job: Job) -> None:
        return self._jobs.rename_job(old_name, job)

    def get_job(self, name: str) -> Optional[Job]:
        return self._jobs.get_job(name)

    def upsert_service_group(self, group: ServiceGroup) -> None:
        return self._groups.upsert_service_group(group)

    def load_service_groups(self) -> Dict[str, ServiceGroup]:
        return self._groups.load_service_groups()

    def get_service_group(self, name: str) -> Optional[ServiceGroup]:
        return self._groups.get_service_group(name)

    def delete_service_group(self, name: str) -> None:
        return self._groups.delete_service_group(name)

    def save_run(self, run: Run, heartbeat_only: bool = False) -> None:
        return self._runs.save_run(run, heartbeat_only=heartbeat_only)

    def query_runs(self, *, status=None, job_name=None, job_type=None, name_like=None,
                   sort=None, order="desc", offset=0, limit=200) -> List[Run]:
        return self._runs.query_runs(status=status, job_name=job_name, job_type=job_type,
                                     name_like=name_like, sort=sort, order=order,
                                     offset=offset, limit=limit)

    def count_runs(self, *, status=None, job_name=None, job_type=None,
                   name_like=None) -> int:
        return self._runs.count_runs(status=status, job_name=job_name,
                                     job_type=job_type, name_like=name_like)

    def get_runs(self, limit: int = 200, status: Optional[str] = None,
                 job_name: Optional[str] = None) -> List[Run]:
        return self._runs.get_runs(limit=limit, status=status, job_name=job_name)

    def get_run(self, run_id: str) -> Optional[Run]:
        return self._runs.get_run(run_id)

    def recent_runs(self, limit: int = 200) -> List[Run]:
        return self._runs.recent_runs(limit=limit)

    def running_runs(self) -> List[Run]:
        return self._runs.running_runs()

    def running_process_count(self) -> int:
        return self._runs.running_process_count()

    def delete_runs(self, ids: List[str]) -> int:
        return self._runs.delete_runs(ids)

    @staticmethod
    def new_run_id() -> str:
        return RunRepository.new_run_id()

    def close(self) -> None:
        return self._db.close()

    def _run_from_row(self, row) -> Optional[Run]:
        return self._runs.run_from_row(row)

    def _run_query(self, /, status=None, job_name=None, job_type=None,
                   name_like=None, sort=None, order="desc",
                   offset=None, limit=None):
        return self._runs.build_query(status=status, job_name=job_name,
                                      job_type=job_type, name_like=name_like,
                                      sort=sort, order=order,
                                      offset=offset, limit=limit)
