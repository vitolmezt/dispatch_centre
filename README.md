# 调度中心 Scheduler Center

一个「菜单 + 状态看板」式的自动化调度中心。它只关心**有哪些可调度、哪些在执行、哪些完成、哪些失败**，至于每道「菜」（系统命令 / 脚本 / 程序 / 删除操作）内部怎么做的，调度中心一概黑盒处理。

**当前进度**：后端调度服务已完成（Python + FastAPI + APScheduler + SQLite）；前端已重构为**管理系统样式**（侧边栏 + 顶栏）+ **全局右侧抽屉**（点击「详情」滑出半屏，纵向表格展示执行内容/配置/执行经历）。配置采用 **crawler 风格的类型驱动结构**：先 `type` 定类型，再在 `params` 里写该类型的专属参数。

**任务已入库管理**：指令不再需要改 `config/jobs.json` 后重启——任务存储在数据库 `jobs` 表中，界面上可直接 **新增 / 编辑 / 停用 / 删除**，并自动注册/注销定时任务，实时生效（无需重启）。参数表单由后端按类型动态下发，前端自动渲染，**无硬编码**。同时内置**全局并发控制**（同一任务同一时刻最多一个实例在运行，重复触发会被阻止）和**全局动作结果提示**（屏幕中心上方弹出，执行成功/失败/超时/中止即时反馈）。

全部功能已在本机自检 + 端到端验证通过。

---

## 技术栈

| 层       | 技术                                                            |
| -------- | --------------------------------------------------------------- |
| 后端     | Python 3.10+ / FastAPI / uvicorn / APScheduler / SQLite(stdlib) |
| 前端     | Vue3 + Vite + Vue Router + Axios（菜单 / 运行中 / 历史 / 详情） |
| 服务方式 | 后端进程 + 前端进程分别启动，前端连后端 API                     |

---

## 全局常量字典（枚举映射）

后端在 `server/app/constants.py` 定义分组字典，前端启动时通过 `GET /api/constants`
拉取为全局常量，统一做「英文 KEY → 中文文案」翻译，避免重复硬编码。

| 分组              | KEY（后端返回）                                                | 中文                                              |
| ----------------- | -------------------------------------------------------------- | ------------------------------------------------- |
| `STATUS`        | `PENDING / RUNNING / SUCCEEDED / FAILED / TIMEDOUT / KILLED` | 待开始 / 进行中 / 已完成 / 失败 / 已超时 / 已中止 |
| `JOB_TYPE`      | `COMMAND / SCRIPT / PROGRAM / FILEOP`                        | 命令 / 脚本 / 程序 / 文件操作                     |
| `TRIGGER`       | `MANUAL / CRON / API`                                        | 手动触发 / 定时触发 / 接口触发                    |
| `RESULT`        | `OK / FAIL`                                                  | 成功 / 失败                                       |
| `ENABLED`       | `ON / OFF`                                                   | 已启用 / 已停用                                   |
| `FILEOP_ACTION` | `DELETE_FILE / DELETE_DIR / COPY / MOVE`                     | 删除文件 / 删除目录 / 复制 / 移动                 |
| `EXECUTER`      | `LINE / THREAD / PROCESS`                                    | 线性 / 多线程 / 多进程                            |

**添加新常量**：只需改 `constants.py` 往 `ALL_GROUPS` 里加 dict，前后端都自动生效。
前端读取方式见 `web/src/constants.js`（`statusLabel` / `jobTypeLabel` / `triggerLabel` / `t`）。

---

## 启动前端看板

在另一个终端：

```bash
cd web
npm install
npm run dev        # 启动在 http://127.0.0.1:5173
```

开发时由 vite 把 `/api` 代理到后端 `127.0.0.1:8000`（见 `web/vite.config.js`），
直接浏览器打开即可看板。

页面：**菜单**（按分组分区展示、可触发）/ **运行中**（实时、可中止）/ **历史**（可按状态过滤、失败高亮）/ **执行详情**（实时日志）。

---

## 启动后端服务

```bash
cd server
pip install -r requirements.txt
python run.py          # 启动在 http://0.0.0.0:8000
```

访问日志带时间戳：`2026-11-02 09:51:37 127.0.0.1:49778 - "GET /api/jobs HTTP/1.1" 200`。

启动即可访问 API。接口文档见 `http://127.0.0.1:8000/docs`（Swagger 自动生成）。

可用环境变量覆盖默认路径：

| 变量          | 含义              | 默认                         |
| ------------- | ----------------- | ---------------------------- |
| `SC_CONFIG` | 任务配置 json     | `server/config/jobs.json`  |
| `SC_DB`     | SQLite 数据库路径 | `server/data/scheduler.db` |

---

## REST API

| 方法     | 路径                                   | 说明                                                                |
| -------- | -------------------------------------- | ------------------------------------------------------------------- |
| `GET`  | `/api/jobs?type=&enabled=&q=&page=&page_size=&all=` | 菜单：搜索+分页。`type`=JOB_TYPE 枚举，`enabled`=ON/OFF，`q`=名称模糊，`page`/`page_size` 分页，`all=true` 忽略分页返回全部。返回 `{total, page, page_size, items}` |
| `POST` | `/api/jobs`                       | 新增一条指令（body 同 Job 结构，实时入库并注册定时）          |
| `PUT`  | `/api/jobs/{name}`                | 更新一条指令（name 作为主键不可改名，实时生效）                |
| `DELETE` | `/api/jobs/{name}`             | 删除一条指令（同时注销其定时任务）                             |
| `POST` | `/api/jobs/{name}/enable`         | 启用/停用；body `{enabled: bool}`                              |
| `GET`  | `/api/job-schema`                 | 各任务类型→参数表单结构（前端据此动态渲染配置表单）            |
| `GET`  | `/api/groups`                     | 任务分组元信息（key/name/remark/task_type/count）                       |
| `GET`  | `/api/running?type=&q=&page=&page_size=` | 运行中实例（PID、日志流），支持类型/名称过滤+分页，返回 `{total,...,items}` |
| `GET`  | `/api/history?status=&job=&type=&q=&page=&page_size=&all=` | 执行历史，可按状态/任务/类型/名称过滤+分页，返回 `{total, page, page_size, items}` |
| `GET`  | `/api/runs/{id}`                     | 单个运行实例详情（含完整日志）                                      |
| `POST` | `/api/jobs/{name}/trigger`           | 手动触发某个 Job。**409**=该任务正在运行（全局并发上限=1，已阻止）；**403**=已停用 |
| `POST` | `/api/runs/{id}/abort`               | 中止某个运行实例（依赖内存 Popen 引用，针对普通运行）            |
| `POST` | `/api/runs/{id}/stop`                | 停止 detach 后台进程（按持久化的 PID 定向 kill，清理孤儿进程）      |
| `GET`  | `/api/health`                        | 健康检查                                                            |
| `GET`  | `/api/config`                        | 服务配置信息                                                        |

> **并发控制**：任一指令同一时刻只允许**一个**运行实例。同任务重复触发时后端返回 `409`，
> 界面弹出「该指令正在执行中…」提示——避免误点一次又点一次导致重复执行。

---

## 配置任务（mapping）

> **新机制（推荐）**：任务在界面上新增/编辑即写入数据库，**无需改 jobs.json、无需重启**。
> `config/jobs.json` 仅在**数据库为空时做首次迁移种子**（升级老数据用），之后以数据库为唯一权威。

`jobs.json` 的 JSON 结构（供首次导入 / 批量配置参考）：

```jsonc
{
  "groups": [
    {
      "key": "system-commands",       // 分组标识（唯一）
      "name": "系统命令",              // 分组名（菜单按分组分区展示）
      "remark": "执行系统命令和脚本，如数据库升级、数据初始化等",
      "task_type": "COMMAND",        // 组级默认 type（组内 item 的 type 可覆盖）
      "items": [                       // 组内任务列表，每条即一个菜单项
        {
          "key": "night-clean",        // 任务名（唯一标识，key 优先于 name）
          "name": "夜间清理",
          "type": "COMMAND",           // COMMAND | SCRIPT | PROGRAM | FILEOP
          "params": {                  // ← 该 type 专属的参数（见下表）
            "command": "echo done && del /q D:\\tmp\\*.tmp",
            "workdir": "D:\\jobs"
          },
          "schedule": "0 3 * * *",    // cron；null 表示仅手动触发
          "timeout_s": 300,
          "enabled": true,             // 清单里的「上菜」开关
          "description": "夜间清理"
        }
      ]
    }
  ]
}
```

> 组内 item 的 `type` 缺省时取组级 `task_type`；`group` 缺省时取外层分组的 `key`。
>
> **向后兼容**：旧写法（顶层 `{ "jobs": [...] }` 或裸数组 + 扁平 `command`/`path`/`args`/`workdir`/`detach`）
> 仍会被解析与自动迁移，不会破坏已有配置。

### type 各取值及 params 字段

| type        | 用途          | `params` 字段                                                     | 执行方式                                                                                   |
| ----------- | ------------- | ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `COMMAND` | 一条命令      | `command`(必填)、`workdir`、`detach`                      | 交给系统 shell（cmd / sh）执行；`detach:true` 后台独立运行                                    |
| `SCRIPT`  | 脚本          | `path`(必填)、`args`、`workdir`、`detach`          | 按扩展名选解释器：`.ps1`→PowerShell、`.bat/.cmd`→cmd、`.py`→Python、`.sh`→bash；`detach` 同上 |
| `PROGRAM` | 直接执行程序  | `path`(必填)、`args`、`workdir`、`detach`                   | spawn 程序（exe 等）；`detach:true` 启动即成功                                           |
| `FILEOP`  | 文件/目录操作 | `action`(必填，大写)、`source`(必填)、`target`、`recursive` | Python 内建动作，见下                                                                      |

**FILEOP 内建动作**（`action`，大写，对应常量 `FILEOP_ACTION`）：

| action          | 说明              | 附带参数                                |
| --------------- | ----------------- | --------------------------------------- |
| `DELETE_FILE` | 删除单个文件      | `source`、`recursive`(如目标是目录) |
| `DELETE_DIR`  | 递归删除目录/文件 | `source`                              |
| `COPY`        | 复制文件/目录     | `source`、`target`                  |
| `MOVE`        | 移动文件/目录     | `source`、`target`                  |

**`detach`（进程类通用：COMMAND / SCRIPT / PROGRAM）**：`true` = 启动即成功（点一下立即 SUCCEEDED），目标进程作为**独立后台进程**在系统里常驻运行，调度中心**不等待其退出、不读取其输出**，不再产生孤儿。配合「停止进程」（`POST /api/runs/{id}/stop`）按记录的 PID 精准 `taskkill /F /T` 整树终止，适合「点击启动常驻服务」场景。**

> **detach 三要素**：① 进程以 `CREATE_NO_WINDOW + stdin/stdout 到 DEVNULL` 脱离调度中心独立存活；② 该 run 的 PID 持久化入库，供“停止进程”调用；③ 调度中心关闭 / 重启**不会**连带终止 detach 后台进程（永不尾随 kill）。**注意**：detach 启动即成功，若进程随后异常，run 仍记为 SUCCEEDED——如需判别存活请用「停止进程」返回值或直接看进程。

---

## 执行状态机

```
trigger ──► pending ──► running ──► succeeded
                              ├──► failed
                              ├──► timedout
                              └──► killed (用户中止)
```

- **成功判定**：退出码 `0` → succeeded，否则 failed。
- **超时**：超过 `timeout_s` → timedout 并中止进程树。
- **中止**：`POST /api/runs/{id}/abort`，Windows 下用 `taskkill /F /T` 连同子进程树一起终止（依赖内存中的 Popen 引用，仅对仍在运行表的普通任务有效）。
- **停止后台进程**：`POST /api/runs/{id}/stop`，按**持久化的 PID** 定向 `taskkill /F /T`——即使 detach 进程已脱离跟踪、或调度中心曾重启，也能兜底按 PID 清掉孤儿进程。

> **子进程输出统一按 UTF-8 读取**（`text=True` 叠 `encoding="utf-8", errors="replace"`）：修掉 Windows 下默认 GBK 解码在子进程输出中文/特殊字节时抛 `UnicodeDecodeError`、使读取线程崩溃而被误判“启动失败”的问题。`errors="replace"` 兜底非法字节（边界截断）不中断。

---

## 目录结构

```
scheduler-center/
  server/
    app/
      __init__.py
      models.py      # Job / Run 数据模型与状态机（Job 用 type + params 类型驱动结构）
      executor.py    # 跨平台进程执行器 + FILEOP 内建动作(spawn/pid/kill/日志/超时/detach独立后台/UTF-8解码)
      scheduler.py   # 调度核心 + APScheduler 定时
      store.py       # SQLite 持久化
      main.py        # FastAPI 路由
      constants.py   # 全局枚举常量字典
    config/jobs.json # 你的任务映射配置（type + params 结构）
    scripts/         # 示例脚本
    data/            # SQLite 数据库(运行时生成)
    requirements.txt
    run.py           # 后端启动入口（http://0.0.0.0:8000）
  web/               # Vue3 + Vite 前端（管理系统样式）
    src/
      constants.js   # 前端全局常量映射
      api.js         # axios 请求封装
      router.js      # 页面路由
      App.vue        # 管理系统布局：侧边栏 + 顶栏 + 全局抽屉 + ToastHost
      store/drawer.js# 全局抽屉状态（openJob/closeJob）
      store/toast.js # 全局动作结果提示（toast.success/error/info）
      components/    # JobDrawer/JobForm(动态表单)/JobEditModal/ToastHost/RunDetailDrawer
      views/         # MenuBoard/RunningBoard/HistoryBoard/RunDetail
    vite.config.js   # dev 代理 /api -> 后端
    package.json
```

---

## 后续规划

- [X] 前端 Vue3 + Vite：菜单页 / 运行中页 / 历史页，通过 `/api/*` 对接
- [X] 前端重构为管理系统样式 + 全局右侧抽屉（详情面板）
- [X] `fileop` 内置动作（DELETE_FILE / DELETE_DIR / COPY / MOVE，大写、常量化）
- [X] `PROGRAM` 支持 `detach`（点击启动软件，启动即成功）
- [X] `detach` 扩展至全部进程类（COMMAND / SCRIPT / PROGRAM），独立后台 + 记录 PID + 附带"停止进程"清理孤儿
- [X] 子进程输出统一按 UTF-8 读取（修 Windows GBK 解码崩溃）
- [X] 指令 CRUD：新增/编辑/停用/删除入库，界面可直接操作、免改 json 免重启
- [X] 类型驱动的动态配置表单（前端按 `/api/job-schema` 自动渲染参数，无硬编码）
- [X] 全局并发控制：同一指令同一时刻最多一个实例，重复触发被阻止（409）
- [X] 全局动作结果提示：执行成功/失败/超时/中止在屏幕中心上方弹出
- [ ] 按 Run 实例流式推日志（WebSocket 或 SSE）
- [ ] 通知（任务失败时 HTTP/Email/微信）让「菜单端上来」更及时
- [ ] 用 NSSM 把后端注册成 Windows 服务开机自启
- [ ] 前端常量分组继续扩展（你后续自己往 constants.py 里加即可）

## 说明

自检与端到端已在本机验证通过：`server/selfcheck.py` 全部 OK（含 FILEOP 真删文件测试），
类型化配置从 API 正确下发，`detach` 触发记事本立即 SUCCEEDED，前端 `npm run build` 通过、
dev 服务 `/api` 代理连通并取回类型化任务。

**本次变更验证（真实 Windows 宿主）**：① UTF-8 中文输出任务 `SUCCEEDED`、日志中文无损、无 `UnicodeDecodeError`；
② detach 任务启动即 `SUCCEEDED` 且 PID 落库，`POST /api/runs/{id}/stop` 命中该 PID 用 `taskkill /F /T`
整树杀掉；③ detach 后台进程独立存活、调度中心关闭不连带杀（宿主级闭环测试通过）。
