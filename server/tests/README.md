# 后端测试套件

调度中心后端的 pytest 测试，共 **96 项**（参数化展开后），覆盖进程解耦、异常恢复、认证鉴权三部分。

---

## 快速开始

```bash
cd server
pip install -r requirements.txt   # 已含 pytest
pytest                            # 运行全部
```

常用命令：

```bash
pytest -v                         # 显示每个用例名
pytest tests/test_security.py     # 只跑某个文件
pytest -m security                # 按标记筛选
pytest -m "not slow"              # 跳过会真实起进程/等超时的用例
pytest -m source_contract         # 只跑源码契约断言
pytest -x                         # 首个失败即停止
pytest --collect-only             # 只列出用例，不执行
```

---

## 目录结构

```
server/
  pytest.ini                  # pytest 配置与自定义标记注册
  tests/
    __init__.py               # 包标识与模块职责说明
    conftest.py               # 环境初始化 + 共享 fixture
    test_process_decouple.py  # 进程生命周期解耦（23 项 → 33）
    test_phase2_recovery.py   # 异常恢复与状态收敛（26 项 → 29）
    test_security.py          # 认证鉴权与契约回归（12 项 → 34）
    README.md                 # 本文件
```

---

## 环境初始化（conftest.py）

`conftest.py` 在**导入任何 app 模块之前**完成环境变量设置，这是必须的：
`app/audit.py`、`app/auth.py`、`app/main.py` 在模块级就读取 `SC_DB`，导入后再改已无效。

| 变量 | 值 | 作用 |
|---|---|---|
| `SC_TOKEN` | `test-long-token-1234567890` | 固定长期凭证，不依赖 `data/sc_token` 文件 |
| `SC_DB` | 会话级临时目录下 `security.db` | 不污染真实数据库 |
| `SC_ALLOW_ORIGINS` | `http://127.0.0.1:9999` | 跨域白名单 |
| `SC_NODE_ID` | `unit-node` | 节点标识 |
| `SC_HEARTBEAT_INTERVAL_SEC` | `2` | 缩短心跳轮询，便于测超时 |
| `SC_HEARTBEAT_TIMEOUT_TIMES` | `2` | 缩短超时倍数 |
| `SC_HEARTBEAT_BATCH_SEC` | `60` | 避免批次循环干扰 |
| `SC_SAFE_ROOT` / `SC_EXEC_ROOT` | **显式清除** | 验证「不设路径白名单」的行为 |

### 可用 fixture

| fixture | 说明 |
|---|---|
| `tmp_dir` | 单项测试独立临时目录（pytest `tmp_path` 自动清理） |
| `sched` | 独立 `Scheduler` 实例，独立 db，结束后自动 `store.close()` |
| `alive_true` | 把 OS 探针改成「进程存活」 |
| `alive_false` | 把 OS 探针改成「进程已死」 |
| `client` | `TestClient(app)`（进程内，不占端口） |
| `auth_client` | 已登录的 `(client, headers)` |
| `capture_broadcast` | 拦截 `Scheduler.broadcast`，收集推送事件 |

---

## 标记（markers）

| 标记 | 含义 |
|---|---|
| `decouple` | 进程生命周期解耦相关 |
| `recovery` | 异常恢复与状态收敛 |
| `security` | 认证鉴权与安全 |
| `regression` | 原有功能契约回归 |
| `slow` | 会真实拉起进程或等待超时，耗时较长 |
| `source_contract` | **源码契约断言**：直接读取 `.py` 检查关键实现，对重构敏感 |

> `source_contract` 类用例通过字符串匹配校验实现存在性，改变量名或缩进可能误报。
> 它们验证的是「架构约束不能被悄悄删掉」，属于必要但脆弱的保护网。

---

## 一、test_process_decouple.py — 进程解耦（23 项 → 33）

验证「任务进程为什么能不受调度中心影响」的每一项技术前提。

### shell 判定

| 用例 | 作用 |
|---|---|
| `test_plain_command_no_shell` | 普通命令（`python run.py`、`ping`、`node app.js`）不应套 shell。**这是「记录的 PID 等于真实任务进程」的前提** —— 套了 shell 记录的会是 shell 的 PID。参数化 5 条命令 |
| `test_shell_syntax_needs_shell` | 管道 `\|`、重定向 `>`、串联 `&&`、`copy` 必须走 shell，否则语义改变。参数化 4 条 |
| `test_quoted_semicolon_not_shell` | `python -c "import time; time.sleep(1)"` 的分号在引号内属参数语法，**不能**触发 shell 包装 |
| `test_shell_builtin_needs_shell` | `echo`/`dir`/`set`/`cd` 是内建命令，磁盘上没有对应文件，必须交给 shell。参数化 4 条 |
| `test_split_preserves_quoted_args` | 参数拆分要保留引号内空格：`--name "a b"` 不能被切成 `a` 和 `b` |

### via_shell 标记

| 用例 | 作用 |
|---|---|
| `test_via_shell_flag_roundtrip` | `via_shell` / `log_offset` 能持久化往返，重启后不丢 |
| `test_via_shell_true_for_shell_command` | 走 shell 的命令可被识别，供停止/恢复按进程树处理 |

### 日志偏移续读

| 用例 | 作用 |
|---|---|
| `test_log_offset_avoids_replay` | 从 `log_offset` 续读：**不重复推送历史日志、不丢新增** |
| `test_log_truncation_resets` | 日志被截断/轮转（文件变短）时回到起点，而不是卡死在越界偏移 |
| `test_on_log_write_file_false_avoids_feedback` | 跟随读取的行**不得写回同一文件**，否则自我循环导致日志无限膨胀 |

### 进程创建标志

| 用例 | 作用 |
|---|---|
| `test_detach_flag_variants_ordered` | detach 标志有降级候选：首选含 `CREATE_BREAKAWAY_FROM_JOB`，末位去掉它。避免权限不足时彻底启动不了 |
| `test_non_detach_uses_no_window_only` | 非 detach 不应带 `DETACHED_PROCESS`，否则控制台行为异常。**仅 Windows 运行**，其他平台跳过 |

### 真实进程

| 用例 | 作用 |
|---|---|
| `test_real_process_writes_log_file` | 真跑 `python -c "print(12345)"`，确认 stdout **直写日志文件而非走 PIPE**，且退出码为 0。`slow` |

### shutdown 行为

| 用例 | 作用 |
|---|---|
| `test_shutdown_skips_detached_source` | 源码级：`shutdown()` 必须 `continue` 跳过 detach 任务，只中止普通任务。`source_contract` |

### 停止的安全性与诚实性

| 用例 | 作用 |
|---|---|
| `test_stop_refuses_on_identity_mismatch` | 身份核验失败时**拒绝终止且不谎报成功**（`ok:false`、`stop:false`），避免「显示已停止但进程还活着」 |
| `test_stop_reports_unknown_run` | 不存在的 run 明确返回 `run_not_found`，不静默失败 |
| `test_stop_uses_job_then_tree` | 停止优先用 Job 句柄整组终止，无句柄时回退进程树，并记录 `stop_via` 途径。`source_contract` |
| `test_shell_orphan_sweep_exists` | `via_shell` 任务停止时按 `ParentProcessId=` **精确清理孤儿**，不做全局扫描 |

### 环境与命名 Job

| 用例 | 作用 |
|---|---|
| `test_job_environment_probe` | Job 环境探测返回 `in_job`/`kill_on_close`/`breakaway_allowed`/`can_escape`，不抛异常 |
| `test_instance_job_no_kill_on_close` | **每实例命名 Job 必须 `LimitFlags = 0`**，不启用 `KILL_ON_JOB_CLOSE`。这是「调度中心退出不会带走任务」的技术前提。`source_contract` |
| `test_isolation_warning_field_roundtrip` | `isolation_warning` 可持久化，用于如实暴露环境约束 |

### 启动方式与一致性

| 用例 | 作用 |
|---|---|
| `test_headless_launcher_exists` | 存在 `run_headless.py` 且用 `schtasks` 计划任务；`run.py` 支持 `SC_PORT` 覆盖端口 |
| `test_terminal_run_reconcile_exists` | 存在终态记录一致性核对 `_reconcile_terminal_runs`（含 `verify_identity`），且被 `recover_on_start` 调用。`source_contract` |

---

## 二、test_phase2_recovery.py — 异常恢复（26 项 → 29）

验证「数据库里的状态」与「真实进程」如何保持一致。核心原则：
**是否判定进程死亡一律以 OS 探针实测为准，心跳仅作活性参考。**

### M1 数据结构与预登记

| 用例 | 作用 |
|---|---|
| `test_roundtrip_new_fields` | Phase 2 新增的 10 个字段（`node_id`/`process_create_time`/`adopted`/`retryable` 等）完整往返 |
| `test_old_row_defaults` | Phase 1 旧数据行缺新字段时有合理默认值，且 `"42"` → `42` 类型归一化 |
| `test_run_states_contain_new` | 新增状态 `STARTING`/`INTERRUPTED`/`ORPHANED`/`UNKNOWN` 已进 `STATUS` 与 `RUN_STATES`。参数化 4 条 |
| `test_running_runs_grouping` | `running_runs()` 返回运行类状态；`running_process_count()` **不计 UNKNOWN**（无法确认是否真实存在） |
| `test_trigger_starting_pre_registration` | 触发先落 `STARTING` 预登记，`on_started` 再推进 `RUNNING`，防崩溃丢失启动意图 |

### M2 启动恢复

| 用例 | 作用 |
|---|---|
| `test_residual_starting_no_identity_unknown` | 残留 `STARTING` 无身份凭据 → `UNKNOWN`（无法认领具体进程） |
| `test_phase1_leftover_running_unknown` | Phase-1 遗留 `RUNNING` 缺 `create_time` → `UNKNOWN`，不盲目判死也不盲目接管 |
| `test_dead_proc_interrupted` | 探针确认已死 → `INTERRUPTED`，写 `end` 与原因，**移出运行集** |
| `test_alive_orphan_resume` | 存活 `ORPHANED` → 保持 `ORPHANED` 并刷新心跳，等待四问接管 |
| `test_identity_alive_starting_running` | 有凭据且存活 `STARTING` → `RUNNING` |
| `test_recover_alive_running_registered_in_running_register` | **关键回归**：存活的 `RUNNING` 恢复后必须重新登记进 `self.running`，否则任务列表误判空闲、重复触发不被拦截（`trigger` 应返回 `blocked`） |
| `test_shutdown_skips_detached` | `shutdown()` 只中止非 detach 实例，detach 交给下次启动接管 |

### M3 心跳

| 用例 | 作用 |
|---|---|
| `test_within_timeout_boundary` | `_within_timeout` 边界：1 秒前在窗口内、100 秒前超出、非法日期判否 |
| `test_heartbeat_timeout_dead_interrupted` | 心跳超时 **+** 探针死亡 → `INTERRUPTED`（两者缺一不可） |
| `test_heartbeat_timeout_alive_no_kill` | 心跳超时 **但** 探针存活 → **保持 `RUNNING`，绝不判死** |

### M4 安全停止

| 用例 | 作用 |
|---|---|
| `test_safe_kill_identity_mismatch_refusal` | 身份不匹配（PID 可能被复用）→ 拒绝终止 |
| `test_safe_kill_no_credential` | 无身份凭据 → 拒绝 |
| `test_safe_kill_no_pid` | 无 PID → 拒绝并说明原因 |

### M5 四问接管

| 用例 | 作用 |
|---|---|
| `test_adopt_orphan_all_questions_pass` | 身份/监控/日志/停止四项全过 → `ORPHANED` 接管为 `RUNNING(adopted=True)` |
| `test_adopt_orphan_no_log_anchor` | 缺日志锚点（问题 3 不过）→ 保持 `ORPHANED`，`adopted=False` |
| `test_adopt_orphan_identity_mismatch` | 身份不匹配（PID 复用）→ 拒绝接管 |

### M7 SAFE-FIRST

| 用例 | 作用 |
|---|---|
| `test_safe_first_no_auto_restart` | `INTERRUPTED` 是终态：**不复活、不计运行、不自动重试**（`retryable` 预留字段不生效） |
| `test_safe_first_unknown_not_counted` | `UNKNOWN` 不计入实际运行进程数 |

### M6 运行视图

| 用例 | 作用 |
|---|---|
| `test_running_view_grouping` | `running_view` 的分组、`alive_total` 口径（`INTERRUPTED` 不计入）、Phase2 字段透出 |

### M-SSE 状态迁移广播

| 用例 | 作用 |
|---|---|
| `test_status_change_broadcast_fires` | 心跳超时收敛为 `INTERRUPTED` 时广播 `status_changed`，前端才能实时更新 |
| `test_starting_no_cred_broadcast` | 无凭据 `STARTING` 收敛为 `UNKNOWN` 时同样广播 |

---

## 三、test_security.py — 鉴权与契约（12 项 → 34）

### A. 鉴权边界

| 用例 | 作用 |
|---|---|
| `test_unauthorized_rejected` | **23 个受保护端点**未认证一律 401。参数化展开为 23 条独立用例，失败能精确定位到具体端点 |
| `test_public_open` | `health`/`constants` 无需认证；`constants` 暴露 `capabilities.fsPick=true`；错误凭证登录 401 |
| `test_login_logout_flow` | 登录 → 带 token 访问 200 → 登出 → 旧 token 立即失效（401） |
| `test_dangerous_endpoint_protection` | 危险端点在认证下可用，未认证被拒（含 `trigger`） |
| `test_login_throttle` | 连续 8 次错误凭证后进入冷却，**冷却期内即使给正确凭证也 401 并提示「频繁」** |
| `test_fs_pick_no_switch_authed` | `fs/pick` 无开关，认证后 200，未认证 401（用 monkeypatch 替换原生选择器） |
| `test_sse_unauthorized` | `/api/events` 与 `/api/runs/{id}/stream` 未认证 401 |
| `test_sse_authenticated_finite` | 带 Bearer 的 SSE 能消费到 `"end": true` 结束哨兵，验证认证握手。`slow` |

### B. 权限模型

| 用例 | 作用 |
|---|---|
| `test_fileop_anywhere` | 不设安全根：`FILEOP` 可删根外普通文件、`.git` 内文件、数据目录风格路径；目标不存在时报「不存在」而非安全根拒绝 |
| `test_script_program_outside_exec_root` | `PROGRAM` 可跑执行根外的解释器、`SCRIPT` 可跑根外脚本、`COMMAND` 不受限 |
| `test_nonzero_exit_interrupted` | **非 0 退出 → `INTERRUPTED`（不是 `FAILED`）**。`FAILED` 仅用于引擎自身异常（进程未真正启动、无退出码）；同时校验错误文案可解释、内部诊断前缀不污染用户日志 |

### B. 原有功能契约

| 用例 | 作用 |
|---|---|
| `test_original_features_regression` | `job-schema`/`groups`/`history`/`running`/任务 CRUD/审计 在认证后契约不变。`regression` |

---

## 与旧版手写测试的差异

旧版是手写 runner（`main()` 里维护函数清单 + 裸 `assert` + `print`），已迁移为 pytest：

| 方面 | 旧版 | pytest |
|---|---|---|
| 用例发现 | **手写清单**，新增用例忘加进列表就静默不跑 | 自动发现 |
| 跳过 | `print("[SKIP]"); return`，**被计为通过** | `@pytest.mark.skipif`，明确区分 skipped |
| 清理 | 中途失败则清理不执行，临时目录残留 | fixture + `tmp_path` 保证清理 |
| 参数化 | 手写 for 循环，只报首个失败 | `@parametrize`，每条独立报告 |
| 全局状态 | 模块级 monkeypatch，靠 `try/finally` 手动还原 | `monkeypatch` fixture 自动还原 |
| 隔离 | 共用 `_TMP`，db 名靠时间戳避开冲突 | 每项独立 `tmp_path` |

用例覆盖内容**逐一对应，无删减**：原 23 + 26 + 12 = 61 个检查项全部保留，
经参数化展开后共 96 条测试用例。

---

## 已知限制

- **前端零覆盖**：所有测试均为后端 Python 测试，看板统计、图表、状态徽章文案等前端逻辑没有自动化保护。
- **`source_contract` 类脆弱**：通过字符串匹配源码，重构（改变量名、调整缩进、拆分函数）可能误报。
- **`test_sse_authenticated_finite` 依赖时序**：等待任务结束的超时为 15 秒，机器极慢时可能不稳定。
- **平台差异**：部分用例仅 Windows 有意义（`test_non_detach_uses_no_window_only` 等），其他平台会自动跳过。
