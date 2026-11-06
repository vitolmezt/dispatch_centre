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

### 全局并发控制

同一任务同一时刻至多一个实例在运行；重复触发会被后端拒绝（409）并在前端提示，避免误点重复执行。

### 实时状态

页面通过 SSE（`/api/events`）订阅任务**开始 / 结束 / 中止**事件：各看板自动刷新，执行成功 / 失败 / 超时 / 中止即时弹提示，无需手动刷新、无需轮询。

### 产物 / 运行口径

- **成功**：退出码 `0` → succeeded，否则 failed。
- **超时**：超过 `timeout_s` → timedout 并中止进程树。
- **中止**：`taskkill /F /T` 连同子进程树终止。
- **停止后台进程**：按持久化 PID 定向 kill，兜底清理孤儿进程。
- 子进程输出统一按 UTF-8 读取（`errors="replace"` 兜底非法字节），避免 Windows 下 GBK 解码崩溃。

---

## 前端看板

管理系统三栏骨架（侧边栏 + 顶栏 + 内容区），Element Plus 暗色主题，全局右侧抽屉展示详情。

| 页面     | 说明                                                                                                                        |
| -------- | --------------------------------------------------------------------------------------------------------------------------- |
| 任务列表 | 任务管理台账：新增 / 编辑 / 启停 / 删除；支持按类型、启用状态、名称搜索与排序；「启动」二次确认执行；正在执行的任务不可停用 |
| 执行中心 | 正在运行的实例：实时列表、按类型/名称过滤、排序；可**详情**（打开大尺寸抽屉）或**中止**                         |
| 执行历史 | 已完成实例的历史：按状态 / 类型 / 名称过滤、排序；点击某条打开**详情抽屉**（含实时日志）                              |

- 详情统一为**全局右侧抽屉**：占约 4/5 屏宽，点击空白处即关闭。任务详情与执行详情各自成套。
- 删除操作走应用内确认弹窗（非浏览器原生 confirm）。
- 排序为后端排序（跨页生效），各列 `sortable` + `@sort-change` 触发。

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
- 环境变量覆盖默认路径：

| 变量          | 含义              | 默认                         |
| ------------- | ----------------- | ---------------------------- |
| `SC_CONFIG` | 任务配置 json     | `server/config/jobs.json`  |
| `SC_DB`     | SQLite 数据库路径 | `server/data/scheduler.db` |

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
| `GET`    | `/api/running?type=&q=&page=&page_size=`                         | 运行中实例（PID、日志流），过滤 + 分页                               |
| `GET`    | `/api/history?status=&job=&type=&q=&page=&page_size=&all=`       | 执行历史，多条件过滤 + 分页                                          |
| `GET`    | `/api/runs/{id}`                                                 | 单个运行实例详情（含完整日志）                                       |
| `POST`   | `/api/runs/{id}/abort`                                           | 中止运行实例                                                         |
| `POST`   | `/api/runs/{id}/stop`                                            | 停止 detach 后台进程（按持久化 PID 定向 kill）                       |
| `GET`    | `/api/health`                                                    | 健康检查                                                             |
| `GET`    | `/api/config`                                                    | 服务配置信息                                                         |
| `GET`    | `/api/events`                                                    | SSE 事件流（任务开始/结束/中止实时推送）                             |

---

## 执行状态机

```
trigger ──► pending ──► running ──► succeeded
                              ├──► failed
                              ├──► timedout
                              └──► killed (用户中止)
```

---

## 目录结构

```
scheduler-center/
  server/
    app/
      models.py      # Job / Run 数据模型与状态机（type + params 类型驱动结构）
      executor.py    # 跨平台进程执行器 + FILEOP 内建动作
      scheduler.py   # 调度核心 + APScheduler 定时
      store.py       # SQLite 持久化
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
      router.js      # 页面路由（/jobs、/running、/history）
      App.vue        # 管理系统骨架 + 全局抽屉 + SSE 订阅
      store/         # drawer、refresh、toast、realtime
      components/    # JobDrawer / JobForm / JobEditModal / RunDetailDrawer / PagerBar
      views/         # MenuBoard / RunningBoard / HistoryBoard
    vite.config.js   # dev 代理 /api -> 后端
    package.json
```
