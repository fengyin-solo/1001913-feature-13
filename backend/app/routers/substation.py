"""升压站接口：维护升压站、负荷越限判定、判定口径版本与越限留档。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.substation import NOT_JUDGED, OVERLOAD_STATUS, service

router = APIRouter(prefix="/api/substation", tags=["升压站"])


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按站区编号检索"),
    status: str | None = Query(default=None, description="待检修、运行正常、负荷越限、已停运"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """升压站台账：同一站区重复登记只保留最近一次结论；卡片统计与台账同口径。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size, stats=service.stats())


@router.get("/criteria")
def list_criteria() -> dict[str, Any]:
    """读取负荷率判定口径的全部历史版本及当前生效版本。"""
    criteria = service.list_criteria()
    active = next((item for item in criteria if item.get("active")), None)
    return {"items": criteria, "active": active}


@router.post("/criteria", response_model=ActionResult)
def add_criteria(payload: EntryPayload) -> ActionResult:
    """登记并生效一版新口径；现有升压站记录自动按新版重算，历史越限结论留档。"""
    criteria, message = service.add_criteria(payload.values)
    if criteria is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=criteria)


@router.post("/criteria/{version}/activate", response_model=ActionResult)
def activate_criteria(version: str) -> ActionResult:
    """切换生效口径版本；切换后按该版重算，历史留档不变。"""
    criteria, message = service.activate_criteria(version)
    if criteria is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=criteria)


@router.get("/verdicts")
def list_verdicts(
    station: str | None = Query(default=None, description="按站区编号筛选留档"),
) -> dict[str, Any]:
    """越限留档：每条历史结论都带着当时那一版口径，只读不改。"""
    items = service.list_verdicts(station)
    return {"total": len(items), "items": items}


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出升压站台账：同站区只保留最近一次结论，并附上判定用的负荷与容量。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "substation", "total": total, "stats": service.stats(), "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条升压站明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"升压站 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条升压站，缺字段时说明原因而不是静默丢弃；登记后按生效口径自动判定。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    message = "升压站已登记"
    if entry.get("越限标记") == OVERLOAD_STATUS:
        message = f"升压站已登记，判定为负荷越限（{entry.get('越限类型')}）"
    elif entry.get("越限标记") == NOT_JUDGED:
        message = "升压站已登记：当前负荷缺失，负荷率无法计算，暂不判定"
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条升压站执行确认检修、负荷判定、停运升压站；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    action_values = {key: value for key, value in payload.values.items() if key != "action"}
    entry, message = service.run_action(entry_id, action, action_values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
