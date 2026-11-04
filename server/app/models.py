"""数据模型：Job(配置映射的菜单项) 与 Run(某次执行实例).

文档约定：Job.type / Run.status / Run.trigger 一律使用大写英文缩写
（见 constants.py），前端通过 /api/constants 的字典映射成中文显示。

配置风格（参考 crawler 项目）：
    每条 Job 先由 type 决定「菜的类型」，再在 params 里按类型写各自的参数。
    例：
      { "name": "打开记事本", "type": "PROGRAM",
        "params": { "path": "C:/Windows/System32/notepad.exe", "detach": true },
        "schedule": null, "enabled": true, "description": "..." }

    各类型 params 字段：
      COMMAND:  command(命令串,必填), workdir(工作目录), env
      SCRIPT:   path(脚本路径,必填), args, workdir, env
      PROGRAM:  path(程序路径,必填), args, workdir, env, detach(是否启动即成功)
      FILEOP:   action(delete_file|delete_dir|copy|move), source, target,
                recursive(仅删除目录用)
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from . import constants


def _as_job_type(v: Any) -> str:
    """归一化为大写英文缩写，例如 'script'、'SCRIPT' -> 'SCRIPT'."""
    return str(v).strip().upper()


def _as_run_state(v: Any) -> str:
    return str(v).strip().upper()


# 各类型 params 必填/可选字段说明（供前端展示 & 校验）
JOB_PARAM_SCHEMA: Dict[str, Dict[str, Any]] = {
    "COMMAND": {
        "label": "命令",
        "fields": [
            {"key": "command", "label": "命令", "required": True, "type": "text"},
            {"key": "workdir", "label": "工作目录", "required": False, "type": "text"},
        ],
    },
    "SCRIPT": {
        "label": "脚本",
        "fields": [
            {"key": "path", "label": "脚本路径", "required": True, "type": "text"},
            {"key": "args", "label": "参数", "required": False, "type": "args"},
            {"key": "workdir", "label": "工作目录", "required": False, "type": "text"},
        ],
    },
    "PROGRAM": {
        "label": "程序",
        "fields": [
            {"key": "path", "label": "程序路径", "required": True, "type": "text"},
            {"key": "args", "label": "参数", "required": False, "type": "args"},
            {"key": "workdir", "label": "工作目录", "required": False, "type": "text"},
            {"key": "detach", "label": "启动即成功", "required": False, "type": "bool"},
        ],
    },
    "FILEOP": {
        "label": "文件操作",
        "fields": [
            {"key": "action", "label": "动作", "required": True, "type": "select",
             "options": list(constants.FILEOP_ACTION.keys())},
            {"key": "source", "label": "源路径", "required": True, "type": "text"},
            {"key": "target", "label": "目标路径", "required": False, "type": "text"},
            {"key": "recursive", "label": "含子目录", "required": False, "type": "bool"},
        ],
    },
}


@dataclass
class Job:
    """一个可调度的任务定义（对应 config/jobs.json 中的一条 mapping）.

    name       任务名（唯一标识，对应 crawler 分组内 item 的 key）
    type       大写英文缩写：COMMAND | SCRIPT | PROGRAM | FILEOP
    group      所属分组标识（crawler 风格外层分组 key，如 system-commands）
    params     类型专属参数字典（见 JOB_PARAM_SCHEMA）
    schedule   cron 表达式，None 表示仅手动触发
    timeout_s  超时秒数，0 表示不限制
    enabled    是否启用
    description 说明
    """

    name: str
    type: str = "COMMAND"
    group: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    schedule: Optional[str] = None
    timeout_s: int = 0
    enabled: bool = True
    description: str = ""

    @property
    def executable(self) -> str:
        """该任务对应的可执行目标（供展示/执行）."""
        if self.type == "COMMAND":
            return str(self.params.get("command", ""))
        return str(self.params.get("path", ""))

    # ---- 兼容旧扁平字段：把 legacy 标签映射到 params ----
    def _params(self, d: Dict[str, Any]) -> Dict[str, Any]:
        params = dict(d.get("params") or {})
        # 兼容旧配置里扁平字段
        if "command" in d or "path" in d:
            legacy = {}
            for k in ("command", "path"):
                if d.get(k):
                    legacy[k] = d[k]
            if d.get("args"):
                legacy["args"] = [str(x) for x in d["args"]]
            if d.get("workdir"):
                legacy["workdir"] = d["workdir"]
            if "detach" in d:
                legacy["detach"] = d["detach"]
            # 新 params 优先，其次用 legacy 补齐
            legacy.update(params)
            params = legacy
        if d.get("env"):
            params.setdefault("env", {str(k): str(v) for k, v in d["env"].items()})
        return params

    def validate(self) -> List[str]:
        errors: List[str] = []
        if not self.name:
            errors.append("name 不能为空")
        if self.type not in constants.JOB_TYPES:
            errors.append(f"type 必须是 {constants.JOB_TYPES} 之一")
            return errors
        schema = JOB_PARAM_SCHEMA.get(self.type)
        fields = (schema or {}).get("fields", []) if schema else []
        for f in fields:
            if f.get("required") and not self.params.get(f["key"]):
                errors.append(
                    f"{self.type} 类型缺少必填参数: {f['label']}({f['key']})"
                )
        if self.timeout_s < 0:
            errors.append("timeout_s 不能为负")
        return errors

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Job":
        job = cls(
            name=str(d.get("key") or d.get("name") or ""),
            type=_as_job_type(d.get("type", "COMMAND")),
            group=str(d.get("group", "")),
            schedule=d.get("schedule"),
            timeout_s=int(d.get("timeout_s", 0) or 0),
            enabled=bool(d.get("enabled", True)),
            description=str(d.get("description", "")),
        )
        job.params = job._params(d)
        return job

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Run:
    """一次性执行实例."""

    id: str
    job_name: str
    job_type: str
    status: str = "PENDING"  # constants.RUN_STATES
    trigger: str = "MANUAL"  # MANUAL | CRON | API
    pid: Optional[int] = None
    start: Optional[str] = None
    end: Optional[str] = None
    exit_code: Optional[int] = None
    timeout_s: int = 0
    log: List[str] = field(default_factory=list)  # 按行追加（进程 stdout+stderr）
    error: str = ""

    def __post_init__(self):
        self.status = _as_run_state(self.status)
        self.trigger = str(self.trigger).strip().upper()
        self.job_type = _as_job_type(self.job_type)

    @property
    def duration_ms(self) -> Optional[int]:
        if not self.start:
            return None
        end = self.end or _end_now()
        try:
            s = _parse_local(self.start)
            e = _parse_local(end)
            return int((e - s).total_seconds() * 1000)
        except ValueError:
            return None

    def append_log(self, line: str):
        if len(self.log) >= 5000:  # 日志上限，防止内存膨胀
            self.log = self.log[-1000:]
        self.log.append(line)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Run":
        """从持久化/API 字典还原，只取 dataclass 声明字段，忽略 duration_ms 等计算属性."""
        import dataclasses
        allowed = {f.name for f in dataclasses.fields(cls)}
        kwargs = {k: v for k, v in d.items() if k in allowed}
        return cls(**kwargs)

    def to_dict(self, include_log: bool = True) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "job_name": self.job_name,
            "job_type": self.job_type,
            "status": self.status,
            "trigger": self.trigger,
            "pid": self.pid,
            "start": self.start,
            "end": self.end,
            "exit_code": self.exit_code,
            "timeout_s": self.timeout_s,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }
        if include_log:
            d["log"] = self.log
        return d


def job_to_api(job: Job, running_count: int = 0, last: Optional[Run] = None,
               is_running: bool = False) -> Dict[str, Any]:
    """菜单项 API 视图：配置 + 运行时信息 + 最近一次结果.

    若当前有该任务在运行(is_running)，"最近状态"展示进行中(RUNNING)，
    否则展示最近一次结束结果。
    """
    d = job.to_dict()
    d["enabled"] = job.enabled
    d["next_run_at"] = None  # 由调度器填充
    d["running_count"] = running_count
    d["params_schema"] = JOB_PARAM_SCHEMA.get(job.type, {})
    if is_running:
        d["last_status"] = "RUNNING"
        d["last_start"] = None
        d["last_end"] = None
        d["last_exit_code"] = None
    elif last:
        d["last_status"] = last.status
        d["last_start"] = last.start
        d["last_end"] = last.end
        d["last_exit_code"] = last.exit_code
    return d


def _end_now():
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_local(s: str):
    from datetime import datetime
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        # 兼容无时区的 UTC 字符串
        return datetime.fromisoformat(s + "+00:00")