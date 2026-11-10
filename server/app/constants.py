
STATUS = {
    "PENDING": "待开始",
    "STARTING": "预登记中",
    "RUNNING": "进行中",
    "SUCCEEDED": "已完成",
    "FAILED": "失败",
    "TIMEDOUT": "已超时",
    "KILLED": "已中止",
    "INTERRUPTED": "已中断",
    "ORPHANED": "孤儿",
    "UNKNOWN": "待核查",
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