"""全局常量定义 & 后端枚举字典.

规范：
- 后端 API 一律返回【大写英文缩写】（如 STATUS = "FAILED"、JOB_TYPE = "SCRIPT"）。
- 前端拿到英文 KEY 后，用这里的字典映射成显示文案（中文），实现「前端展示 -> 后端枚举」解耦。
- 每个分组都是 dict[英文KEY, 中文label]，可按需扩展分组与条目。
- 端点 GET /api/constants 会把 ALL_GROUPS 一次性交给前端作为全局只读字典。

如果你要加新常量，只需要在本文件新增/追加分组即可，前、后端无需改代码——前端从
/api/constants 拉取后自动得到全部组。
"""

# ---- 任务执行状态 (Run.status) ----
STATUS = {
    "PENDING": "待开始",
    "RUNNING": "进行中",
    "SUCCEEDED": "已完成",
    "FAILED": "失败",
    "TIMEDOUT": "已超时",
    "KILLED": "已中止",
}

# ---- 任务类型 (Job.type)，菜单里的「菜」 ----
JOB_TYPE = {
    "COMMAND": "命令",
    "SCRIPT": "脚本",
    "PROGRAM": "程序",
    "FILEOP": "文件操作",
}

# ---- 触发方式 (Run.trigger) ----
TRIGGER = {
    "MANUAL": "手动触发",
    "CRON": "定时触发",
    "API": "接口触发",
}

# ---- 结果判定（布尔/退出码语义） ----
RESULT = {
    "OK": "成功",
    "FAIL": "失败",
}

# ---- 启用开关 (Job.enabled) ----
ENABLED = {
    "ON": "已启用",
    "OFF": "已停用",
}

# ---- FILEOP 内建动作 (Job.params.action) ----
FILEOP_ACTION = {
    "DELETE_FILE": "删除文件",
    "DELETE_DIR": "删除目录",
    "COPY": "复制",
    "MOVE": "移动",
}

# ---- 分组内项目执行方式 (参考 crawler executer) ----
EXECUTER = {
    "LINE": "线性执行",
    "THREAD": "多线程执行",
    "PROCESS": "多进程执行",
}

# 全部分组：组名(大写英文) -> {英文KEY: 中文label}
ALL_GROUPS = {
    "STATUS": STATUS,
    "JOB_TYPE": JOB_TYPE,
    "TRIGGER": TRIGGER,
    "RESULT": RESULT,
    "ENABLED": ENABLED,
    "FILEOP_ACTION": FILEOP_ACTION,
    "EXECUTER": EXECUTER,
}

# 便捷别名（后端内部也便于引用）
RUN_STATES = tuple(STATUS.keys())
JOB_TYPES = tuple(JOB_TYPE.keys())