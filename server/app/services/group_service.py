# -*- coding: utf-8 -*-

from __future__ import annotations

from fastapi import HTTPException

from .. import audit
from ..models import ServiceGroup
from ..scheduler import Scheduler


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


class GroupService:
    """服务组业务。构造时注入 Scheduler 门面，不自行持有任何状态。"""

    def __init__(self, scheduler: Scheduler):
        self._sched = scheduler

    # ------------------------------------------------------------------
    # 创建
    # ------------------------------------------------------------------
    def create_group(self, payload: dict) -> dict:
        s = self._sched
        group = ServiceGroup.from_dict(payload)
        errs = group.validate()
        if errs:
            raise _bad_request("；".join(errs))
        if group.name in s.service_groups:
            raise HTTPException(status_code=409, detail=f"服务组已存在: {group.name}")
        s.add_service_group(group)
        audit.audit("service_group.create", obj=group.name,
                    detail=f"members={len(group.members)}")
        return {"ok": True, "name": group.name}

    # ------------------------------------------------------------------
    # 更新
    # ------------------------------------------------------------------
    def update_group(self, name: str, payload: dict) -> dict:
        s = self._sched
        if name not in s.service_groups:
            raise HTTPException(status_code=404, detail=f"服务组不存在: {name}")

        data = dict(payload)
        data["name"] = name          # 服务组不允许改名
        group = ServiceGroup.from_dict(data)
        errs = group.validate()
        if errs:
            raise _bad_request("；".join(errs))

        s.update_service_group(group)
        audit.audit("service_group.update", obj=name,
                    detail=f"members={len(group.members)}")
        return {"ok": True, "name": name}

    # ------------------------------------------------------------------
    # 删除
    # ------------------------------------------------------------------
    def delete_group(self, name: str) -> dict:
        s = self._sched
        if name not in s.service_groups:
            raise HTTPException(status_code=404, detail=f"服务组不存在: {name}")
        s.delete_service_group(name)
        audit.audit("service_group.delete", obj=name)
        return {"ok": True, "name": name}

    # ------------------------------------------------------------------
    # 详情（收口私有方法访问）
    # ------------------------------------------------------------------
    def get_detail(self, name: str) -> dict:
        s = self._sched
        group = s.service_groups.get(name)
        if group is None:
            raise HTTPException(status_code=404, detail=f"服务组不存在: {name}")
        # 原 API 层直接调用 s._service_group_view(group)，此处收口
        return s._service_group_view(group)

    # ------------------------------------------------------------------
    # 启动整组
    # ------------------------------------------------------------------
    def start_group(self, name: str) -> dict:
        res = self._sched.start_service_group(name)
        if not res.get("ok"):
            raise HTTPException(
                status_code=404 if res.get("reason") == "not_found" else 400,
                detail=res.get("errors") and "；".join(res["errors"]) or "启动失败")
        audit.audit("service_group.start", obj=name)
        return res

    # ------------------------------------------------------------------
    # 停止整组
    # ------------------------------------------------------------------
    def stop_group(self, name: str) -> dict:
        res = self._sched.stop_service_group(name)
        if not res.get("ok"):
            raise HTTPException(
                status_code=404 if res.get("reason") == "not_found" else 400,
                detail=res.get("errors") and "；".join(res["errors"]) or "停止失败")
        audit.audit("service_group.stop", obj=name)
        return res
