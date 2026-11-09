# Phase 2 实施详细方案 —— 状态一致性与异常恢复（修订版 3）

基线提交：`6d348ac`（Phase 1 安全加固，通过 12 项安全测试）
目标：单机版 `scheduler-center` 补齐"进程身份持久化、启动恢复、孤儿与中断识别、心跳、安全停止、detach 差异恢复、新状态全栈同步、SAFE-FIRST 重试"等状态一致性与异常恢复能力。
范围边界（本阶段明确不做）：不引入 Worker / 多机 / MQ / Redis / HA / 目录树重构 / 工具链替换；不改 Phase 1 认证与文件安全根。

> 修订版 3 已按【最终修正 5 项 + 其他要求】落实：统一非 detach 状态语义、detach 五问修正（退出码非恢复前提）、UNKNOWN 在执行中心归属、Windows/Linux 安全停止最终明确、spawn→持久化异常窗口加入 STARTING 预登记。评审通过后方可改代码。

---

## 1. 现状代码存在的具体问题

| # | 问题 | 所在文件 | 影响 |
|---|------|---------|------|
| P1 | 进程身份只存在于运行时 `Popen` 引用与 `Run.pid`，不下持久化。重启即丢失身份凭据 | `executor.py:273`、`models.py:227` | 无法恢复管理；PID-only 停杀有误杀风险 |
| P2 | `Run` 只有 `pid`，无创建标识/路径/指纹/节点/心跳/detached/日志，无法可靠身份核验 | `models.py:218-286` | 不满足 §三.1 |
| P3 | 无心跳；也无法区分"运行表残留假 RUNNING""管理链路失联" | `executor.py`、`scheduler.py` | 不满足 §三.4 |
| P4 | 启动不做恢复扫描；也无 STARTING 预登记，spawn↔落库窗口崩溃即产生"进程活但无身份记录" | `main.py`、`executor.py`、`store.py` | 不满足 §三.2 / 最终修正⑤ |
| P5 | 停止只按 PID 定向 kill，未经身份核验；PID 复用误杀 | `scheduler.py:385`、`executor.py:336` | 不满足 §三.5 |
| P6 | 无孤儿/中断/未知态 | `constants.py`、`models.py` | 不满足 §三.3/§三.7 |
| P7 | 无统一进程树/进程组安全终止入口 | `executor.py` | 不满足 §三.5 |
| P8 | detach 与普通任务同路径 spawn；崩溃后不可恢复原 stdout 管道；退出码是否作为恢复前提未厘清 | `executor.py`、`scheduler.py` | 不满足 §三.6 |
| P9 | 状态过滤、历史筛选、SSE 缺新状态；前端硬编码分支需同步 | `constants.py`、`web/src/views` | 不满足 §三.7 |
| P10 | `to_dict()` 白名单序列化，新增字段不加入被丢弃；`save_run` 全表重写写放大 | `models.py:268`、`store.py:169` | 身份字段丢失；心跳写放大 |
| P11 | 无 SAFE-FIRST 重试建模 | `scheduler.py` | 不满足 §三.8 |

---

## 2. 需要修改的文件

| 文件 | 改动 |
|------|------|
| `server/app/models.py` | `Run` 增加身份/生命周期字段 + `STARTING` 状态；`to_dict()/from_dict()` 同步；定义 `ProcessIdentity` |
| `server/app/store.py` | `save_run` 心跳节流（`heartbeat_only`）；批量收敛助手；`running_runs` 按新归属取数 |
| `server/app/executor.py` | 独立会话/PGID；spawn 前 STARTING 预登记→身份固化→RUNNING；OS 探测 + 严格身份核验；进程树/组安全停止（竞态缓解）；detach 日志文件化 |
| `server/app/scheduler.py` | startup 恢复扫描（先于调度）；孤儿对账；SAFE-FIRST；新状态收尾与广播 |
| `server/app/main.py` | startup 显式"恢复扫描→再 aps.start"；新增只读身份 API 与孤儿 reclaim；透出新状态 |
| `server/app/constants.py` | `STATUS` 追加 `STARTING/INTERRUPTED/ORPHANED/UNKNOWN`；心跳配置读取 |
| `server/selfcheck.py` | 追加恢复收敛/心跳/新状态自检（原步骤保持通过） |
| `server/test_security.py` | 保持 12 项原样通过，断言只追加 |
| `server/test_phase2_recovery.py`（新增） | 10 项验收 + Windows/Linux 恢复 + STARTING 窗口 + Running/History 归属断言 |
| `web/src/views/*` | 新状态在历史筛选/运行看板（UNKNOWN 计入待核查、不计运行数）/角标/详情标签 |
| `docs/phase2-plan.md` | 本方案 |

---

## 3. 数据库字段 / 表设计

### 3.1 存储策略（保留）
沿用 run_json + 少量索引列；新增字段进 `Run` dataclass 随 `to_dict()` 序列化，**无强制 ALTER TABLE**；旧记录 `__post_init__` 默认补齐。新状态仍走既有 `status` 列过滤；`node_id` 本机常量不入索引。

### 3.2 `Run` 新增字段
| 字段 | 类型 | 说明 |
|------|------|------|
| `node_id` | `str` | 本机 = `HOSTNAME`，预留多机来源 |
| `process_create_time` | `str?` | OS 原始标识：Windows=`GetProcessTimes` FILETIME；Linux=`/proc/<pid>/stat` 第 22 starttime(ticks)+`boot_id`。严格比对，零容差 |
| `executable_path` | `str?` | WMI ExecutablePath / `/proc/<pid>/exe` |
| `command_line_fingerprint` | `str?` | argv 规范化 SHA-256（不存整行命令行） |
| `process_group_id` | `str?` | 真实进程组标识（见 §5.2） |
| `detached` | `bool` | 快照固化，防配置漂移 |
| `last_heartbeat` | `str?` | 仅提示管理链路失联，不判死 |
| `log_file_path` | `str` | detach 日志文件落点（见 §6） |

### 3.3 spawn→持久化异常窗口：STARTING 预登记（最终修正⑤）
新增预登记状态 `STARTING`（见 §4），把"进程起来但身份没落库"的可疑窗口显式化：
1. 先生成唯一 `run_id`；
2. **先持久化 `STARTING` 记录**（此时无 pid/身份）；
3. 启动进程；建立可核验的 `run_id↔进程` 关联（记录 pid 候选）；
4. 采集身份 token，原子地更新为 `RUNNING`（一次 `save_run` 写入身份 + 状态 + pid）；
5. 启动恢复扫描时**检查残留 `STARTING`**：仍处于 STARTING 的 run，其进程可能已起但未完成登记 → 按身份探测：能匹配则补登记为 RUNNING/ORPHANED；身份不可得 → **UNKNOWN，绝不盲杀**；
6. `save_run`/进程之间任一步崩溃，先落库的 STARTING 记录在下次启动被显式识别与收束。

平台限制/降级：Windows 下 `run_id↔进程` 关联依赖"spawn 返回 pid→采集 token"时序，若在 syzygy 之间崩溃，只能靠 STARTING + 宿主线索（exe/cmdline）试探；无法确认一律 UNKNOWN 归待核查，不自动清理。

---

## 4. 统一状态机语义（最终修正①统一 + 终版一致）

| 状态 | 语义 | 归属 |
|------|------|------|
| `PENDING` | 待开始 | 运行表（排队） |
| `STARTING` | 已预登记、进程拉起/登记中（新增，窗口态） | 运行/待核查 |
| `RUNNING` | **进程存活且有效受管理** | 运行看板「正常运行」计数 |
| `ORPHANED` | **进程存活但失去有效管理**（detach/非 detach 均可） | 运行看板「待处理」区 |
| `UNKNOWN` | **进程身份或实际状态无法可靠确定**（不代表已确认存活） | 运行看板「待核查」区，**不计入实际运行进程数** |
| `INTERRUPTED` | 已确认任务异常中断/终止 | 执行历史 |
| `SUCCEEDED/FAILED/TIMEDOUT/KILLED` | 既有终态 | 执行历史 |

关键统一：
- detach 与**非**detach **只影响恢复/日志/重新接管/清理策略，不改变状态含义**。
- NON-detach 崩溃后若进程仍存活 → 仍按存活归属（RUNNING/ORPHANED/UNKNOWN），**不能因"应为非 detach"就标 INTERRUPTED**（最终修正①）。
- UNKNOWN 禁止自动猜转终态，只能靠可靠证据或管理员复核；**不加入正常运行进程数量**。

### 4.1 状态迁移
```
PENDING ─触发→ STARTING ─身份落库→ RUNNING
RUNNING ─正常→ SUCCEEDED/Failed；超时/中止→ TIMEDOUT/KILLED
RUNNING ─恢复扫描确认进程已死(身份匹配)→ INTERRUPTED(记 reason+检查时间)
RUNNING ─存活但管理失效→ ORPHANED
STARTING/RUNNING/ORPHANED ─身份/状态不可靠→ UNKNOWN（不碰不杀）
ORPHANED ─(身份+监控+日志+停止 全通过)→ RUNNING
UNKNOWN ─可靠证据匹配→ 依归属 SUCCEEDED/FAILED 或转 RUNNING；否则保持待复核
```

### 4.2 数据库/API/SSE/看板/历史归属
- `running_runs()` 返回 `STARTING+RUNNING+ORPHANED+UNKNOWN`（执行中心活性区）；整除 `INTERRUPTED/终态` 入历史。
- API：`/api/running` 分组返回 `running/orphaned/unknown/starting` 计数与列表；`/api/history` 过滤含新状态。SSE `run` 事件 status 字段照常广播。
- 前端运行看板：
  - **正常运行计数** = RUNNING 数。
  - **待处理区** = ORPHANED。
  - **待核查区** = UNKNOWN + STARTING（**不计入正常运行数量**，最终修正③）。
  - 历史 = INTERRUPTED + 既有终态。

---

## 5. 进程身份 / 进程树安全停止（最终明确，最终修正④）

**统一原则**：身份检测与安全停止共用同一套 `verify_identity(pid)`；凡无法证明归属，**一律禁止停止**（"无法证明归属就不杀进程"）。全程 OS 原始标识严格比对，零容差。

### 5.1 各平台身份采集与核验
| 能力 | Windows | Linux |
|------|---------|-------|
| 创建标识 | `GetProcessTimes` 原始 FILETIME | `/proc/<pid>/stat` 第 22 starttime + `/proc/sys/kernel/random/boot_id` |
| 可执行路径 | `Win32_Process.ExecutablePath` | `/proc/<pid>/exe` |
| 命令行指纹 | `Win32_Process.CommandLine` 规范化 SHA-256 | `/proc/<pid>/cmdline` join 规范化 SHA-256 |
| 是否存活 | `OpenProcess`+`GetExitCodeProcess` | 存在 `/proc/<pid>` 且 `state != Z` |
| kill | 见 5.2 | `os.killpg`/`os.kill` |

核验规则：
```
verify(pid, expected_token):
    t = capture_create_token(pid)          # 权限/字段不可得 → None
    if 进程不存在: DEAD
    if t is None: UNKNOWN_IMPLICIT      # 禁止停止
    if t != expected_token: REUSED       # 禁止停止（PID/PGID 复用）
    return MATCH
```

### 5.2 安全停止方案（最终明确，不再二选一）
**停止资格 = 身份核验 MATCH 且归属在已登记进程/组内；否则一律拒绝。**

**Windows —— Job Object（优先）+ 进程句柄兜底：**
- **选择标准**：默认优先 **Job Object**（把每次 spawn 拉起的进程放进独立 Job，job 内限定本次任务的可停止归属；停止= terminate 该 job 内进程，天然不波及树外进程）。仅当 Job Object 不可用/被绕过时，退级为**基于进程句柄**：持句柄 + 身份核验后 kill。
- **后端重启后重新识别原有 Job Object**：Job 由本进程 create 时不带名字，重启即丢失句柄。因此重启恢复对 detach 幸存进程：**不再尝试恢复 Job 句柄**，降级为"进程句柄 + 身份核验 + 重新登记"管理；停止使用句柄 + token 核验。Job 句柄无法重新取得 → 走句柄兜底，仍满足"身份核验"前提。
- **允许自动停止 / 必须拒绝**：
  - 允许：身份 MATCH 且在登记归属内；或 job 句柄仍有效且进程在 job 内。
  - 拒绝（安全停止 must-拒绝）：token 为 None（权限不足）、REUSED（PID 复用）、进程脱离 job 且句柄失效、无法证明归属。

**Linux —— PGID 终止 + pidfd：**
- spawn 用 `start_new_session=True` 显式建独立会话/进程组，`os.getpgid` 记录**真实 PGID**（不假设 PID=PGID）。
- **降低 PGID 复用 / 核验→终止竞态**：`threading.Lock` 不能消除操作系统级 PID/PGID 复用竞态（不声称 Lock 即解决）。因此：
  - 复验 token 尽量逼近 kill，并用 **`os.killpg(pgid, SIGKILL)` 目标限定在本会话组**；
  - 对**单进程**终止优先用 **pidfd**（`os.pidfd_open`，Linux≥5.3）：pidfd 绑定的是"进程实体"而非 PID，从 pidfd_open 到 pidfd_send_signal 期间即使 PID 被复用也打在原进程上，原生消除单进程 PID 复用竞态；
  - 进程组级终止无法用 pidfd → 采用 `os.killpg`，并在 kill 前对组内成员做 owner 身份抽验；确证不了归属即拒绝。
- **pidfd 边界**：仅适用于**单进程**终止；无法对进程组整体做 pidfd 记（组不是单一 open 描述）。资源受限/老内核无 pidfd_open → 退回 killpg + token 复验，并声明残余竞态窗更宽，属可接受降级。
- **允许/拒绝**：身份 + 归属在组内 → allow；token None/REUSED/组成员含无法确认实体 → refuse。

**跨平台共同安全原则**：任何"核验通过→kill"之间存在 OS 复用窗口；用 pidfd（单）、job 句柄（Win）、killpg 目标限定（组）**收窄**该窗口，但不做绝对消除的虚假承诺；凡不能证明归属一律拒绝停止。

---

## 6. detach / 非 detach 差异恢复

### 6.1 detach：日志文件化 + 四问接管（最终修正②）
- 启动时 stdout/stderr **直接写调度中心管理的 `<run_id>.log` 文件**；运行时调度中心按文件尾增量推 SSE；崩溃后按文件尾续读恢复日志采集（不再依赖不可恢复的 `stdout=PIPE`）。
- **接管判定降低为 4 问**（最终修正②：退出码不能在进程还跑时预取，不算接管前提）：
  1. 进程身份 MATCH 且仍存活；
  2. 进程监控可重建（运行表登记 + 日志文件锚点）；
  3. 日志采集可恢复（文件存在可读 + 可靠文件尾续读）；
  4. 停止能力经安全验证（§5.2 任一途径可安全停止）。
- 4 问全过 → `ORPHANED → RUNNING` 重建管理。
- 任一不过 → 保持 ORPHANED，允许管理员查看与安全处理。
- 进程结束时：取退出码，取不到则**如实保存 `exit_code=null` + `exit_code_unknown_reason`**，不伪造成功/失败；状态置 INTERRUPTED（若异常）或 SUCCEEDED（确属正常结束且身份匹配）。

### 6.2 非 detach
- 平常随 worker 结束 → SUCCEEDED/FAILED。
- 调度中心崩溃期间进程异常存活：按存活归属（RUNNING/ORPHANED/UNKNOWN），**不可因"应是非 detach"直接标 INTERRUPTED**（最终修正①）；INTERRUPTED 仅在确认异常退出后使用。
- 差异仅体现于恢复/接管/清理策略，不改变状态含义。

---

## 7. 心跳设计

三源分离，判死只走 OS 探测：
| 源 | 含义 | 用于 |
|----|------|------|
| 运行表内存 | 调度中心是否已知该 run | 管理关系判定 |
| executor 心跳 `last_heartbeat` | 管理链路“活性” | 仅链路失联提示，**绝不单独判 OS 死亡** |
| OS 探测 (pid+token) | 真实存活 | 唯一判死依据 |

- 默认 10s 批量写入（`save_run(heartbeat_only=True)` 只更新心跳 JSON 列，避免全表重写）。
- 间隔/超时可配：`SC_HEARTBEAT_INTERVAL_SEC`（默认 10）、`SC_HEARTBEAT_TIMEOUT_TIMES`（默认 3）。
- 超时阈值仅解释为"管理链路失联"信号，判死必须 OS 探测。

---

## 8. 启动顺序与 SAFE-FIRST（最终不变）

### 8.1 启动顺序
`main.py` startup 强制：建 Scheduler → **先启动恢复扫描**（收敛终态/孤儿/残留 STARTING）→ **全部恢复完成后再 `aps.start()`**，避免与正常任务调度竞争。

### 8.2 重试：仅 SAFE-FIRST
- 默认只修正状态，不自动重启任何异常任务；管理员确认后经现有 `trigger` 手动重触发。
- 暂不实现 `idempotent-auto-retry`；`retryable=true` 本轮仅预留、不生效。

---

## 9. 测试与验收

新增 `server/test_phase2_recovery.py`，覆盖 10 项验收 + Windows/Linux 恢复 + STARTING 窗口 + Running/History 归属断言。

### 9.1 10 项验收映射
| # | 验收 | 测试 |
|---|------|------|
| 1 | 正常执行完整结束 | 非 detach 正常 → SUCCEEDED，身份/心跳/状态齐全 |
| 2 | 崩溃→重启收敛死进程 | "进程已死但库 RUNNING"→ INTERRUPTED（中断原因/检查时间） |
| 3 | 崩溃→detach 存活被识别 | 存活 detach → 四问判别 ORPHANED 或重建 |
| 4 | PID 复用不误判/误杀 | token 不匹配 → UNKNOWN/identity_mismatch；stop 拒绝 |
| 5 | 孤儿被识别并安全处理 | 存活失联 → ORPHANED；reclaim 显式接管 |
| 6 | 非 detach 异常中断按设计 | 父退子存 → 按存活归属（不直接 INTERRUPTED），默认不自动杀 |
| 7 | 心跳超时被识别但单超时不死判 | 心跳过期但 OS 存活 → 仍 RUNNING；OS 死才判死 |
| 8 | abort/stop 不误杀无关进程 | 身份核验失败 stop 返回 identity_mismatch 且不 kill |
| 9 | 中途写库异常无永久不一致 | save_run 抛错 → 内存/落库最终一致；STARTING 残留可收束 |
| 10 | Phase1 测试 + 前端构建通过 | `test_security.py` 12/12 + `selfcheck.py` + `npm run build` |

### 9.2 Windows / Linux 恢复测试（保留分平台）
**Windows**：spawn 取 FILETIME token；复用 PID → REUSED 拒绝；detach 文件尾续读不谎报实时；Job Object / 句柄停止只停本任务归属、树外不受影响；**重启后 Job 句柄丢失 → 句柄兜底重建 + 身份核验**。
**Linux**：`start_new_session=True` + 真实 PGID（!==PID 场景）；`/proc/<pid>/stat`+boot_id 作 token；复用场景 REUSED/UNKNOWN 拒杀；**pidfd 单进程终止消除 PID 复用**（verify 有 pidfd 段）；killpg 组内 owner 抽验；进程已退但退出码不可得 → INTERRUPTED+null。
**归属断言**：UNKNOWN 不计入"正常运行进程数"；INTERRUPTED 入历史；`/api/running` 分组 running/orphaned/unknown/starting 正确。

### 9.3 通过标准
`test_phase2_recovery.py` 全绿 + `test_security.py` 全绿 + `selfcheck.py` SUCCESS + 前端 `npm run build` 通过。

---

## 10. 如何不破坏 Phase 1 安全验证

- 新增 API 挂 `auth.auth_required`/危险端点令牌校险；不改 auth/audit/SSE Bearer。
- `check_safe_path`/`check_exec_path` 不动。
- 状态枚举只追加不删除；历史响应结构兼容。
- 结束跑 `test_security.py` 12/12。
- 独立 git commit，保留 Phase 1 历史（6d348ac 之后追加）。

---

## 11. 修订版 3 对 5 项最终修正的逐项解决 + 仍存限制

| 修正 | 解决 |
|------|------|
| ① 统一非 detach 状态语义 | 存活态一律按存活归属（RUNNING/ORPHANED/UNKNOWN），INTERRUPTED 仅确认异常退出后用；detach 只影响策略 |
| ② detach 五问→四问 | 退出码从接管前提移除，改为进程结束时处理，取不到即 `exit_code=null`+未知原因 |
| ③ UNKNOWN 归属 | 归「待核查」，不计入正常运行进程数；running_runs 分组 running/orphaned/unknown/starting；INTERRUPTED 入历史 |
| ④ 安全停止最终明确 | Windows=Job Object 优先后句柄兜底，重启无法重取 Job → 句柄+身份重建；Linux=killpg 限定组 + 单进程 pidfd 消 PID 复用；Lock 不消除 OS 竞态（明示）；不能证归属即拒杀 |
| ⑤ STARTING 预登记 | 先落 STARTING→spawn→采集身份→RUNNING；恢复扫描收束残留 STARTING；不可得一律 UNKNOWN 不盲杀 |

**仍存在的技术限制（如实声明）**：
- OS 层 PID/PGID 复用竞态无法用 Python 锁彻底消除；pidfd（Linux 单进程）/Job 句柄（Win）/killpg 限定组（Linux 组）只能大幅收窄窗口，重启后 Job 句柄不可重取、已脱离 job/会话的进程属能力边界。
- 非 detach 崩溃时进程若先退，无 OS 身份锚点则只能靠残留记录归 INTERRUPTED（记录 reason），存在极小的"异常但被归为自然结束"歧义风险（靠 APPEND-ONLY 审计缓解）。
- detach 文件尾续读仅覆盖写文件后的日志；进程在恢复前写入的最后缓冲若未 flush 到文件则不可见。
- STARTING 预登记的 `run_id↔进程` 关联在 Windows 崩溃窗口内可能断链，只能归 UNKNOWN 待核查。

---

完成后提交修订版 3，等待最终批准后再正式实施 Phase 2。