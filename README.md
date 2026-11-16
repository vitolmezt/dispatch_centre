# 调度中心 Scheduler Center

一个「菜单 + 状态看板」式的自动化调度中心。它负责**有哪些任务可调度、哪些在执行、哪些完成、哪些失败**；至于每条任务（命令 / 脚本 / 程序 / 文件操作）内部怎么执行，调度中心一概黑盒处理。

- 后端：Python + FastAPI + APScheduler + SQLite
- 前端：Vue 3 + Vite + Vue Router + Axios + Element Plus
- 部署形态：单机单实例，前后端分别启动，前端通过 `/api` 对接后端

---

## 一、架构与进程模型

### 1.1 分层

```
Vue 前端 ──HTTP/SSE──> FastAPI (main.py) ──> Scheduler ──Popen──> 任务进程组
                                                  │                    │
                                                  │                    └─ 每实例命名 Job
                                                  │                       (不设 KILL_ON_JOB_CLOSE)
                                                  └─ SQLite + 日志文件
```

**只有两层进程**：调度中心（FastAPI + Scheduler）与真实任务进程。
没有额外的常驻管理层——监控、日志、停止、恢复全部由 Scheduler 自己做。

### 1.2 三级生命周期（互不依赖）

| 事件 | 调度中心 | 任务进程 |
|---|---|---|
| 调度中心崩溃 / 被强杀 | 退出 | **继续运行** |
| 调度中心重启 | 恢复 | **原进程继续，自动接管** |
| 用户主动停止 | 存活 | 按要求终止 |

任务进程的存活**不依赖调度中心**：调度中心退出后任务照常执行、照常写日志。

### 1.3 为什么需要 `run_headless.py`

Windows 上，从终端 / VS Code / IDE 启动的进程会处于宿主的 **Job Object** 中；若该 Job 带有 `KILL_ON_JOB_CLOSE`，宿主退出（关终端、关 IDE、取消后台任务）会连带杀死其派生进程——也就是你的任务。

实测三种启动方式的 Job 归属：

| 启动方式 | 父进程 | 所属 Job 的 `LimitFlags` | 任务能否独立 |
|---|---|---|---|
| 终端里 `python run.py` | powershell / cmd | `0x00002000`（含 KILL_ON_JOB_CLOSE） | 否 |
| `subprocess` + `DETACHED_PROCESS` | 仍是终端 | `0x00002000` | 否（实测无效） |
| **`python run_headless.py`** | **svchost.exe** | **`0x00000000`** | **是** |

`run_headless.py` 通过 Windows 计划任务让 **Task Scheduler 服务**创建调度中心进程，从而脱离终端/IDE 的受限 Job。这是让任务真正独立的前提。

> 若用 `python run.py` 在终端里跑，关闭该终端仍会连带杀死任务。此时运行记录会写入
> `isolation_warning` 字段，提示改用 `run_headless.py`。

---

## 二、核心能力

### 2.1 任务管理（即用入库，免改配置、免重启）

任务存储在 SQLite `jobs` 表，界面内直接**新增 / 编辑 / 改名 / 启停 / 删除**，实时生效；定时任务自动注册 / 注销。参数表单由后端按类型动态下发，前端自动渲染，**无硬编码**。

- **改名**：`PUT /api/jobs/{name}` 的 body 里若 `name` 与路径不同即为改名；新名已被占用返回 `409`。改名会同步更新服务组成员引用与该任务的历史执行记录。

### 2.2 任务类型（type 驱动）

| type | 用途 | 主要参数 |
|---|---|---|
| `COMMAND` | 一条命令 | `command`、`workdir`、`detach`、`env` |
| `SCRIPT` | 脚本 | `path`、`args`、`workdir`、`detach`、`env` |
| `PROGRAM` | 直接执行程序 | `path`、`args`、`workdir`、`detach`、`env` |
| `FILEOP` | 文件/目录操作 | `action`、`source`、`target`、`recursive` |

- `SCRIPT` 按扩展名选解释器：`.ps1`→PowerShell、`.bat/.cmd`→cmd、`.py`→Python、`.sh`→bash。
- `FILEOP` 内建动作：`DELETE_FILE` / `DELETE_DIR` / `COPY` / `MOVE`。
- `detach`（进程独立）：目标进程作为独立后台进程常驻，调度中心不设超时、随服务一直跑；状态跟随进程存亡。
- `env`：传给子进程的环境变量（仅进程类任务）。

**关于 shell 包装**：`COMMAND` 类型不再无条件套 `cmd /c`。只有当命令**在引号之外**含 shell 语法（管道 `|`、重定向 `>`、`&&` 等）或首个词是 shell 内建命令（`echo`、`dir`、`set` 等）时才经过 shell；否则直接以真实可执行文件为被管理进程。这样记录的 PID 就是真正干活的进程，而不是中间的 `cmd`。经过 shell 包装的运行实例会标记 `via_shell=true`。

### 2.3 执行权限模型

调度中心是**通用黑盒任务调度与执行管理平台**，不审查任务内部业务行为。经管理员认证后即可执行任意符合操作系统条件的命令 / 脚本 / 程序 / 文件操作：

- **不设路径白名单 / 安全根目录**：`SCRIPT` / `PROGRAM` 的 `path` 可为绝对或相对路径、可跨盘；`FILEOP` 可作用于运行账户能访问的任何路径。
- 实际操作权限以**调度中心运行账户**在操作系统上拥有的权限为准。
- 管理面安全（令牌认证、Bearer 鉴权、跨域白名单、登录限流、审计）与**进程身份校验**（PID + 进程创建时刻）保持不变。

### 2.4 全局并发控制

同一任务同一时刻至多一个实例在运行；重复触发会被拒绝（`409`）并在前端提示。

### 2.5 服务组（把后台任务编组成「一台服务」）

把若干 `detach` 任务按顺序编排为**服务组**，视为整体启停：

- 新建 / 编辑：命名、描述、从后台任务中勾选成员并排序。
- **一键启动 / 停止整组**：启动按成员顺序逐个触发；停止按持久化 PID 定向终止整组进程树。
- **整组状态**：任一成员在运行即为「运行中」，否则「已停止」。
- **统一日志视图**：抽屉内并排展示各成员最近一次执行的实时输出。

### 2.6 实时状态

页面通过 SSE（`/api/events`）订阅任务的**开始 / 结束 / 中止 / 状态收敛**事件，各看板自动刷新，无需轮询。

---

## 三、进程管理细节

### 3.1 进程身份凭据

每次拉起任务都会采集：`PID` + `process_create_time`（进程创建时刻）+ 可执行文件路径 + 命令行指纹 + 进程树根，写入运行记录。

**身份核验必须同时满足 PID 与创建时刻一致**，避免 PID 被系统复用后误认、误杀。停止前一律核验，核验失败拒绝操作，绝不盲杀。

### 3.2 每实例命名 Job

每个运行实例建立一个命名 Job（`Local\SCRun_<run_id>`），显式设置 `LimitFlags = 0`——**不启用 `KILL_ON_JOB_CLOSE`**。

- **用途**：调度中心存活期间，可用 Job 句柄精确终止整个进程组（含孙进程）。
- **不依赖它存活**：调度中心退出时 Job 句柄关闭、Job 随内核对象销毁，但任务进程不受影响。
- **无法跨进程重开**：Job 是内核对象，最后一个句柄关闭即销毁、名字消失（实测 `OpenJobObject` 返回 `ERROR_FILE_NOT_FOUND`）。因此调度中心重启后的接管**不依赖 Job**，改用「持久化记录 + 身份核验 + `taskkill /T` 进程树停止」。

### 3.3 日志

- 任务 `stdout` / `stderr` **直写** `data/logs/run_<id>.log`，不经管道、不依赖调度中心持续读取。调度中心退出期间任务仍能继续写日志。
- `log_offset` 持久化读取位置：重启后从上次位置续读，**不重复推送历史日志、不丢失离线期间日志**。
- 子进程输出按原始字节读取并自动识别编码：优先 UTF-8 严格解码，失败回退系统编码（Windows 中文系统为 GBK/cp936）。

### 3.4 启动恢复与状态收敛

调度中心启动时：

1. 扫描非终态记录，逐条用 OS 探针 + 身份核验判断真实状态。
2. 确认存活的 → 重建为 `RUNNING`，恢复日志跟随与状态监控（**不重新启动任务**）。
3. 确认死亡的 → 收敛为 `INTERRUPTED`，写入结束时间。
4. **终态记录一致性核对**：对最近 200 条记录（含终态）再核一次——若数据库记录已是终态但进程其实仍在运行，修正为 `RUNNING` 并在 `interrupted_reason` 写明原因。这修补的是「界面显示已结束、进程其实还在跑」的不一致。

心跳超时**仅作活性参考**，绝不单独作为「进程已死」的判定依据；是否死亡一律以 OS 探针实测为准。

### 3.5 停止规则

| 操作 | 行为 |
|---|---|
| 中止（`abort`） | 仅对持有 Popen 句柄的运行实例有效；进程树终止 |
| 停止（`stop`） | 先做身份核验；有 Job 句柄则用它终止整组，否则按进程树 `taskkill /T`；返回 `stop_via` 标明途径 |
| `via_shell` 任务 | 额外执行 `_sweep_shell_orphans`：按父 PID 精确匹配清理 shell 残留子进程 |
| 身份核验失败 | 返回 `409` 并携带真实原因，**不谎报已停止** |

---

## 四、执行状态机

**进程类任务**（COMMAND / SCRIPT / PROGRAM）的状态演进：

```
trigger ──► STARTING ──► RUNNING ──► SUCCEEDED
                  │            ├──► INTERRUPTED
                  │            ├──► TIMEDOUT
                  │            └──► KILLED
                  │
                  ├──► FAILED       # 引擎自身异常（找不到可执行目标 / 参数错误）
                  ├──► UNKNOWN      # 无身份凭据，无法安全核验
                  └──► INTERRUPTED  # 确认进程已死，收敛为异常终态

RUNNING ──(重启且存活)──► ORPHANED ──(四问接管通过)──► RUNNING(adopted)
                                   └─(接管未通过)──► 保持 ORPHANED 待人工处理
```

| 状态 | 中文 | 语义 |
|---|---|---|
| `STARTING` | 预登记中 | spawn 前的中间态，防崩溃丢失启动意图 |
| `RUNNING` | 进行中 | 进程存活且处于管理关系内 |
| `ORPHANED` | 孤儿 | 存活但管理关系丢失，待四问接管 |
| `UNKNOWN` | 待核查 | 无身份凭据，无法安全判断 |
| `INTERRUPTED` | 进程中断 | 确认进程已死（含非 0 退出、离线期间自然结束）；**非用户发起** |
| `SUCCEEDED` | 已完成 | 退出码 `0` |
| `FAILED` | 失败 | 引擎自身异常，**进程未真正启动**（无退出码） |
| `TIMEDOUT` | 已超时 | 超过 `timeout_s` 被调度中心中止 |
| `KILLED` | 用户中止 | 用户主动停止 |

**关键区分**：

- `SUCCEEDED` vs `FAILED`：前者「跑完了且成功」，后者「压根没跑起来」。
- `FAILED` vs `INTERRUPTED`：前者无退出码（未启动），后者有非 0 退出码（跑过但异常结束）。
- **无可靠退出码时不伪造**：标 `INTERRUPTED` 且 `exit_code` 保持 `null`，并在 `interrupted_reason` 写明「无可靠凭证」。

> `FILEOP` 为同步文件操作，无进程态，直接落 `SUCCEEDED` / `FAILED`。
> 异常任务一律保持终态、**不自愈、不自动重试**，由管理员显式手动重新触发。

---

## 五、启动与运行

### 5.1 推荐：无界面启动（任务可独立存活）

```powershell
cd server

python run_headless.py            # 启动（计划任务方式，脱离终端 Job）
python run_headless.py --status   # 查看状态
python run_headless.py --logs     # 查看运行日志（最近 100 行）
python run_headless.py --logs -f  # 实时跟踪日志
python run_headless.py --stop     # 停止
```

特点：

- 后台静默运行，**关闭终端 / VS Code 都不影响任务**
- 无控制台窗口（用 `pythonw.exe` + 计划任务）
- 运行日志写入 `data/run/server_stdout.log`，按 10MB 轮转、保留 3 份（总量约 40MB）
- 状态文件：`data/run/server_status.json`

### 5.2 开发：前台启动

```bash
cd server
pip install -r requirements.txt
python run.py          # 启动在 http://0.0.0.0:8000
```

可以看到实时访问日志，但**关闭终端会连带杀死任务**（受终端 Job 约束）。仅建议开发调试使用。

### 5.3 前端

```bash
cd web
npm install
npm run dev        # http://127.0.0.1:5173
```

开发时由 vite 把 `/api` 代理到后端 `127.0.0.1:8000`。

### 5.4 环境变量

| 变量 | 含义 | 默认 |
|---|---|---|
| `SC_PORT` | 监听端口 | `8000` |
| `SC_HOST` | 监听地址 | `0.0.0.0` |
| `SC_CONFIG` | 任务配置 json（首次迁移种子） | `server/config/jobs.json` |
| `SC_DB` | SQLite 数据库路径 | `server/data/scheduler.db` |
| `SC_ALLOW_ORIGINS` | 允许的前端跨域来源（逗号分隔） | `http://localhost:5173` |
| `SC_SESSION_TTL` | 会话 token 有效期（秒） | `43200`（12 小时） |
| `SC_TOKEN` | 长期管理凭证（环境变量直给） | 无 |
| `SC_TOKEN_FILE` | 长期管理凭证文件路径 | `server/data/sc_token` |
| `SC_AUTO_GEN_TOKEN` | 为 `1` 且未配置凭证时自动生成 | 无 |
| `SC_LOGIN_WINDOW` | 登录限流滑动窗口（秒） | `900`（15 分钟） |
| `SC_LOGIN_MAX_FAIL` | 窗口内最大失败次数 | `5` |
| `SC_LOGIN_COOLDOWN` | 超限冷却时长（秒） | `300`（5 分钟） |
| `SC_NODE_ID` | 节点标识（写入运行记录） | 主机名 |
| `SC_HEARTBEAT_INTERVAL_SEC` | 心跳间隔（秒） | `10` |
| `SC_HEARTBEAT_TIMEOUT_TIMES` | 心跳超时倍数 | `3` |
| `SC_HEARTBEAT_BATCH_SEC` | 心跳落库节流（秒） | `5` |
| `SC_LOG_MAX_BYTES` | 运行日志单文件上限 | `10485760`（10MB） |
| `SC_LOG_BACKUPS` | 运行日志保留份数 | `3` |
| `SC_AUDIT_MAX_RECORDS` | 审计记录上限 | `2000` |
| `SC_AUDIT_KEEP_ARCHIVES` | 审计归档保留数 | `5` |

### 5.5 认证与凭证

所有管理接口（除 `login` / `health` / `constants` 等少数）均需会话认证。两层凭证：

1. **长期管理凭证（登录密码）**：默认在 `server/data/sc_token`。优先级：`SC_TOKEN` 环境变量 → `SC_TOKEN_FILE` → 默认 `data/sc_token` → `SC_AUTO_GEN_TOKEN=1` 时自动生成。**无过期机制**。
2. **短期会话 token**：登录成功后签发，存活 `SC_SESSION_TTL`；服务重启后内存会话清空、需重新登录。

> ⚠️ **单机单实例设计**，不共享会话。多机部署时每台需单独配置凭证；`data/sc_token` 已被 `.gitignore` 排除，不要把管理口令带进代码库。

---

## 六、REST API

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/jobs?type=&enabled=&q=&page=&page_size=&all=&sort=&order=` | 任务列表：过滤 + 搜索 + 分页 + 排序 |
| `POST` | `/api/jobs` | 新增任务（实时入库并注册定时） |
| `PUT` | `/api/jobs/{name}` | 更新任务；body 中 `name` 与路径不同即**改名**；新名冲突返回 `409` |
| `DELETE` | `/api/jobs/{name}` | 删除任务（同时注销定时任务） |
| `POST` | `/api/jobs/{name}/enable` | 启用/停用；**409** = 正在执行不可停用 |
| `POST` | `/api/jobs/{name}/trigger` | 手动触发；**409** = 正在运行；**403** = 已停用 |
| `GET` | `/api/job-schema` | 各任务类型的参数表单结构 |
| `GET` | `/api/groups` | 任务分组元信息 |
| `GET` | `/api/service-groups` | 服务组列表 |
| `POST` | `/api/service-groups` | 新建服务组 |
| `PUT` | `/api/service-groups/{name}` | 更新服务组 |
| `DELETE` | `/api/service-groups/{name}` | 删除服务组（不影响成员任务） |
| `GET` | `/api/service-groups/{name}` | 服务组详情（含成员实时/最近状态） |
| `POST` | `/api/service-groups/{name}/start` | 启动整组；**409** = 有成员已在运行 |
| `POST` | `/api/service-groups/{name}/stop` | 停止整组 |
| `GET` | `/api/running?type=&q=&status=&page=&page_size=` | 运行中实例（含 `alive_total` 分桶计数） |
| `GET` | `/api/history?status=&job=&type=&q=&page=&page_size=&all=` | 执行历史 |
| `DELETE` | `/api/history` | 批量删除历史记录 |
| `GET` | `/api/runs/{id}` | 单个运行实例详情（含日志与身份/心跳元信息） |
| `GET` | `/api/runs/{id}/stream` | 该次执行的日志 SSE 流 |
| `POST` | `/api/runs/{id}/abort` | 中止运行实例 |
| `POST` | `/api/runs/{id}/stop` | 停止后台进程；身份核验失败返回 **409** 并携带原因 |
| `GET` | `/api/constants` | 全局状态 / 类型 / 分组常量字典 |
| `POST` | `/api/login` | 用长期凭证换取会话 token |
| `POST` | `/api/logout` | 登出，作废当前会话 |
| `GET` | `/api/audit` | 审计日志 |
| `GET` | `/api/events` | SSE 事件流（需会话认证） |
| `GET` | `/api/health` | 健康检查（无需认证） |
| `GET` | `/api/config` | 服务配置信息 |
| `POST` | `/api/fs/pick` | 图形化选择文件/路径（需认证；无图形环境时返回明确错误） |

---

## 七、前端看板

Element Plus 暗色主题，五个页面：

| 页面 | 场景 |
|---|---|
| 数据看板 | 全局概览：任务/执行数量与状态的图表汇总 |
| 任务列表 | 任务台账：新增 / 编辑 / 改名 / 启停 / 删除，按类型 / 启用状态 / 名称搜索排序 |
| 执行中心 | 正在运行的实例：实时列表、过滤、排序；查看详情或中止 |
| 执行历史 | 已完成实例历史：按状态 / 类型 / 名称过滤排序；点击查看详情与日志 |
| 服务组 | 把多个 `detach` 任务编组成「一台服务」，一键启停、统一查看日志 |

- 任务详情、执行详情统一以右侧抽屉展示。
- 任务列表 / 服务组的操作列统一为「互斥主按钮 + ⋯ 收纳次要操作」。
- 删除服务组（及停用中任务）有二次确认。

相关文档：`docs/layout-guide.md`（前端统一布局规范）。

---

## 八、目录结构

```
scheduler-center/
  server/
    app/
      models.py      # Job / Run / ServiceGroup 数据模型
      executor.py    # 进程执行器（每实例命名 Job、shell 判定、日志跟随）+ FILEOP
      scheduler.py   # 调度核心：APScheduler 定时 + 恢复接管 + 心跳 + 停止
      store.py       # SQLite 持久化
      identity.py    # 进程身份采集与安全停止（PID + 创建时刻 + 指纹）
      auth.py        # 认证与会话管理（长期凭证 / 会话 token / 登录限流）
      main.py        # FastAPI 路由
      constants.py   # 全局枚举常量字典
      audit.py       # 审计日志
    config/jobs.json # 老式任务配置（数据库为空时的首次迁移种子）
    scripts/         # 示例脚本
    data/            # 运行时数据（已 gitignore）
      scheduler.db   #   SQLite 数据库
      logs/          #   每次运行的日志文件
      run/           #   run_headless 的状态与运行日志
    requirements.txt
    run.py           # 前台启动入口（终端内运行，会受终端 Job 约束）
    run_headless.py  # 无界面启动器（计划任务方式，任务可独立存活）
    test_phase2_recovery.py   # 恢复/身份/心跳测试（26 项）
    test_process_decouple.py  # 进程解耦与 Job 语义测试（23 项）
    test_security.py          # 安全与回归测试（12 项）
  web/               # Vue 3 + Element Plus 前端
    src/
      views/         # DashboardBoard / MenuBoard / RunningBoard / HistoryBoard / ServiceGroupBoard
      components/    # JobDrawer / JobForm / JobEditModal / RunDetailDrawer / RunLogPanel / PagerBar
      composables/   # useTablePanel / useTheme / useChartTheme
      store/         # drawer / refresh / toast / realtime
      api.js router.js constants.js App.vue
  docs/              # layout-guide / phase2-plan / phase2-acceptance-report
  README.md
```

---

## 九、测试

```powershell
cd server
python test_process_decouple.py   # 23 项
python test_phase2_recovery.py    # 26 项
python test_security.py           # 12 项
```

前端构建回归：

```powershell
cd web
npm run build
```

---

## 十、已知限制

1. **任务独立性依赖启动方式**。必须用 `run_headless.py` 启动；用 `python run.py` 在终端里跑，关闭终端会连带杀死任务（受终端 Job 约束），此时运行记录会写入 `isolation_warning`。
2. **调度中心停机期间任务自然结束时，退出码不可恢复**。此时没有进程句柄，`exit_code` 保持 `null`，状态标 `INTERRUPTED` 并写明「无可靠凭证」，**不伪造**。
3. **重启接管后无法精确终止进程组**。命名 Job 已随调度中心退出而销毁，只能用 `taskkill /T` 按进程树终止；若任务存在脱离父子链的 daemon 进程可能残留。
4. **`via_shell=true` 的任务仍是孙进程管理**。含 shell 语法的命令无法避免中间 shell，停止依赖进程树；shell 先死时靠 `_sweep_shell_orphans` 按父 PID 精确清理。
5. **依赖 Windows 计划任务可用** `run_headless.py`。受限环境（无计划任务权限、组策略限制）只能回退到 `run.py`，此时失去 Job 隔离。
6. **Linux 未验证**。Job Object 相关逻辑仅 Windows 生效，Linux 下退化为纯进程树管理。
7. **终态记录一致性核对只覆盖最近 200 条**，避免每次启动扫全表。
