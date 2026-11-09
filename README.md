# 调度中心 Scheduler Center

一个「菜单 + 状态看板」式的自动化调度中心。它负责**有哪些任务可调度、哪些在执行、哪些完成、哪些失败**，至于每条「任务」（系统命令 / 脚本 / 程序 / 删除操作）内部具体怎么执行，调度中心一概黑盒处理。

- 后端：Python + FastAPI + APScheduler + SQLite
- 前端：Vue 3 + Vite + Vue Router + Axios + Element Plus（管理系统样式）
- 服务方式：后端进程与前端进程分别启动，前端通过 `/api` 对接后端

---

## 核心能力

### 任务管理（即用入库，免改配置、免重启）

任务存储在数据库 `jobs` 表中，界面内直接 **新增 / 编辑 / 启停 / 删除**，实时生效；定时任务自动注册 / 注销。参数表单由后端按类型动态下发，前端自动渲染，**无硬编码**。

### 任务类型（type 驱动）

先选 `type` 定类型，再填该类型的专属参数 `params`：

| type        | 用途          | 主要参数                                          |
| ----------- | ------------- | ------------------------------------------------- |
| `COMMAND` | 一条命令      | `command`、`workdir`、`detach`              |
| `SCRIPT`  | 脚本          | `path`、`args`、`workdir`、`detach`       |
| `PROGRAM` | 直接执行程序  | `path`、`args`、`workdir`、`detach`       |
| `FILEOP`  | 文件/目录操作 | `action`、`source`、`target`、`recursive` |

- `SCRIPT` 按扩展名选解释器：`.ps1`→PowerShell、`.bat/.cmd`→cmd、`.py`→Python、`.sh`→bash。
- `FILEOP` 内建动作：`DELETE_FILE` / `DELETE_DIR` / `COPY` / `MOVE`。
- `detach`（进程类通用，即「进程独立」）：目标进程作为独立后台进程常驻，调度中心不设超时、随服务一直跑并实时读取输出；状态跟随进程存亡——进程存活期间保持「进行中」，退出后才标记完成（成功/失败）。配套「停止进程」按记录的 PID 精准 `taskkill /F /T` 整树终止，可清理孤儿进程。

### 执行权限模型

调度中心是**通用黑盒任务调度与执行管理平台**，不审查被执行任务的内部业务行为。经**管理员登录认证**后即可执行任意符合操作系统条件的命令 / 脚本 / 程序 / 文件操作：

- **不设路径白名单 / 安全根目录**：`SCRIPT` / `PROGRAM` 的 `path` 可为绝对或相对路径、可跨盘；`COMMAND` 不受任何目录限制；`FILEOP` 可作用于运行账户能访问的任何路径（含 `.git`、`server/data` 等），不再要求配置 `SC_SAFE_ROOT` / `SC_EXEC_ROOT`。
- **无 `SC_ENABLE_FS_PICK` 开关**：文件/路径选择接口 `/api/fs/pick` 仅要求管理员认证即可用；若后端运行环境无图形桌面（tkinter 不可用）会返回明确的「环境不支持」错误。
- 实际操作权限以**调度中心运行账户**在操作系统上拥有的权限为准；路径不存在、无权限等错误由操作系统正常返回并记入失败历史。
- 管理面安全（令牌认证、Bearer 鉴权、跨域白名单、登录限流、审计）与**进程身份校验**（PID + 进程创建时刻）保持不变。

### 全局并发控制

同一任务同一时刻至多一个实例在运行；重复触发会被后端拒绝（409）并在前端提示，避免误点重复执行。

### 服务组管理（把后台任务组成「一台服务」）

把若干「进程独立（`detach`）」的任务按顺序编排为**服务组**，视为一个整体启停：

- 新建 / 编辑服务组：命名、写描述、从后台任务中**勾选成员**并排序（成员可重复/可重命名前台显示标签）。
- **一键启动 / 停止整组**：启动按成员顺序逐个触发；停止按持久化 PID 定向 kill 整组进程（连同子进程树）。
- **整组状态**：任一成员在运行即为「运行中」，否则「已停止」；操作列按状态互斥显示「启动 / 停止」。
- **统一日志视图**：抽屉内并排展示各成员最近一次执行的实时输出，方便观察"一组服务一起起没起来"。
- 高频操作（日志 / 启动或停止）直接摆在操作列；编辑 / 删除 收入「⋯」下拉（删除带二次确认）。

> 服务组只调度 `detach` 后台任务（常驻服务型），一个任务可同时属于多个服务组。

### 实时状态

页面通过 SSE（`/api/events`）订阅任务**开始 / 结束 / 中止 / 状态收敛**事件：各看板自动刷新，执行成功 / 失败 / 超时 / 中止即时弹提示，无需手动刷新、无需轮询。

### 状态一致性与异常恢复（Phase 2）

调度中心启动 / 运行期间会对进程做**状态收敛与异常恢复**，保证"数据库里的状态"与"真实进程"一致：

- **进程身份凭据**：每次拉起都会采集 PID + 进程创建时刻 + 可执行文件 + 命令行指纹 + 进程树根，写入运行记录；重启后能据此可靠地识别"是不是原来那个进程"。
- **启动恢复**：服务重启后扫描残留记录——已确认死亡的收敛为 `INTERRUPTED`；确认存活但丢失管理关系的标记为 `ORPHANED` 并走**四问接管**（身份 / 监控 / 日志 / 停止能力四项校验全通过才重建为 `RUNNING`）。
- **心跳机制**：管理进程定期上报心跳并落库；心跳超时**仅作活性参考**，绝不单独作为"进程已死"的判定依据——是否判定死亡一律以 OS 探针实测为准。
- **安全停止（SAFE-FIRST）**：停止 detach 后台进程前先做身份核验（PID 未被复用/凭据可信），核验失败**拒绝操作、绝不盲杀**；没有有效身份凭据的事件一律归 `UNKNOWN` 待核查，不做幂等自动重试、不模糊认领孤儿。

### 产物 / 运行口径

- **成功**：退出码 `0` → `SUCCEEDED`。
- **异常终止**：进程非 `0` 退出且**非**调度中心主动中止/停止（abort/stop）时 → `INTERRUPTED`（外部终止/异常退出），不再记 `FAILED`；`FAILED` 仅用于引擎自身异常（找不到可执行目标、参数错误等）。
- **超时**：超过 `timeout_s` → timedout 并中止进程树。
- **中止**：`taskkill /F /T` 连同子进程树终止。
- **停止后台进程**：按持久化 PID 定向 kill，兜底清理孤儿进程。
- 子进程输出按原始字节读取并**自动识别编码**：优先按 UTF-8 严格解码，失败则回退系统编码（Windows 中文系统为 GBK/cp936）。这样依赖是 GBK 输出的中文脚本/程序也能在日志里正常显示，避免乱码或解码崩溃。

---

## 前端看板

Element Plus 暗色主题，五个页面覆盖「管任务 → 看执行 → 查历史 → 组服务」的完整使用场景：

| 页面     | 场景                                                                                      |
| -------- | ----------------------------------------------------------------------------------------- |
| 数据看板 | 全局概览：任务/执行数量与状态的图表汇总                                                     |
| 任务列表 | 任务台账：新增 / 编辑 / 启停 / 删除，按类型 / 启用状态 / 名称搜索与排序，「启动」执行，正在执行的任务不可停用 |
| 执行中心 | 正在运行的实例：实时列表、过滤、排序；可查看详情或中止                                        |
| 执行历史 | 已完成实例的历史：按状态 / 类型 / 名称过滤、排序；点击查看详情（含实时日志）                  |
| 服务组    | 把多个 `detach` 后台任务编组成「一台服务」，一键启动/停止整组、统一查看各成员实时日志          |

- 任务详情、执行详情统一以右侧抽屉展示，可分页、可看该次执行的实时输出。
- 新建 / 编辑任务亦在抽屉中完成。
- 任务列表 / 服务组的操作列统一为「互斥主按钮 + ⋯ 收纳次要操作」：主操作右对齐、8px 间距、不换行。
- 删除服务组（及停用中任务）均有 `ElMessageBox` 二次确认，避免误删。

相关文档：`docs/layout-guide.md`（前端统一布局规范）。

---

## 启动

### 后端

```bash
cd server
pip install -r requirements.txt
python run.py          # 启动在 http://0.0.0.0:8000
```

- 访问日志带时间戳；接口文档见 `http://127.0.0.1:8000/docs`（Swagger）。
- 环境变量覆盖默认路径与行为：

| 变量          | 含义              | 默认                         |
| ------------- | ----------------- | ---------------------------- |
| `SC_CONFIG` | 任务配置 json     | `server/config/jobs.json`  |
| `SC_DB`     | SQLite 数据库路径 | `server/data/scheduler.db` |
| `SC_ALLOW_ORIGINS` | 允许的前端跨域来源（逗号分隔） | `http://localhost:5173` |
| `SC_SESSION_TTL` | 会话 token 有效期（秒） | `43200`（12 小时） |
| `SC_TOKEN` | 长期管理凭证（环境变量直给） | 无 |
| `SC_TOKEN_FILE` | 长期管理凭证密钥文件路径 | 默认 `server/data/sc_token` |
| `SC_AUTO_GEN_TOKEN` | 为 `1` 且未配置凭证时首次启动自动生成 | 无 |

### 认证与凭证（会登录）

所有管理接口（除 `login` / `health` / `constants` 等少数）均需会话认证。凭证分两层：

1. **长期管理凭证（登录密码）**：存在 `server/data/sc_token`（默认）。来源优先级：`SC_TOKEN` 环境变量 → `SC_TOKEN_FILE` 指定文件 → 默认 `data/sc_token` → `SC_AUTO_GEN_TOKEN=1` 时自动生成。**该凭证无过期机制**，只要文件存在内容不变就一直有效。
2. **短期会话 token**：登录成功（`POST /api/login` 用长期凭证校验）后签发，存活 `SC_SESSION_TTL`（默认 12h），过期需重新登录；每次服务重启内存会话清空、所有会话立即失效；重新登录会作废旧会话（轮换）。

> ⚠️ **跨机部署说明**：调度中心为**单机单实例**设计，不共享内存会话。另一台机器能否登录取决于是否拉取了相同的 `data/sc_token`：
> - 一起拉取 → 用同一长期凭证可登录，但两台是各自独立的会话实例；
> - 未拉取 + 设了 `SC_AUTO_GEN_TOKEN=1` → 新机器自动生成**不同**的凭证，需用新文件里的值；
> - 未拉取 + 未设 → 无法生成、无法登录。
>
> **安全建议**：`data/sc_token` 已被 `.gitignore` 排除、不会提交到仓库。若在多机部署，请每台单独配置 `SC_TOKEN` / `SC_TOKEN_FILE` 或依赖 `SC_AUTO_GEN_TOKEN`，不要把管理口令带进代码库。

### 登录限流

同一来源 IP 在滑动窗口内连续失败超限会进入冷却，防止暴力猜测长期凭证（可用 `SC_LOGIN_WINDOW` / `SC_LOGIN_MAX_FAIL` / `SC_LOGIN_COOLDOWN` 覆盖）。

### 前端

```bash
cd web
npm install
npm run dev        # 启动在 http://127.0.0.1:5173
```

开发时由 vite 把 `/api` 代理到后端 `127.0.0.1:8000`（见 `web/vite.config.js`），浏览器直接打开即可。

---

## REST API

| 方法       | 路径                                                               | 说明                                                                 |
| ---------- | ------------------------------------------------------------------ | -------------------------------------------------------------------- |
| `GET`    | `/api/jobs?type=&enabled=&q=&page=&page_size=&all=&sort=&order=` | 任务列表：过滤 + 搜索 + 分页 + 排序；`all=true` 忽略分页           |
| `POST`   | `/api/jobs`                                                      | 新增一条任务（实时入库并注册定时）                                   |
| `PUT`    | `/api/jobs/{name}`                                               | 更新一条任务（name 为主键不可改名）                                  |
| `DELETE` | `/api/jobs/{name}`                                               | 删除一条任务（同时注销其定时任务）                                   |
| `POST`   | `/api/jobs/{name}/enable`                                        | 启用/停用；body `{enabled: bool}`；**409**=正在执行不可停用  |
| `POST`   | `/api/jobs/{name}/trigger`                                       | 手动触发；**409**=正在运行（并发上限 1）；**403**=已停用 |
| `GET`    | `/api/job-schema`                                                | 各任务类型 → 参数表单结构（前端据此动态渲染）                       |
| `GET`    | `/api/groups`                                                    | 任务分组元信息                                                       |
| `GET`    | `/api/service-groups`                                            | 服务组列表                                                           |
| `POST`   | `/api/service-groups`                                            | 新建服务组；body `{name, description, members:[{job_name,label,order}]}` |
| `PUT`    | `/api/service-groups/{name}`                                     | 更新服务组（成员 / 描述 / 顺序）                                     |
| `DELETE` | `/api/service-groups/{name}`                                     | 删除服务组（不影响成员任务本身）                                     |
| `GET`    | `/api/service-groups/{name}`                                     | 服务组详情（含成员实时/最近状态）                                    |
| `POST`   | `/api/service-groups/{name}/start`                               | 启动整组：按成员顺序逐个触发；**409**=有成员已在运行                |
| `POST`   | `/api/service-groups/{name}/stop`                                | 停止整组：按持久化 PID 定向 kill 各成员进程树                        |
| `GET`    | `/api/running?type=&q=&status=&page=&page_size=` | 运行中实例（含 PID、异常状态分桶计数 alive_total），多条件过滤 + 分页；`status` 可按目标状态精确过滤（R运行正常/STARTING/ORPHANED/UNKNOWN） |
| `GET`    | `/api/history?status=&job=&type=&q=&page=&page_size=&all=` | 执行历史，多条件过滤 + 分页（`status` 含 INTERRUPTED 等终态） |
| `GET`    | `/api/runs/{id}`                                                 | 单个运行实例详情（含完整日志与身份/心跳元信息）                       |
| `POST`   | `/api/runs/{id}/abort`                                           | 中止运行实例（非 detach 的强中止）                                    |
| `POST`   | `/api/runs/{id}/stop`                                            | 停止 detach 后台进程（身份核验后按持久化 PID 定向 kill，核验失败则拒绝） |
| `GET`    | `/api/constants`                                                 | 全局状态 / 类型 / 分组常量字典（含状态中文翻译）                       |
| `POST`   | `/api/login`                                                     | 用长期管理凭证换取短期会话 token，body `{token}`                       |
| `POST`   | `/api/logout`                                                    | 主动登出，作废当前会话 token                                          |
| `GET`    | `/api/audit`                                                     | 审计日志                                                              |
| `GET`    | `/api/health`                                                    | 健康检查（无需认证）                                                   |
| `GET`    | `/api/config`                                                    | 服务配置信息                                                           |
| `GET`    | `/api/events`                                                    | SSE 事件流（任务开始/结束/中止/状态收敛实时推送，需会话认证）           |

---

## 执行状态机

**进程类任务**（COMMAND / SCRIPT / PROGRAM）的状态演进如下（Phase 1 + Phase 2）：

```
trigger ──► STARTING ──► RUNNING ──► SUCCEEDED
                  │            ├──► FAILED
                  │            ├──► TIMEDOUT
                  │            └──► KILLED (用户中止 / 安全停止)
                  │
                  ├──► UNKNOWN      # 预登记后无法安全核验（无身份凭据 / 遗留记录）
                  └──► INTERRUPTED  # 确认进程已死，收敛为异常终态

RUNNING ──(重启且存活)──► ORPHANED ──(四问接管通过)──► RUNNING(adopted)
RUNNING ──(重启且存活)──► ORPHANED ──(接管未通过)──► 保持 ORPHANED 待人工处理
```

各异常状态的语义：

| 状态 | 中文 | 进程真实情况 | 触发场景 |
| ---- | ---- | ------------ | -------- |
| `STARTING` | 预登记中 | 正要拉起 | 触发任务、spawn 前的中间态（防崩溃丢失启动意图） |
| `RUNNING` | 进行中 | 存活且正常管理 | 正常执行 / 孤儿四问接管后重建 |
| `ORPHANED` | 孤儿 | 存活但管理关系丢失 | detach 进程在服务崩溃后继续存活，待四问接管 |
| `UNKNOWN` | 待核查 | 无法安全判断 | 无身份凭据的残留 STARTING / Phase-1 遗留 / 接管失败自救 |
| `INTERRUPTED` | 已中断 | 确认已死 | 重启收敛死进程 / 心跳超时 + OS 探针确认死亡 / 未登记用户停止的非 0 退出（外部终止或异常退出） |
| `SUCCEEDED` / `FAILED` / `TIMEDOUT` / `KILLED` | 终态 | 已退出 | 正常完成 / 引擎异常(IO/参数/找不到目标) / 超时中止 / 用户中止 |

> `FILEOP` 为同步文件操作，无进程态，直接落 `SUCCEEDED` / `FAILED`。
> 异常任务一律保持终态、**不自愈、不自动重试**，由管理员显式手动重新触发。

---

## 目录结构

```
scheduler-center/
  server/
    app/
      models.py      # Job / Run 数据模型与状态机（type + params 类型驱动结构）
      executor.py    # 跨平台进程执行器 + FILEOP 内建动作
      scheduler.py   # 调度核心 + APScheduler 定时 + 异常恢复/心跳/安全停止
      store.py       # SQLite 持久化
      auth.py        # 认证与会话管理（长期凭证 / 会话 token / 登录限流）
      identity.py    # 进程身份采集与安全停止（PID + 创建时刻 + 指纹 / OS 探针）
      main.py        # FastAPI 路由
      constants.py   # 全局枚举常量字典
    config/jobs.json # 老式任务配置（数据库为空时的首次迁移种子）
    scripts/         # 示例脚本
    data/            # SQLite 数据库（运行时生成）
    requirements.txt
    run.py           # 后端启动入口
  web/               # Vue 3 + Element Plus 前端
    src/
      constants.js   # 前端全局常量映射
      api.js         # axios 请求封装
      router.js      # 页面路由（/dashboard、/jobs、/running、/history、/service-groups）
      App.vue        # 管理系统骨架 + 全局抽屉 + SSE 订阅
      store/         # drawer、refresh、toast、realtime
      composables/   # useTablePanel 等共享逻辑
      components/    # JobDrawer / JobForm / JobEditModal / RunDetailDrawer / RunLogPanel / PagerBar
      views/         # DashboardBoard / MenuBoard / RunningBoard / HistoryBoard / ServiceGroupBoard
    vite.config.js   # dev 代理 /api -> 后端
    package.json
```
