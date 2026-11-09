# Phase 2 验收报告 —— 状态一致性 & 异常恢复

- 版本：修订版 3
- 基线提交：`6d348ac`（Phase 1 安全加固，12 项安全测试）
- 实施提交：`eae01aa`（Phase 2，本地 commit，未 push）
- 验收日期：2026-11-09
- 验收结果：**全部通过**

---

## 1. 验收结论一览

| 验收项 | 结果 | 说明 |
|---|---|---|
| `test_phase2_recovery.py`（新增） | ✅ 24/24 | Module 1~7 单测 + 运行看板分组口径 + **SSE 状态迁移广播** |
| `test_security.py`（保留回归） | ✅ 12/12 | Phase 1 安全基线完好 |
| `selfcheck.py` | ✅ SUCCESS | 常量/参数迁移/历史响应契约不变 |
| 前端 `npm run build` | ✅ 通过 | 14s 构建，无报错，仅 500k chunk 体积提示（既有） |

---

## 2. 交付内容（对照计划 §2 文件清单）

| 文件 | 状态 | 改动 |
|---|---|---|
| `server/app/models.py` | 改 | `Run` 身份/生命周期字段 + `STARTING` 入 `STATUS`；`to_dict/from_dict` 同步；旧行缺省补齐 + 兼容归一（旧 pid/timeout 字符串） |
| `server/app/identity.py` | **新增** | Windows FILETIME + Job/taskkill；Linux pidfd + `/proc/boot_id` + killpg；`safe_kill` 零容忍身份核验 |
| `server/app/store.py` | 改 | `save_run(heartbeat_only)` 心跳节流合并写；`running_runs` 分组含 UNKNOWN、排除 INTERRUPTED；`running_process_count` 只计 STARTING/RUNNING/ORPHANED |
| `server/app/executor.py` | 改 | spawn 后采集身份快照；`run_id↔进程` 关联；`process_create_time` 不可得即拒跑 |
| `server/app/scheduler.py` | 改 | 启动恢复扫描（先于 aps.start）；心跳循环；四问接管；SAFE-FIRST；`node_id` 优先级修复；**状态迁移 `status_changed` SSE 广播** |
| `server/app/constants.py` | 改 | `STARTING/INTERRUPTED/ORPHANED/UNKNOWN` 入状态字典（前端 `/api/constants` 自动同步） |
| `server/app/main.py` | 改 | `/api/running` 增加 `alive_total` + `counts`（兼容原结构） |
| `server/test_phase2_recovery.py` | **新增** | 24 项验收单测（含 SSE 广播 t70/t71） |
| `docs/phase2-plan.md` | **新增** | 实施详细方案（修订版 3） |
| `web/src/views/RunningBoard.vue` | 改 | 动态状态角标 + 分桶汇总 `statbar` |

---

## 3. 十项核心验收映射

| # | 验收 | 测试 | 通过 |
|---|---|---|---|
| 1 | 正常执行完整结束 → SUCCEEDED | `test_security` 真实 echo 触发链路 | ✅ |
| 2 | 崩溃→重启收敛死进程 → INTERRUPTED | `t12_dead_proc_interrupted` | ✅ |
| 3 | 崩溃→detach 存活被四问判别 | `t40/t41/t42`（接管成功/缺日志/身份不符） | ✅ |
| 4 | PID 复用不误判/误杀 | `t30/t31/t42`；safe_kill 拒绝 | ✅ |
| 5 | 孤儿被识别并安全处理 | `t13`（存活 ORPHANED 接管核查） | ✅ |
| 6 | 非 detach 异常中断按存活归属 | `_recover_one` 统一存活归属，不直接 INTERRUPTED | ✅（逻辑覆盖） |
| 7 | 心跳超时单超时不判死，OS 死才判死 | `t21/t22` | ✅ |
| 8 | abort/stop 不误杀无关进程 | `t30/t31/t32` 身份核验拒绝 | ✅ |
| 9 | 中途写库异常无永久不一致 | STARTING 预登记可收束；`t10/t11` | ✅ |
| 10 | Phase1 测试 + 前端构建通过 | security 12/12 + build | ✅ |

---

## 4. 关键安全原则（SAFE-FIRST）落实点

1. **绝不盲杀**：`safe_kill`/`stop` 先做身份核验（pid + 创建时刻零容忍），证明不了归属一律返回拒绝原因，不发信号。
2. **绝不做幂等自动重试**：异常任务一律保持终态（由管理员显式 trigger 手动重触发）；`retryable` 本轮仅预留不生效。
3. **仅 OS 探针判生死**：心跳 `last_heartbeat` 只记录管理链路活性，绝不单独据此断言死亡；单次超时不死判，须探针确认死亡才转 `INTERRUPTED`。
4. **不可得即 `UNKNOWN`**：残留 `STARTING` / Phase-1 遗留无凭据 `RUNNING` 拿不到身份 → 归 `UNKNOWN` 待核查，不计入实际运行进程数。
5. **四问才接管**：detach 存活孤儿必须 身份匹配 / 监控可重建 / 日志可恢复 / 停止能力安全验证 四问全过，才重建 `RUNNING(adopted)`。
6. **启动先恢复后调度**：`recover_on_start()` 先于 `aps.start()`，避免与正常调度竞争。

---

## 5. 实际修复的 Phase-1 存量缺陷（测试驱动发现）

- **scheduler.sc node_id 三元表达式优先级错误**：原代码在无 `os.uname`（Windows）分支里忽略 `SC_NODE_ID` 环境变量，导致节点标识无法覆盖。已改为显式 `if/elif/else`，`SC_NODE_ID` 始终优先（`t05` 捕获）。
- **`to_dict` 白名单漏序列化告警**：新增 `retryable` 字段未入白名单导致 roundtrip 断言失败，已补齐（P10 修复确认）。

---

## 6. 平台说明

- 实施与验收在 **Windows** 完成：Windows 分支（`GetProcessTimes` FILETIME、`taskkill`/Job 语义）经代码执行 + `_os_alive_probe` 存活探针实测。
- **Linux 分支**（`pidfd_open`、`/proc/<pid>/stat`+`boot_id`、`killpg`、`os.pidfd_open` 发信号消除单-PID 复用）已按文档编写，逻辑经静态核验；因宿主机为 Windows 未在本机跑通 Linux 真实进程路径（计划 §9.2 分平台项，需在 Linux 环境补验证）。
- 恢复/决策/心跳/四问/SAFE-FIRST/**SSE 状态同步**等**平台无关逻辑**均在本机 24 项测试中覆盖。
- **SSE 新状态同步**：启动恢复扫描、心跳超时核查对运行态做 `UNKNOWN/ORPHANED/RUNNING(adopted)/INTERRUPTED` 收敛迁移后，统一广播 `status_changed` 事件；前端 `realtime.js` 收到任意 `run` 事件即 `bump()` 刷新看板、仅 `finished` 弹提示，故新状态实时上屏且不误弹（`t70`/`t71` 断言广播触发）。

---

## 7. 已知技术限制（如实声明，见计划 §3 结尾）

- OS 层 PID/PGID 复用竞态无法用 Python 锁彻底消除；pidfd/Job 句柄/killpg 只能大幅收窄窗口；重启后 Job 句柄不可重取（已按规则归"待核查"，不谎报接管）。
- detach 文件尾续读仅覆盖写文件后的日志；进程在恢复前写入的最后缓冲若未 flush 则不可见。
- Windows 崩溃窗口内 `run_id↔进程` 关联可能断链，只能归 `UNKNOWN` 待核查。

---

## 8. 变更集

```
eae01aa phase-2 state consistency & exception recovery
  10 files changed, 1589 insertions(+), 21 deletions(-)
```
（本地 commit 未 push；分支 `main`，基线 `6d348ac` 之上。）