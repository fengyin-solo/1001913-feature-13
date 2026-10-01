"""升压站业务规则：负荷率口径、越限判定、版本留档与站区台账。

口径（随版本演进，每次调整生成一个新版本）：
- 负荷率 = 当前负荷 / 主变容量
- 负荷率超过设定上限即标记「负荷越限」，并按越限持续时长区分「短时冲击」与「持续越限」
- 主变容量或当前负荷缺失（不可计算负荷率）的记录不允许判定

历史留档：每次越限结论连同当时生效的口径版本写入越限留档表，老版本结论不被覆盖；
口径调整后，现有升压站记录按新版本重算当前结论。

列表台账：同一站区编号重复登记只保留最近一次登记的结论，同时保留判定用的负荷与容量；
概览读到的越限数与该台账口径一致。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "substation"
CRITERIA_MODULE = "_substation_criteria"
ARCHIVE_MODULE = "_substation_verdicts"

REQUIRED_FIELDS = ["站区编号", "主变容量", "电压等级"]
OPTIONAL_FIELDS = ["所属场站", "上次检修日", "值班班组", "当前负荷", "越限持续分钟"]

STATUS_ORDER = ["待检修", "运行正常", "负荷越限", "已停运"]
OFFLINE_STATUS = "已停运"
OVERLOAD_STATUS = "负荷越限"
NORMAL_STATUS = "运行正常"

SHORT_OVERLOAD = "短时冲击"
PERSISTENT_OVERLOAD = "持续越限"
NOT_JUDGED = "不可判定"

# 动作里同时保留旧名称「登记负荷越限」作为「负荷判定」的别名，避免旧入口失效。
ACTION_ALIASES = {"登记负荷越限": "负荷判定"}
ACTION_RULES = {"确认检修": "确认检修", "负荷判定": "负荷判定", "停运升压站": "停运升压站"}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _number(value: Any) -> float | None:
    """把容量/负荷这类录入值解析成非负数；解析不出来或为负时返回 None（即缺失）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip().replace(",", "").rstrip("%")
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    return number if number >= 0 else None


class SubstationService:
    def __init__(self) -> None:
        self._sync_seed()

    # ---------- 口径版本 ----------

    def _sync_seed(self) -> None:
        """把种子数据对齐到当前生效口径：不写留档，只刷新现有记录的当前结论。

        内存仓库每次启动都会重建；留档表里预置的历史结论永远代表「当时那一版」，
        这里只负责让现有登记记录的当前结论按生效版本重算一遍。
        """
        criteria = self._criteria()
        active = next((item for item in criteria if item.get("active")), None)
        if active is None:
            return
        for row in store.rows(MODULE):
            self._judge_into(row, active, archive=False)

    def _criteria(self) -> list[dict[str, Any]]:
        return store.rows(CRITERIA_MODULE)

    def _active_criteria(self) -> dict[str, Any] | None:
        return next((item for item in self._criteria() if item.get("active")), None)

    def list_criteria(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._criteria()]

    def add_criteria(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """登记一版新口径并立即生效，现有升压站记录按新版重算并追加越限留档。"""
        limit = _number(values.get("负荷率上限"))
        if limit is None or limit <= 0:
            return None, "负荷率上限缺失或不是有效数值，请按小数填写（如 0.85）"
        if limit > 1:
            # 兼容 85 这种百分数写法，统一换算成 0.85
            limit = limit / 100
        minutes = _number(values.get("持续越限分钟"))
        if minutes is None or minutes < 0:
            minutes = 15.0
        existing = self._criteria()
        version = str(values.get("版本") or "").strip()
        if version:
            if any(str(item.get("版本")) == version for item in existing):
                return None, f"口径版本 {version} 已存在，不能重复登记"
        else:
            version = f"v{max((int(str(item.get('版本', 'v0')).lstrip('v') or 0) for item in existing), default=0) + 1}"
        criteria = {
            "版本": version,
            "负荷率上限": limit,
            "持续越限分钟": minutes,
            "生效时间": _now(),
            "active": True,
        }
        for item in existing:
            item["active"] = False
        existing.append(criteria)
        recalculated = self._recompute_all(criteria)
        return criteria, f"口径 {version} 已生效，{recalculated} 条升压站记录按新口径重算"

    def activate_criteria(self, version: str) -> tuple[dict[str, Any] | None, str]:
        """切换生效口径版本；切换后现有记录按该版重算，历史留档保持不变。"""
        criteria = next((item for item in self._criteria() if str(item.get("版本")) == version), None)
        if criteria is None:
            return None, f"口径版本 {version} 不存在"
        if criteria.get("active"):
            return criteria, f"口径 {version} 已是当前生效版本"
        for item in self._criteria():
            item["active"] = item is criteria
        recalculated = self._recompute_all(criteria)
        return criteria, f"已切换到口径 {version}，{recalculated} 条记录已按该版重算"

    def _recompute_all(self, criteria: dict[str, Any]) -> int:
        """按指定口径重算全部登记记录，新产生的越限结论追加到留档表。"""
        count = 0
        for row in store.rows(MODULE):
            before_mark = row.get("越限标记")
            before_version = row.get("判定版本")
            self._judge_into(row, criteria, archive=True)
            if row.get("越限标记") != before_mark or row.get("判定版本") != before_version:
                count += 1
        return count

    # ---------- 判定核心 ----------

    def _judge_into(
        self,
        row: dict[str, Any],
        criteria: dict[str, Any],
        *,
        archive: bool,
        trigger: str = "按口径重算",
    ) -> None:
        """按给定口径判定一条登记记录并写回字段；越限时追加留档。

        已停运站区状态口径不变，不参与自动越限标记。
        """
        was_offline = row.get("status") == OFFLINE_STATUS
        if was_offline:
            # 已停运站区状态口径不变，不参与自动越限标记，保留停运时的判定痕迹；
            # 负荷率仅作信息展示，不改变状态与越限结论
            row["判定版本"] = criteria.get("版本")
            row.setdefault("越限标记", None)
            row.setdefault("越限类型", None)
            capacity = _number(row.get("主变容量"))
            load = _number(row.get("当前负荷"))
            row["负荷率"] = round(load / capacity, 4) if capacity is not None and load is not None else None
            self._sync_display(row)
            return
        capacity = _number(row.get("主变容量"))
        load = _number(row.get("当前负荷"))
        duration = _number(row.get("越限持续分钟")) or 0.0

        row["判定版本"] = criteria.get("版本")
        if capacity is None or load is None or capacity <= 0:
            # 容量或负荷率（=当前负荷/主变容量）缺失，不允许判定
            row["负荷率"] = None
            row["越限标记"] = NOT_JUDGED
            row["越限类型"] = None
            # 不可判定不改变人工/业务状态，仅同步展示口径；默认停留在待检修
            row.setdefault("status", STATUS_ORDER[0])
            row["abnormal"] = False
            row["pending"] = row.get("status") != OFFLINE_STATUS
            self._sync_display(row)
            return

        ratio = load / capacity
        row["负荷率"] = round(ratio, 4)
        if ratio > float(criteria["负荷率上限"]):
            kind = PERSISTENT_OVERLOAD if duration >= float(criteria["持续越限分钟"]) else SHORT_OVERLOAD
            row["越限标记"] = OVERLOAD_STATUS
            row["越限类型"] = kind
            row["status"] = OVERLOAD_STATUS
            row["abnormal"] = True
            row["pending"] = True
            if archive:
                self._archive(row, criteria, ratio, kind, trigger=trigger)
        else:
            row["越限标记"] = NORMAL_STATUS
            row["越限类型"] = None
            if row.get("status") == OVERLOAD_STATUS:
                # 重算后不再越限，站区状态与越限口径保持一致
                row["status"] = NORMAL_STATUS
            row["abnormal"] = False
            row["pending"] = row.get("status") != OFFLINE_STATUS
        self._sync_display(row)

    @staticmethod
    def _sync_display(row: dict[str, Any]) -> None:
        """列表展示用的「升压站状态」与 status 字段保持同一口径（负荷率字段即判定算出的比例）。"""
        row["升压站状态"] = row.get("status")

    def _archive(
        self,
        row: dict[str, Any],
        criteria: dict[str, Any],
        ratio: float,
        kind: str,
        *,
        trigger: str,
    ) -> None:
        archive_rows = store.rows(ARCHIVE_MODULE)
        entry = {
            "id": max((int(item.get("id", 0)) for item in archive_rows), default=0) + 1,
            "登记ID": row.get("id"),
            "站区编号": row.get("站区编号"),
            "判定版本": criteria.get("版本"),
            "负荷率上限": criteria.get("负荷率上限"),
            "持续越限分钟": criteria.get("持续越限分钟"),
            "主变容量": _number(row.get("主变容量")),
            "当前负荷": _number(row.get("当前负荷")),
            "负荷率": round(ratio, 4),
            "越限类型": kind,
            "越限结论": OVERLOAD_STATUS,
            "触发方式": trigger,
            "留档时间": _now(),
        }
        archive_rows.append(entry)

    def list_verdicts(self, station: str | None = None) -> list[dict[str, Any]]:
        """越限留档：历史结论按当时那一版留档，只读不改。"""
        rows = store.rows(ARCHIVE_MODULE)
        if station:
            rows = [row for row in rows if station in str(row.get("站区编号", ""))]
        return [dict(row) for row in rows]

    # ---------- 台账：同站区只留最近一次结论 ----------

    def ledger(self) -> list[dict[str, Any]]:
        """站区台账：同一站区编号重复登记时只保留最近一次（id 最大）的结论。

        判定用的当前负荷与主变容量随最近一次登记一起保留。
        """
        latest: dict[str, dict[str, Any]] = {}
        for row in store.rows(MODULE):
            code = str(row.get("站区编号", ""))
            current = latest.get(code)
            if current is None or int(row.get("id", 0)) >= int(current.get("id", 0)):
                latest[code] = row
        return [latest[code] for code in sorted(latest, key=lambda key: int(latest[key].get("id", 0)))]

    def stats(self) -> dict[str, int | float | None]:
        """台账统计：在运、越限（短时冲击/持续越限）、平均负荷率；与概览共用同一口径。"""
        ledger = self.ledger()
        in_service = [row for row in ledger if row.get("status") != OFFLINE_STATUS]
        overload = [row for row in ledger if row.get("越限标记") == OVERLOAD_STATUS]
        judged = [row for row in in_service if isinstance(row.get("负荷率"), (int, float))]
        avg_ratio = round(sum(float(row["负荷率"]) for row in judged) / len(judged), 4) if judged else None
        return {
            "在运升压站": len(in_service),
            "负荷越限站数": len(overload),
            "短时冲击站数": sum(1 for row in overload if row.get("越限类型") == SHORT_OVERLOAD),
            "持续越限站数": sum(1 for row in overload if row.get("越限类型") == PERSISTENT_OVERLOAD),
            "平均负荷率": avg_ratio,
        }

    def overload_count(self) -> int:
        """概览读取的越限数：直接数台账，保证和列表页一致。"""
        return int(self.stats()["负荷越限站数"])

    # ---------- 列表 / 明细 / 登记 / 动作 ----------

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self.ledger()
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("站区编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in OPTIONAL_FIELDS:
            if values.get(field) is not None and str(values.get(field)).strip() != "":
                entry[field] = values.get(field)
        # 重复登记同一站区编号是允许的：台账只保留最近一次，旧登记仍留在留档/历史里
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        active = self._active_criteria()
        if active is not None:
            self._judge_into(entry, active, archive=True, trigger="登记升压站")
        return entry, []

    def run_action(self, entry_id: int, action: str, values: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"升压站 {entry_id} 不存在或已归档"
        action = ACTION_ALIASES.get(action, action)
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于升压站可执行范围"
        active = self._active_criteria()

        if action == "停运升压站":
            entry["status"] = OFFLINE_STATUS
            entry["pending"] = False
            entry["abnormal"] = False
            entry["越限标记"] = None
            entry["越限类型"] = None
            self._sync_display(entry)
            return entry, "升压站已停运"

        if action == "确认检修":
            # 检修确认意味着重新投运：先退出停运口径，再按当前口径重判；
            # 仍越限时不允许标成正常
            if entry.get("status") == OFFLINE_STATUS:
                entry["status"] = STATUS_ORDER[0]
            if active is not None:
                if _number(entry.get("主变容量")) is None or _number(entry.get("当前负荷")) is None:
                    return None, "主变容量或当前负荷缺失，负荷率无法计算，不允许判定"
                self._judge_into(entry, active, archive=True, trigger="确认检修")
                if entry.get("越限标记") == OVERLOAD_STATUS:
                    return None, f"当前负荷率仍越限（{entry.get('越限类型')}），不能确认检修为正常"
            entry["status"] = NORMAL_STATUS
            entry["pending"] = True
            entry["abnormal"] = False
            self._sync_display(entry)
            return entry, "升压站已确认检修，判定为运行正常"

        # 负荷判定：可携带最新当前负荷/越限持续分钟后按生效口径判定
        values = values or {}
        for field in ("当前负荷", "越限持续分钟"):
            if values.get(field) is not None and str(values.get(field)).strip() != "":
                entry[field] = values.get(field)
        if active is None:
            return None, "尚未配置负荷率判定口径，无法判定"
        if _number(entry.get("主变容量")) is None or _number(entry.get("当前负荷")) is None:
            self._judge_into(entry, active, archive=False, trigger="负荷判定")
            return None, "主变容量或当前负荷缺失，负荷率无法计算，不允许判定"
        self._judge_into(entry, active, archive=True, trigger="负荷判定")
        if entry.get("越限标记") == OVERLOAD_STATUS:
            return entry, f"负荷越限（{entry.get('越限类型')}），已按口径 {active.get('版本')} 标记并留档"
        return entry, f"负荷率 {float(entry['负荷率']) * 100:.1f}%，未越限"


# 路由层与概览共用同一个实例：判定口径与台账口径始终一致
service = SubstationService()
