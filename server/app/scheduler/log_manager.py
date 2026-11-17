# -*- coding: utf-8 -*-
"""日志文件管理与日志推送。"""
from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from typing import Dict, List, Optional

from ..models import Run


class LogManager:

    def __init__(self, sched) -> None:
        self._sched = sched
        self._log_subscribers: Dict[str, List[queue.Queue]] = {}
        self._log_sub_lock = threading.Lock()

    def prepare_log_path(self, run: Run) -> str:
        if not run.log_path:
            base = self._sched._logs_dir if self._sched._logs_dir else Path("logs")
            run.log_path = str(Path(base) / f"{run.id}.log")
        Path(run.log_path).parent.mkdir(parents=True, exist_ok=True)
        return run.log_path

    def append_log_file(self, run: Run, line: str) -> None:
        logs_dir = self._sched._logs_dir
        if not logs_dir or not run.log_path:
            return
        try:
            path = Path(run.log_path)
            with path.open("a", encoding="utf-8", errors="replace") as f:
                f.write(line + "\n")
        except Exception:  # noqa: BLE001
            pass

    def read_log_file(self, run_id: str) -> Optional[List[str]]:
        logs_dir = self._sched._logs_dir
        if not logs_dir:
            return None
        path = logs_dir / f"{run_id}.log"
        try:
            if not path.exists():
                return None
            with path.open("r", encoding="utf-8", errors="replace") as f:
                return f.read().splitlines()
        except Exception:  # noqa: BLE001
            return None

    def on_log(self, run: Run, line: str, write_file: bool = True) -> None:
        run.append_log(line)
        if write_file:
            self.append_log_file(run, line)
        self.publish_log(run.id, line)

    def subscribe_log(self, run_id: str) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=500)
        with self._log_sub_lock:
            self._log_subscribers.setdefault(run_id, []).append(q)
        return q

    def unsubscribe_log(self, run_id: str, q: queue.Queue) -> None:
        with self._log_sub_lock:
            subs = self._log_subscribers.get(run_id)
            if subs:
                try:
                    subs.remove(q)
                except ValueError:
                    pass
                if not subs:
                    self._log_subscribers.pop(run_id, None)

    def publish_log(self, run_id: str, line: str) -> None:
        with self._log_sub_lock:
            subs = list(self._log_subscribers.get(run_id) or [])
        if not subs:
            return
        payload = (f"data: "
                   f"{json.dumps({'type': 'log', 'run_id': run_id, 'line': line}, ensure_ascii=False)}\n\n")
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

    def publish_log_end(self, run_id: str) -> None:
        with self._log_sub_lock:
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
