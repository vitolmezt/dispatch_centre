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
      PROGRAM:  path(程序路径,必填), args, workdir, env, detach(是否进程独立/后台常驻)
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
            {"key": "detach", "label": "进程独立", "required": False, "type": "bool"},
        ],
    },
    "SCRIPT": {
        "label": "脚本",
        "fields": [
            {"key": "path", "label": "脚本路径", "required": True, "type": "text"},
            {"key": "args", "label": "参数", "required": False, "type": "args"},
            {"key": "workdir", "label": "工作目录", "required": False, "type": "text"},
            {"key": "detach", "label": "进程独立", "required": False, "type": "bool"},
        ],
    },
    "PROGRAM": {
        "label": "程序",
        "fields": [
            {"key": "path", "label": "程序路径", "required": True, "type": "text"},
            {"key": "args", "label": "参数", "required": False, "type": "args"},
            {"key": "workdir", "label": "工作目录", "required": False, "type": "text"},
            {"key": "detach", "label": "进程独立", "required": False, "type": "bool"},
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
    created_at: Optional[str] = None  # 创建时间（ISO），首次落库时写入
    updated_at: Optional[str] = None  # 更新时间（ISO），每次保存时刷新

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
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
        )
        job.params = job._params(d)
        return job

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ServiceGroup:
    """一个「服务组」：把若干常驻(detach)任务编排为一键启动/停止的服务组.

    members 里的每一项引用一个【已存在的 Job】名（通常 detach=True 常驻），
    不重复定义子任务，仅描述"这一组要一起拉起哪些服务、以何顺序启动"。
    order 越小越先启动；start_group 按 order 升序逐个触发成员 Job。
    """

    name: str
    members: List[Dict[str, Any]] = field(default_factory=list)  # [{job_name, label, order}]
    description: str = ""
    created_at: Optional[str] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ServiceGroup":
        return cls(
            name=str(d.get("name") or ""),
            members=[dict(m) for m in (d.get("members") or [])],
            description=str(d.get("description") or ""),
            created_at=d.get("created_at"),
        )

    def validate(self) -> List[str]:
        errors: List[str] = []
        if not self.name:
            errors.append("name 不能为空")
        seen = set()
        for m in self.members:
            if not str(m.get("job_name") or ""):
                errors.append("存在空的任务名成员")
            jn = str(m.get("job_name") or "")
            if jn in seen:
                errors.append(f"存在重复的成员任务: {jn}")
            seen.add(jn)
        if not any(m.get("job_name") for m in self.members):
            errors.append("服务组至少需要一个成员任务")
        return errors

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "members": [dict(m) for m in self.members],
            "description": self.description,
            "created_at": self.created_at,
        }


@dataclass
class Run:
    id: str
    job_name: str
    job_type: str
    status: str = "PENDING"
    trigger: str = "MANUAL"
    pid: Optional[int] = None
    start: Optional[str] = None
    end: Optional[str] = None
    exit_code: Optional[int] = None
    timeout_s: int = 0
    log: List[str] = field(default_factory=list)
    log_path: Optional[str] = None
    error: str = ""
    node_id: str = ""
    process_create_time: Optional[str] = None
    executable_path: Optional[str] = None
    command_line_fingerprint: Optional[str] = None
    process_group_id: Optional[int] = None
    detached: bool = False
    last_heartbeat: Optional[str] = None
    interrupted_reason: str = ""
    adopted: bool = False
    retryable: bool = False

    def __post_init__(self):
        self.status = _as_run_state(self.status)
        self.trigger = str(self.trigger).strip().upper()
        self.job_type = _as_job_type(self.job_type)
        if self.pid is not None:
            self.pid = int(self.pid)
        if self.timeout_s is not None:
            self.timeout_s = int(self.timeout_s)
        if self.process_group_id is not None:
            self.process_group_id = int(self.process_group_id)
        self.detached = bool(self.detached)
        self.adopted = bool(self.adopted)
        self.retryable = bool(self.retryable)

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
        if len(self.log) >= 2000:
            self.log = self.log[-1999:]
        self.log.append(line)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Run":
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
            "log_path": self.log_path,
            "error": self.error,
            "node_id": self.node_id,
            "process_create_time": self.process_create_time,
            "executable_path": self.executable_path,
            "command_line_fingerprint": self.command_line_fingerprint,
            "process_group_id": self.process_group_id,
            "detached": self.detached,
            "last_heartbeat": self.last_heartbeat,
            "interrupted_reason": self.interrupted_reason,
            "adopted": self.adopted,
            "retryable": self.retryable,
        }
        if include_log:
            d["log"] = self.log
        return d


def job_to_api(job: Job, running_count: int = 0, last: Optional[Run] = None,
               is_running: bool = False) -> Dict[str, Any]:
    d = job.to_dict()
    d["enabled"] = job.enabled
    d["next_run_at"] = None
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
        return datetime.fromisoformat(s + "+00:00")