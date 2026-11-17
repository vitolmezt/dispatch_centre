# -*- coding: utf-8 -*-
"""API 接口层。

按业务资源划分路由模块，每个模块只负责：
  - 接收 HTTP 请求、解析参数
  - 调用 Scheduler（或后续的业务 Service 层）
  - 映射 HTTP 异常、返回 JSON

不在此层实现业务编排逻辑。

模块划分：
  dependencies.py  公共依赖：Scheduler 单例获取、Bearer token 解析
  jobs.py          任务管理（8 端点）
  runs.py          执行记录与运行控制（6 端点）
  groups.py        服务组（7 端点）
  auth.py          登录 / 登出（2 端点）
  system.py        常量 / 健康 / 配置 / 审计（4 端点）
  events.py        SSE 事件流、执行日志流、文件选择器（3 端点）
"""
