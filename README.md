# 调度中心 Scheduler Center

一个「菜单 + 状态看板」式的自动化调度中心。它负责**有哪些任务可调度、哪些在执行、哪些完成、哪些失败**；至于每条任务（命令 / 脚本 / 程序 / 文件操作）内部怎么执行，调度中心一概黑盒处理。

- **后端**：Python + FastAPI + APScheduler + SQLite
- **前端**：Vue 3 + Vite + Element Plus + ECharts
- **部署形态**：单机单实例，前后端分别启动，前端通过 `/api` 对接后端

---

## 快速开始

```powershell
# 后端（推荐：任务可独立存活）
cd server
pip install -r requirements.txt
python run_headless.py

# 前端
cd web
npm install
npm run dev
```

访问 `http://127.0.0.1:5173`，用管理凭证登录（默认存于 `server/data/sc_token`）。

> **为什么推荐 `run_headless.py`**：它通过计划任务让调度中心脱离终端/IDE 的受限 Job Object，关闭终端不会连带杀死任务。`python run.py` 仅适合开发调试。

---

## 功能概览

### 任务管理

任务存于 SQLite，界面内直接新增 / 编辑 / 改名 / 启停 / 删除，实时生效。四种类型：

| 类型 | 用途 |
|---|---|
| `COMMAND` | 执行一条命令 |
| `SCRIPT` | 执行脚本（按扩展名自动选解释器） |
| `PROGRAM` | 直接执行程序 |
| `FILEOP` | 文件/目录操作（删除、复制、移动） |

参数表单由后端按类型动态下发，前端自动渲染，无硬编码。

### 执行控制

- **同步执行**：占用调度中心超时管理，超时自动中止
- **进程独立（`detach`）**：作为后台常驻进程运行，状态跟随进程存亡
- **全局并发控制**：同一任务同时至多一个实例
- **身份核验停止**：终止前核验 PID + 进程创建时刻，防止 PID 复用误杀

### 服务组

把若干 `detach` 任务编组为「一台服务」，一键启动 / 停止整组，统一查看各成员实时日志。

### 实时状态

通过 SSE 推送任务开始 / 结束 / 中止事件，看板自动刷新，无需轮询。

### 前端看板

| 页面 | 场景 |
|---|---|
| 数据看板 | 全局概览：执行趋势、状态分布、任务排行与健康度 |
| 任务列表 | 任务记录：增删改查、启停、执行 |
| 执行中心 | 正在运行的实例：实时列表、详情、中止 |
| 执行历史 | 已完成实例：过滤排序、查看详情与日志 |
| 服务组 | 编组管理、整组启停、统一日志 |

---

## 核心特性：任务生命周期独立

**任务的存活不依赖调度中心。**

| 事件 | 调度中心 | 任务进程 |
|---|---|---|
| 调度中心崩溃 / 被强杀 | 退出 | **继续运行** |
| 调度中心重启 | 恢复 | **原进程继续，自动接管** |
| 用户主动停止 | 存活 | 按要求终止 |

实现要点：

- 每实例命名 Job 显式设 `LimitFlags = 0`，不启用 `KILL_ON_JOB_CLOSE`
- 日志直写文件，不经管道，不依赖调度中心持续读取
- 重启后通过「持久化身份凭据 + OS 探针核验」接管，而非依赖进程句柄

---

## 执行状态

`COMMAND` / `SCRIPT` / `PROGRAM` 的状态演进：

```
trigger ──► STARTING ──► RUNNING ──► SUCCEEDED / INTERRUPTED / TIMEDOUT / KILLED
                  └──► FAILED       # 引擎异常，进程未真正启动
```

| 状态 | 含义 |
|---|---|
| `STARTING` | 预登记中，防崩溃丢失启动意图 |
| `RUNNING` | 进行中 |
| `ORPHANED` | 存活但管理关系丢失，待接管 |
| `UNKNOWN` | 无身份凭据，无法安全判断 |
| `SUCCEEDED` | 退出码 0 |
| `FAILED` | 引擎自身异常（找不到目标、参数错误），**进程未启动** |
| `INTERRUPTED` | 跑过但异常结束（非 0 退出、外部终止、重启收敛），**非用户发起** |
| `TIMEDOUT` | 超过 `timeout_s` |
| `KILLED` | 用户主动中止 |

> `FILEOP` 为同步操作，直接落 `SUCCEEDED` / `FAILED`。
> 异常任务保持终态，**不自愈、不自动重试**。

---

## 项目结构

```
scheduler-center/
├── server/                     后端
│   ├── app/
│   │   ├── main.py             FastAPI 入口（app 实例、CORS、生命周期、路由聚合）
│   │   ├── api/                HTTP 接口层（30 端点，按资源分包）
│   │   ├── services/           业务服务层（任务 / 服务组的写操作编排）
│   │   ├── scheduler/          调度核心（生命周期、恢复、心跳、停止、视图）
│   │   ├── executor/           任务执行（命令解析、进程管理、Job、日志跟随）
│   │   ├── persistence/        持久化（连接、Job/Run/Group 三个 Repository）
│   │   ├── auth/               认证（凭证、会话、登录限流）
│   │   ├── models.py           Job / Run / ServiceGroup 数据模型
│   │   ├── identity.py         进程身份核验与安全停止
│   │   ├── store.py            持久化兼容层
│   │   ├── constants.py        全局枚举常量
│   │   └── audit.py            审计日志
│   ├── tests/                  pytest 测试套件（96 项）
│   ├── config/jobs.json        首次迁移种子
│   ├── data/                   运行时数据（数据库、日志、状态文件，已 gitignore）
│   ├── run.py                  前台启动（开发用）
│   ├── run_headless.py         无界面启动（推荐）
│   └── selfcheck.py            自检脚本
│
├── web/                        前端
│   └── src/
│       ├── views/              五个页面
│       ├── components/         抽屉、表单、日志面板等组件
│       ├── composables/        表格、主题、图表主题
│       ├── store/              抽屉、刷新、提示、实时事件
│       └── styles/             全局主题样式
│
└── docs/                       设计文档（布局规范、阶段方案与验收报告）
```

### 后端分层调用关系

```
HTTP 请求
   ↓
api/            接收请求、解析参数、调用 Service 或 Scheduler、映射 HTTP 异常
   ↓
services/       校验、冲突检查、审计、错误语义归一（仅含编排逻辑的接口）
   ↓
scheduler/      调度生命周期、运行状态持有、任务注册、恢复接管、心跳
   ↓
executor/       真正启动进程、超时、日志跟随
persistence/    数据读写
```

**运行状态（任务、运行实例、服务组、调度器）由 `Scheduler` 门面唯一持有**，各子模块只持门面引用，不复制状态。

---

## REST API

共 **30 个端点**，其中 **26 个需认证**、**4 个公开**（`login` / `health` / `constants` / `config`）。

| 分组 | 端点数 | 说明 |
|---|---|---|
| 任务管理 | 8 | 增删改查、启停、触发、表单结构、分组 |
| 执行记录 | 6 | 运行中、历史、详情、中止、停止 |
| 服务组 | 7 | 增删改查、详情、整组启停 |
| 认证 | 2 | 登录、登出 |
| 系统 | 4 | 常量、健康、配置、审计 |
| 事件流 | 3 | 全局 SSE、单次日志 SSE、文件选择器 |

完整接口定义见运行时 `/docs`（Swagger UI）。

---

## 认证

两层凭证：

1. **长期管理凭证**（登录密码）：存于 `server/data/sc_token`，无过期机制
2. **短期会话 token**：登录后签发，默认 12 小时有效；服务重启后需重新登录

所有管理接口需 Bearer token。登录失败有限流保护（默认 15 分钟窗口 / 5 次失败 / 冷却 5 分钟）。

> `data/sc_token` 已被 `.gitignore` 排除。多机部署时每台需单独配置，不要把口令带进代码库。

---

## 常用环境变量

| 变量 | 含义 | 默认 |
|---|---|---|
| `SC_PORT` / `SC_HOST` | 监听端口 / 地址 | `8000` / `0.0.0.0` |
| `SC_DB` | SQLite 路径 | `server/data/scheduler.db` |
| `SC_TOKEN` | 长期管理凭证（优先于文件） | 无 |
| `SC_AUTO_GEN_TOKEN` | 为 `1` 时首次启动自动生成凭证 | 无 |
| `SC_SESSION_TTL` | 会话有效期（秒） | `43200` |
| `SC_ALLOW_ORIGINS` | 允许的跨域来源（逗号分隔） | `http://localhost:5173` |

完整列表见 `server/app/auth/token.py` 与 `server/app/scheduler/core.py` 中的配置读取。

---

## 测试

```powershell
cd server
pytest                        # 全部 96 项
pytest -v                     # 显示用例名
pytest -m "not slow"          # 跳过真实起进程/等超时的用例
pytest tests/test_security.py # 单文件
```

| 文件 | 项数 | 覆盖 |
|---|---|---|
| `test_process_decouple.py` | 33 | shell 判定、日志续读、detach 标志、命名 Job、停止安全性 |
| `test_phase2_recovery.py` | 29 | 启动恢复、心跳判死、四问接管、状态广播 |
| `test_security.py` | 34 | 端点鉴权、登录限流、SSE、路径权限、非 0 退出语义 |

前端构建回归：

```powershell
cd web && npm run build
```

---

## 已知限制

1. **任务独立性依赖启动方式**：必须用 `run_headless.py`；用 `run.py` 在终端跑，关闭终端会连带杀死任务。
2. **停机期间任务自然结束时退出码不可恢复**：此时无进程句柄，`exit_code` 保持 `null`，标 `INTERRUPTED` 并写明原因，**不伪造**。
3. **重启接管后无法精确终止进程组**：命名 Job 已随调度中心销毁，只能用 `taskkill /T` 按进程树终止。
4. **`via_shell=true` 的任务仍是孙进程管理**：含 shell 语法的命令无法避免中间 shell，停止依赖进程树。
5. **依赖 Windows 计划任务**：受限环境只能回退到 `run.py`，此时失去 Job 隔离。
6. **Linux 未验证**：Job Object 相关逻辑仅 Windows 生效。
7. **数据库结构变更走在线迁移**：仅补列，不做破坏性变更。
