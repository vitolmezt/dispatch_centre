# -*- coding: utf-8 -*-
"""调度中心后端测试包。

本包内所有测试均基于 pytest，运行方式见 tests/README.md。

模块职责：
  conftest.py                    共享 fixture 与环境初始化（导入业务模块前完成）
  test_process_decouple.py       进程生命周期解耦（23 项）
  test_phase2_recovery.py        异常恢复与状态收敛（26 项）
  test_security.py               认证鉴权边界与业务契约回归（12 项）
"""
