# 调度中心 Scheduler Center

一个「菜单 + 状态看板」式的自动化调度中心。它只关心**有哪些可调度、哪些在执行、哪些完成、哪些失败**，至于每道「菜」（系统命令 / 脚本 / 程序 / 删除操作）内部怎么做的，调度中心一概黑盒处理。

**当前进度**：后端调度服务已完成（Python + FastAPI + APScheduler + SQLite）；前端已重构为**管理系统样式**（侧边栏 + 顶栏）+ **全局右侧抽屉**（点击「详情」滑出半屏，纵向表格展示执行内容/配置/执行经历）。配置采用 **crawler 风格的类型驱动结构**：先 `type` 定类型，再在 `params` 里写该类型的专属参数。全局常量映射前后端已完成。全部功能已在本机自检 + 端到端验证通过。

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
| `GET`  | `/api/groups`                     | 任务分组元信息（key/name/remark/task_type/count）                       |
| `GET`  | `/api/running?type=&q=&page=&page_size=` | 运行中实例（PID、日志流），支持类型/名称过滤+分页，返回 `{total,...,items}` |
| `GET`  | `/api/history?status=&job=&type=&q=&page=&page_size=&all=` | 执行历史，可按状态/任务/类型/名称过滤+分页，返回 `{total, page, page_size, items}` |
| `GET`  | `/api/runs/{id}`                     | 单个运行实例详情（含完整日志）                                      |
| `POST` | `/api/jobs/{name}/trigger`           | 手动触发某个 Job                                                    |
| `POST` | `/api/runs/{id}/abort`               | 中止某个运行实例                                                    |
| `GET`  | `/api/health`                        | 健康检查                                                            |
| `GET`  | `/api/config`                        | 服务配置信息                                                        |

---

## 配置任务（mapping）

编辑 `config/jobs.json`。配置风格**对齐 crawler 项目的 `TASK_GROUP_CONFIG`**：
**先分「组」，组内再按 `type` 写 `items`（每条 item 就是一条 Job）**。这样好管理、好添加。

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
| `COMMAND` | 一条命令      | `command`(必填)、`workdir`                                      | 交给系统 shell（cmd / sh）执行                                                             |
| `SCRIPT`  | 脚本          | `path`(必填)、`args`、`workdir`                               | 按扩展名选解释器：`.ps1`→PowerShell、`.bat/.cmd`→cmd、`.py`→Python、`.sh`→bash |
| `PROGRAM` | 直接执行程序  | `path`(必填)、`args`、`workdir`、`detach`                   | spawn 程序（exe 等）；`detach:true` 启动即成功                                           |
| `FILEOP`  | 文件/目录操作 | `action`(必填，大写)、`source`(必填)、`target`、`recursive` | Python 内建动作，见下                                                                      |

**FILEOP 内建动作**（`action`，大写，对应常量 `FILEOP_ACTION`）：

| action          | 说明              | 附带参数                                |
| --------------- | ----------------- | --------------------------------------- |
| `DELETE_FILE` | 删除单个文件      | `source`、`recursive`(如目标是目录) |
| `DELETE_DIR`  | 递归删除目录/文件 | `source`                              |
| `COPY`        | 复制文件/目录     | `source`、`target`                  |
| `MOVE`        | 移动文件/目录     | `source`、`target`                  |

**PROGRAM 的 `detach`**：`true` = 启动即成功（点一下立即 SUCCEEDED，软件在后台独立跑，不等待关闭），适合「点击启动软件」。

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
- **中止**：`POST /api/runs/{id}/abort`，Windows 下用 `taskkill /F /T` 连同子进程树一起终止。

---

## 目录结构

```
scheduler-center/
  server/
    app/
      __init__.py
      models.py      # Job / Run 数据模型与状态机（Job 用 type + params 类型驱动结构）
      executor.py    # 跨平台进程执行器 + FILEOP 内建动作(spawn/pid/kill/日志/超时)
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
      App.vue        # 管理系统布局：侧边栏 + 顶栏 + 全局抽屉
      store/drawer.js# 全局抽屉状态（openJob/closeJob）
      components/    # JobDrawer.vue（右侧半屏抽屉，纵向表格展示执行内容/配置/经历）
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
- [ ] 按 Run 实例流式推日志（WebSocket 或 SSE）
- [ ] 通知（任务失败时 HTTP/Email/微信）让「菜单端上来」更及时
- [ ] 用 NSSM 把后端注册成 Windows 服务开机自启
- [ ] 前端常量分组继续扩展（你后续自己往 constants.py 里加即可）

## 说明

自检与端到端已在本机验证通过：`server/selfcheck.py` 全部 OK（含 FILEOP 真删文件测试），
类型化配置从 API 正确下发，`detach` 触发记事本立即 SUCCEEDED，前端 `npm run build` 通过、
dev 服务 `/api` 代理连通并取回类型化任务。
