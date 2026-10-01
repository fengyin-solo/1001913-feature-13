"""升压站接口：登记站区、上报负荷自动判定越限、调整负荷率判定口径。

越限不再人工登记：负荷率=当前负荷/主变容量，超过口径上限自动标记，并区分
短时冲击与持续越限。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload
from app.services.substation import LIST_FIELDS, STATUS_ORDER, service

router = APIRouter(prefix="/api/substation", tags=["升压站"])


@router.get("/caliber")
def get_caliber() -> dict[str, Any]:
    """读取当前负荷率判定口径及历史版本。"""
    return service.caliber()


@router.post("/caliber/adjust", response_model=ActionResult)
def adjust_caliber(payload: EntryPayload) -> ActionResult:
    """调整负荷率上限/短时阈值：自动升版，历史越限结论留档，既有记录按新口径重算。"""
    values = payload.values
    if "load_rate_upper_limit" not in values or "short_term_minutes" not in values:
        return ActionResult(
            ok=False,
            message="请同时提供 load_rate_upper_limit（负荷率上限）与 short_term_minutes（短时阈值分钟）",
        )
    summary, message = service.adjust_caliber(
        load_rate_upper_limit=values.get("load_rate_upper_limit"),
        short_term_minutes=values.get("short_term_minutes"),
        basis=values.get("basis"),
    )
    if summary is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=summary)


@router.post("/recalculate", response_model=ActionResult)
def recalculate() -> ActionResult:
    """按当前口径手动重算全部升压站记录（重算前结论留档）。"""
    summary = service.recalculate_all(trigger="手动重算")
    return ActionResult(
        ok=True,
        message=(
            f"已重算 {summary['recalculated']} 条，"
            f"其中 {summary['blocked']} 条因容量或负荷缺失未判定；"
            f"当前越限站区 {summary['overload_stations']} 个"
        ),
        entry=summary,
    )


@router.get("")
def list_entries(
    keyword: str | None = Query(default=None, description="按站区编号检索"),
    status: str | None = Query(default=None, description="待检修、运行正常、负荷越限、已停运"),
    overload: str | None = Query(default=None, description="传 true 只看越限站区"),
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    """按站区编号与状态过滤升压站列表。

    同一站区重复登记只返回最近一次结论；返回体额外带 stats，越限站数与概览、
    台账使用同一口径。
    """
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    if status and status not in STATUS_ORDER:
        raise HTTPException(status_code=400, detail=f"状态仅支持：{'、'.join(STATUS_ORDER)}")
    effective_status = status
    if (overload or "").strip().lower() in {"true", "1", "yes"}:
        effective_status = "负荷越限"
    items, total, stats = service.list_entries(
        keyword=keyword, status=effective_status, page=page, size=size
    )
    return {
        "items": items,
        "total": total,
        "page": page,
        "size": size,
        "fields": LIST_FIELDS + ["升压站状态"],
        "stats": stats,
    }


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出升压站完整台账：含重复登记记录与逐次判定留档。"""
    ledger = service.ledger_entries()
    return {
        "module": "substation",
        "caliber_version": service.caliber()["active_version"],
        "total": len(ledger),
        "stats": service.overload_stats(),
        "items": ledger,
    }


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict[str, Any]:
    """读取单条升压站明细（含判定留档）；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"升压站 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条升压站；主变容量必须为数值，负荷缺失允许登记但不允许判定。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填或无法解析字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="升压站已登记", entry=entry)


@router.post("/{entry_id}/load", response_model=ActionResult)
def report_load(entry_id: int, payload: EntryPayload) -> ActionResult:
    """上报当前负荷与持续时长，系统按现行口径自动判定短时冲击/持续越限。"""
    entry, message = service.report_load(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条升压站执行确认检修、停运升压站；越限结论一律由系统自动判定。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
