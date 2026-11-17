# -*- coding: utf-8 -*-
"""业务服务层。

只承载「包含编排逻辑」的接口；纯透传的查询接口由 API 层直接调用 Scheduler。

按保守方案（阶段1B 决定）：
  job_service.py    —— 任务写操作（创建 / 更新 / 删除 / 启停）
  group_service.py  —— 服务组（增删改查 + 启动 / 停止）
  run_service.py    —— 暂缓。运行相关接口暂时直接调用 Scheduler，
                       待运行编排确实变复杂时再引入。

不在此层重复实现 Scheduler / Executor / Store 已有的能力。
"""
