"""升压站业务规则：按主变容量口径自动判定负荷越限。

口径要点（v2 起生效，v1 为人工判定的历史口径）：
- 负荷率 = 当前负荷 / 主变容量；
- 负荷率超过口径设定的上限即判为负荷越限；
- 持续时长不超过口径设定的短时阈值记为「短时冲击」，否则为「持续越限」；
- 主变容量或当前负荷缺失、无法解析时不允许判定；
- 口径调整后既有记录按新口径重算，重算前的结论整体存入判定留档，只追加不改写。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.store import store

MODULE = "substation"
REQUIRED_FIELDS = ["站区编号", "主变容量", "电压等级"]
LIST_FIELDS = [
    "站区编号", "主变容量", "电压等级", "所属场站", "上次检修日", "值班班组",
    "当前负荷", "负荷率", "越限分类", "判定口径版本", "登记时间",
]
STATUS_ORDER = ["待检修", "运行正常", "负荷越限", "已停运"]
ACTION_RULES = {"确认检修": "运行正常", "停运升压站": "已停运"}
OVERLOAD_STATUS = "负荷越限"
STOPPED_STATUS = "已停运"
PENDING_STATUS = "待检修"
NORMAL_STATUS = "运行正常"

# 判定口径按版本留档：v1 是人工判定的历史口径，v2 为当前生效口径。
CALIBER_VERSIONS: list[dict[str, Any]] = [
    {
        "version": "v1",
        "effective_since": "2026-09-01",
        "basis": "人工判定（无统一负荷率口径）",
        "load_rate_upper_limit": None,
        "short_term_minutes": None,
        "active": False,
    },
    {
        "version": "v2",
        "effective_since": "2026-10-01",
        "basis": "负荷率=当前负荷/主变容量，超过上限自动越限",
        "load_rate_upper_limit": 0.80,
        "short_term_minutes": 15,
        "active": True,
    },
]
TODAY = "2026-10-01"


def _to_float(value: Any) -> float | None:
    """把容量、负荷这类字段解析成数值；空值或无法解析时返回 None（不允许判定）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("%", "").replace("，", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _latest_per_station(rows: list[dict[str, Any]]) -> set[int]:
    """同一站区重复登记时，只保留登记时间最近（id 最大）的一条作为现行结论。"""
    latest: dict[str, int] = {}
    for index, row in enumerate(rows):
        key = str(row.get("站区编号") or f"__id_{row.get('id')}")
        if key not in latest or int(row.get("id", 0)) > int(rows[latest[key]].get("id", 0)):
            latest[key] = index
    return set(latest.values())


class SubstationService:
    def __init__(self) -> None:
        self._bootstrapped = False

    # ---------- 口径 ----------

    def caliber(self) -> dict[str, Any]:
        active = next(item for item in CALIBER_VERSIONS if item["active"])
        return {
            "active_version": active["version"],
            "effective_since": active["effective_since"],
            "basis": active["basis"],
            "load_rate_upper_limit": active["load_rate_upper_limit"],
            "short_term_minutes": active["short_term_minutes"],
            "history": [dict(item) for item in CALIBER_VERSIONS],
        }

    def adjust_caliber(
        self,
        *,
        load_rate_upper_limit: Any,
        short_term_minutes: Any,
        basis: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """调整判定口径：生成新版本并按新口径重算全部既有记录。"""
        limit = _to_float(load_rate_upper_limit)
        if limit is None or not 0 < limit <= 2:
            return None, "负荷率上限应为 (0, 2] 之间的数值，例如 0.8"
        try:
            short_threshold = int(short_term_minutes)
        except (TypeError, ValueError):
            return None, "短时冲击时长阈值应为正整数分钟"
        if short_threshold <= 0:
            return None, "短时冲击时长阈值应为正整数分钟"

        for item in CALIBER_VERSIONS:
            item["active"] = False
        version = f"v{len(CALIBER_VERSIONS) + 1}"
        CALIBER_VERSIONS.append({
            "version": version,
            "effective_since": TODAY,
            "basis": (basis or "按主变容量口径自动判定负荷越限").strip(),
            "load_rate_upper_limit": round(limit, 4),
            "short_term_minutes": short_threshold,
            "active": True,
        })
        summary = self.recalculate_all(trigger="口径调整")
        summary["caliber"] = self.caliber()
        return summary, f"口径已升级至 {version}，{summary['recalculated']} 条记录按新口径重算"

    # ---------- 判定 ----------

    def _judge(self, entry: dict[str, Any], caliber: dict[str, Any] | None = None) -> dict[str, Any]:
        """按当前口径对单条记录判定，返回判定结论；数据缺失时 blocked 为 True。"""
        caliber = caliber or self.caliber()
        capacity = _to_float(entry.get("主变容量"))
        load = _to_float(entry.get("当前负荷"))
        missing = []
        if capacity is None:
            missing.append("主变容量")
        if load is None:
            missing.append("当前负荷")
        if missing:
            return {"blocked": True, "reason": f"{'、'.join(missing)}缺失或无法解析，不允许判定", "missing": missing}
        if capacity <= 0:
            return {"blocked": True, "reason": "主变容量必须大于 0", "missing": ["主变容量"]}

        rate = load / capacity
        limit = float(caliber["load_rate_upper_limit"])
        exceeded = rate > limit
        duration = _to_float(entry.get("持续时长(分钟)"))
        category: str | None = None
        if exceeded:
            category = (
                "短时冲击"
                if duration is not None and duration <= float(caliber["short_term_minutes"])
                else "持续越限"
            )
        return {
            "blocked": False,
            "capacity": capacity,
            "load": load,
            "load_rate": round(rate, 4),
            "load_rate_percent": f"{rate * 100:.1f}%",
            "upper_limit": limit,
            "exceeded": exceeded,
            "category": category,
            "duration_minutes": duration,
            "version": caliber["active_version"],
        }

    def _apply_judgment(self, entry: dict[str, Any], result: dict[str, Any]) -> None:
        """把判定结论写回记录：越限标记与站区状态保持同一口径。"""
        if result.get("blocked"):
            entry["负荷率"] = None
            entry["越限标记"] = False
            entry["越限分类"] = None
            entry["判定口径版本"] = None
            entry["越限结论"] = None
            entry["判定说明"] = result["reason"]
            # 无法判定时不允许挂着负荷越限状态；停运站保持停运。
            if entry.get("status") == OVERLOAD_STATUS:
                entry["status"] = PENDING_STATUS
            entry["abnormal"] = False
            entry["pending"] = entry.get("status") != STOPPED_STATUS
            return

        entry["主变容量"] = result["capacity"]
        entry["当前负荷"] = result["load"]
        entry["负荷率"] = result["load_rate_percent"]
        entry["判定口径版本"] = result["version"]
        entry["判定说明"] = ""
        entry["越限结论"] = bool(result["exceeded"])
        entry["越限分类"] = result["category"]
        entry["越限标记"] = bool(result["exceeded"])
        if entry.get("status") != STOPPED_STATUS:
            entry["status"] = OVERLOAD_STATUS if result["exceeded"] else NORMAL_STATUS
        entry["abnormal"] = entry["status"] == OVERLOAD_STATUS
        entry["pending"] = entry["status"] != STOPPED_STATUS

    def _archive_current(self, entry: dict[str, Any], reason: str, version_before: str) -> None:
        """把当前结论快照追加进判定留档；历史留档只追加，不按新口径改写。"""
        archives = entry.setdefault("判定留档", [])
        archives.append({
            "archived_at": TODAY,
            "reason": reason,
            "口径版本": version_before,
            "站区状态": entry.get("status"),
            "越限结论": entry.get("越限结论"),
            "越限分类": entry.get("越限分类"),
            "判定用负荷": entry.get("当前负荷"),
            "判定用容量": entry.get("主变容量"),
            "负荷率": entry.get("负荷率"),
        })

    def _judge_and_archive(self, entry: dict[str, Any], *, reason: str, caliber: dict[str, Any]) -> bool:
        """重算并归档旧结论；返回该条是否完成判定（False 表示数据缺失被拦下）。"""
        self._archive_current(entry, reason, str(entry.get("判定口径版本") or "v1"))
        result = self._judge(entry, caliber)
        self._apply_judgment(entry, result)
        return not result.get("blocked", False)

    def bootstrap(self) -> None:
        """服务首次就绪时：把人工口径（v1）下的既有记录按现行口径重算一遍。

        旧结论先整体留档，再按新口径给出新结论；幂等，重复调用不会重复归档。
        """
        if self._bootstrapped:
            return
        caliber = self.caliber()
        for entry in store.rows(MODULE):
            if entry.get("_migrated"):
                continue
            if entry.get("越限结论") is not None:
                self._archive_current(entry, f"{caliber['active_version']} 口径上线，历史人工结论留档", "v1")
            self._apply_judgment(entry, self._judge(entry, caliber))
            entry["_migrated"] = True
        self._bootstrapped = True

    def recalculate_all(self, *, trigger: str = "口径调整") -> dict[str, Any]:
        """按当前口径重算全部记录；历史结论先留档再覆盖为新结论。"""
        caliber = self.caliber()
        rows = store.rows(MODULE)
        blocked = 0
        for entry in rows:
            judged = self._judge_and_archive(
                entry,
                reason=f"{trigger}：按 {caliber['active_version']} 重算前留档",
                caliber=caliber,
            )
            if not judged:
                blocked += 1
        _, stats = self._dedup_view(rows)
        return {
            "recalculated": len(rows),
            "blocked": blocked,
            "overload_stations": stats["overload_stations"],
            "short_term": stats["short_term"],
            "sustained": stats["sustained"],
        }

    # ---------- 台账与列表 ----------

    def _dedup_view(
        self, rows: list[dict[str, Any]], *, keyword: str | None = None, status: str | None = None
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """列表页口径：同一站区重复登记只保留最近一次结论，并留下判定用负荷与容量。"""
        keep = _latest_per_station(rows)
        view: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            if index not in keep:
                continue
            if keyword and keyword not in str(row.get("站区编号", "")):
                continue
            if status and row.get("status") != status:
                continue
            item = {field: row.get(field) for field in LIST_FIELDS}
            item["id"] = row.get("id")
            item["升压站状态"] = row.get("status")
            item["越限标记"] = bool(row.get("越限标记"))
            # 判定依据随列表一起留下：负荷与容量都在，结论才可复算。
            item["判定用负荷"] = row.get("当前负荷")
            item["判定用容量"] = row.get("主变容量")
            item["判定说明"] = row.get("判定说明") or ""
            item["判定留档"] = row.get("判定留档", [])
            view.append(item)
        view.sort(key=lambda item: int(item.get("id") or 0), reverse=True)
        return view, self.overload_stats(view)

    def overload_stats(self, view: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """越限统计以去重后的站区视图为准，保证概览与台账看到的是同一个数。"""
        if view is None:
            view, _ = self._dedup_view(store.rows(MODULE))
        overload = [item for item in view if item.get("越限标记")]
        rates = []
        for item in view:
            if item.get("升压站状态") == STOPPED_STATUS:
                continue
            percent = _to_float(str(item.get("负荷率") or "").rstrip("%"))
            if percent is not None:
                rates.append(percent / 100)
        return {
            "total_stations": len(view),
            "in_service": sum(1 for item in view if item.get("升压站状态") != STOPPED_STATUS),
            "overload_stations": len(overload),
            "short_term": sum(1 for item in overload if item.get("越限分类") == "短时冲击"),
            "sustained": sum(1 for item in overload if item.get("越限分类") == "持续越限"),
            "avg_load_rate": round(sum(rates) / len(rates), 4) if rates else None,
        }

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
        view, stats = self._dedup_view(store.rows(MODULE), keyword=keyword, status=status)
        total = len(view)
        start = max(page - 1, 0) * size
        return view[start:start + size], total, stats

    def ledger_entries(self) -> list[dict[str, Any]]:
        """完整台账：含重复登记与判定留档，供导出与留档核对。"""
        return [deepcopy(row) for row in store.rows(MODULE)]

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        if _to_float(values.get("主变容量")) is None:
            return None, ["主变容量(需为数值，单位 MVA)"]
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in REQUIRED_FIELDS + ["所属场站", "上次检修日", "值班班组", "当前负荷", "持续时长(分钟)"]:
            if values.get(field) is not None:
                entry[field] = values.get(field)
        entry["主变容量"] = _to_float(entry.get("主变容量"))
        if values.get("当前负荷") is not None:
            entry["当前负荷"] = _to_float(values.get("当前负荷"))
        if values.get("持续时长(分钟)") is not None:
            entry["持续时长(分钟)"] = _to_float(values.get("持续时长(分钟)"))
        entry["登记时间"] = str(values.get("登记时间") or TODAY)
        entry["status"] = PENDING_STATUS
        entry["pending"] = True
        entry["abnormal"] = False
        entry["越限标记"] = False
        entry["越限结论"] = None
        entry["越限分类"] = None
        entry["负荷率"] = None
        entry["判定口径版本"] = None
        entry["判定说明"] = ""
        entry["判定留档"] = []
        entry["_migrated"] = True
        result = self._judge(entry)
        if result["blocked"]:
            # 缺负荷是允许登记的，只是不允许判定；结论字段先留空，上报负荷后再判定。
            entry["判定说明"] = result["reason"] if _to_float(entry.get("当前负荷")) is None else ""
        else:
            # 先把判定口径下的负荷率、结论算好，但站区未确认检修前维持待检修、不打越限标记。
            entry["主变容量"] = result["capacity"]
            entry["当前负荷"] = result["load"]
            entry["负荷率"] = result["load_rate_percent"]
            entry["判定口径版本"] = result["version"]
            entry["越限结论"] = result["exceeded"]
            entry["越限分类"] = result["category"]
        rows.append(entry)
        return entry, []

    def report_load(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """上报当前负荷（及本次持续时长）：按现行口径自动判定，替代人工登记越限。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"升压站 {entry_id} 不存在或已归档"
        if entry.get("status") == STOPPED_STATUS:
            return None, "升压站已停运，不再参与负荷越限判定"
        if values.get("当前负荷") is not None:
            entry["当前负荷"] = values.get("当前负荷")
        if values.get("持续时长(分钟)") is not None:
            entry["持续时长(分钟)"] = values.get("持续时长(分钟)")
        result = self._judge(entry)
        if result.get("blocked"):
            self._apply_judgment(entry, result)
            return None, result["reason"]
        self._apply_judgment(entry, result)
        if result["exceeded"]:
            return entry, f"自动判定负荷越限（{result['category']}，负荷率 {result['load_rate_percent']}）"
        return entry, f"自动判定负荷正常（负荷率 {result['load_rate_percent']}）"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"升压站 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于升压站可执行范围"
        target = ACTION_RULES[action]
        if target == NORMAL_STATUS:
            result = self._judge(entry)
            if result.get("blocked"):
                return None, f"无法确认运行：{result['reason']}"
            self._apply_judgment(entry, result)
            if result["exceeded"]:
                return entry, (
                    f"当前负荷率 {result['load_rate_percent']} 已越限（{result['category']}），"
                    "站区维持负荷越限状态"
                )
            entry["status"] = NORMAL_STATUS
        else:
            entry["status"] = target
        # 越限标记始终与站区状态口径一致。
        entry["越限标记"] = entry["status"] == OVERLOAD_STATUS
        entry["abnormal"] = entry["越限标记"]
        entry["pending"] = entry["status"] != STOPPED_STATUS
        return entry, f"升压站已{action}"


service = SubstationService()
# 模块加载即完成一次历史口径迁移：store 在此之前已构建好种子台账，
# 且 bootstrap 幂等，测试/服务两种启动方式看到的结论一致。
service.bootstrap()
