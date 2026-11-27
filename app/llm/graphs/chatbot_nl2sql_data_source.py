"""
智能客服 data_query：在回答末尾追加通俗「数据来源」说明。

事实来自 settle_prepare + 已执行 SQL 的结构化字段，不交给分析模型自由发挥。
正文禁止内部表名与 SQL；仅输出业务可读表述。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

_TABLE_REF_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+([`\"\[]?[a-zA-Z_][\w\.]*[`\"\]]?)",
    re.IGNORECASE,
)

# 运维中间库：物理表 → 业务名（不展开 schema）
_OM_TABLE_DISPLAY: dict[str, str] = {
    "t_om_device_online_by_type": "仪器在线率统计",
    "t_om_complete_rate_by_type": "数据完整率统计",
    "t_om_effective_rate_by_type": "数据有效率统计",
    "t_om_data_alarm_summary": "数据告警总览",
    "t_om_device_alarm_list": "告警设备列表",
    "t_om_offline_device_list": "离线设备列表",
}

_STATION_TABLE_DISPLAY = "站点行政区对照"


@dataclass
class Nl2sqlDataSourceFacts:
    """供 ``build_data_source_section`` 使用的硬事实（可序列化进 stream plan）。"""

    ops_routed: bool = False
    library_id: str | None = None
    library_display_name: str | None = None
    library_table: str | None = None
    library_source: str | None = None  # parsed | default | none | ambiguous
    district: str | None = None
    station_name: str | None = None
    station_id: str | None = None
    grain: str | None = None
    time_tag: str | None = None
    time_start: str | None = None
    time_end: str | None = None
    time_is_latest: bool = False
    period_deterministic: str | None = None  # hit | miss | fail_fallback_llm | skipped_ops
    period_eligible: bool = False
    sql: str = ""
    user_query: str = ""
    extra_tables: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> Nl2sqlDataSourceFacts | None:
        if not isinstance(raw, dict):
            return None
        extras = raw.get("extra_tables")
        extra_list = [str(x) for x in extras if str(x).strip()] if isinstance(extras, list) else []
        return cls(
            ops_routed=bool(raw.get("ops_routed")),
            library_id=(str(raw.get("library_id") or "").strip() or None),
            library_display_name=(str(raw.get("library_display_name") or "").strip() or None),
            library_table=(str(raw.get("library_table") or "").strip() or None),
            library_source=(str(raw.get("library_source") or "").strip() or None),
            district=(str(raw.get("district") or "").strip() or None),
            station_name=(str(raw.get("station_name") or "").strip() or None),
            station_id=(str(raw.get("station_id") or "").strip() or None),
            grain=(str(raw.get("grain") or "").strip() or None),
            time_tag=(str(raw.get("time_tag") or "").strip() or None),
            time_start=(str(raw.get("time_start") or "").strip() or None),
            time_end=(str(raw.get("time_end") or "").strip() or None),
            time_is_latest=bool(raw.get("time_is_latest")),
            period_deterministic=(str(raw.get("period_deterministic") or "").strip() or None),
            period_eligible=bool(raw.get("period_eligible")),
            sql=str(raw.get("sql") or ""),
            user_query=str(raw.get("user_query") or ""),
            extra_tables=extra_list,
        )


def data_source_facts_from_prepare(
    prepare: Any,
    *,
    sql: str = "",
    period_deterministic: str | None = None,
    user_query: str = "",
) -> Nl2sqlDataSourceFacts:
    """从 ChatbotSettlePrepareResult 抽取事实；非地降/无 prepare 时返回尽量空的 facts。"""
    cs = getattr(prepare, "confirmed_scope", None) or {}
    if not isinstance(cs, dict):
        cs = {}
    library = getattr(prepare, "library", None)
    scope = getattr(prepare, "scope", None)
    grain = None
    time_tag = None
    time_start = None
    time_end = None
    time_is_latest = False
    if scope is not None:
        grain = str(getattr(scope, "grain", "") or "").strip() or None
        tw = getattr(scope, "time_snapshot", None) or {}
        if isinstance(tw, dict):
            time_tag = str(tw.get("time_window_tag") or "").strip() or None
            stat = tw.get("statistical_time_range")
            if isinstance(stat, dict):
                time_start = str(stat.get("start") or "").strip() or None
                time_end = str(stat.get("end") or "").strip() or None
            annual = tw.get("annual_window") or getattr(scope, "annual_window", None)
            if isinstance(annual, dict) and (not time_start or not time_end):
                time_start = time_start or (str(annual.get("start") or "").strip() or None)
                time_end = time_end or (str(annual.get("end") or "").strip() or None)
                if not time_tag:
                    time_tag = str(annual.get("tag") or "").strip() or None
        try:
            from app.nl2sql.latest_per_station import is_latest_per_station_question

            q = user_query or str(getattr(prepare, "original_query") or "")
            if is_latest_per_station_question(q) or (
                time_tag and "latest" in time_tag.lower()
            ):
                time_is_latest = True
        except Exception:  # noqa: BLE001
            if time_tag and "latest" in str(time_tag).lower():
                time_is_latest = True

    display_name = None
    lib_table = None
    lib_id = getattr(prepare, "library_id", None)
    if library is not None:
        display_name = str(getattr(library, "display_name", "") or "").strip() or None
        lib_table = str(getattr(library, "table", "") or "").strip() or None
        if not lib_id:
            lib_id = str(getattr(library, "id", "") or "").strip() or None

    station = str(cs.get("station_name") or cs.get("station_id") or "").strip() or None
    station_id = str(cs.get("station_id") or "").strip() or None
    if station_id and station and station_id == station:
        station_id = None

    return Nl2sqlDataSourceFacts(
        ops_routed=bool(getattr(prepare, "ops_routed", False)),
        library_id=str(lib_id).strip() if lib_id else None,
        library_display_name=display_name,
        library_table=lib_table,
        library_source=str(getattr(prepare, "library_source", "") or "").strip() or None,
        district=str(cs.get("district") or "").strip() or None,
        station_name=station,
        station_id=station_id,
        grain=grain,
        time_tag=time_tag,
        time_start=time_start,
        time_end=time_end,
        time_is_latest=time_is_latest,
        period_deterministic=(period_deterministic or None),
        period_eligible=bool(getattr(prepare, "period_eligible", False)),
        sql=(sql or "").strip(),
        user_query=(user_query or str(getattr(prepare, "original_query") or "")).strip(),
    )


def _canonical_table(name: str) -> str:
    t = (name or "").strip().strip("`").strip('"').strip("[]")
    if "." in t:
        t = t.split(".")[-1]
    return t.lower()


def _extract_physical_tables(sql: str) -> list[str]:
    raw = (sql or "").strip()
    if not raw:
        return []
    try:
        from app.nl2sql.validator import SQLValidator

        cte = {x.lower() for x in SQLValidator.extract_cte_names(raw)}
    except Exception:  # noqa: BLE001
        cte = set()
    out: list[str] = []
    seen: set[str] = set()
    for m in _TABLE_REF_RE.finditer(raw):
        name = _canonical_table(m.group(1))
        if not name or name in cte or name in seen:
            continue
        # 跳过明显子查询占位
        if name.startswith("("):
            continue
        seen.add(name)
        out.append(name)
    return out


def _library_table_display_map() -> dict[str, str]:
    """物理沉降表 → 业务展示名。"""
    mapping: dict[str, str] = {"t_station": _STATION_TABLE_DISPLAY}
    try:
        from app.data_query_agent.catalog import get_library_catalog

        cat = get_library_catalog()
        for lib in getattr(cat, "libraries", None) or []:
            table = _canonical_table(str(getattr(lib, "table", "") or ""))
            name = str(getattr(lib, "display_name", "") or "").strip()
            if table and name:
                mapping[table] = f"{name}监测数据"
    except Exception as exc:  # noqa: BLE001
        logger.debug("data_source library catalog unavailable: %s", exc)
        # 兜底七库
        mapping.update(
            {
                "t_data_wash_fcb": "分层标监测数据",
                "t_data_wash_jyb": "基岩标监测数据",
                "t_data_wash_dxswj": "地下水监测数据",
                "t_data_wash_kxsylj": "孔隙水压力监测数据",
                "t_data_wash_gnss": "GNSS监测数据",
                "t_data_wash_qxz": "气象监测数据",
                "t_data_wash_gq": "光纤监测数据",
            }
        )
    mapping.update({k: v for k, v in _OM_TABLE_DISPLAY.items()})
    return mapping


def _display_tables(facts: Nl2sqlDataSourceFacts) -> list[str]:
    mapping = _library_table_display_map()
    labels: list[str] = []
    seen: set[str] = set()

    def _add(label: str) -> None:
        t = (label or "").strip()
        if not t or t in seen:
            return
        seen.add(t)
        labels.append(t)

    if facts.library_display_name:
        label = f"{facts.library_display_name}监测数据"
        if facts.library_source == "default":
            label = f"{label}（问句未指定监测类型时的默认库）"
        _add(label)
    for table in _extract_physical_tables(facts.sql):
        if facts.library_table and table == _canonical_table(facts.library_table):
            continue
        disp = mapping.get(table)
        if disp:
            _add(disp)
        # 未知物理表不展示技术名
    for extra in facts.extra_tables:
        _add(extra)
    # 无库名但有 SQL 沉降表时，用 SQL 映射补主表
    if not labels:
        for table in _extract_physical_tables(facts.sql):
            disp = mapping.get(table)
            if disp and disp != _STATION_TABLE_DISPLAY:
                _add(disp)
                break
    # JOIN 站点表时补充说明
    phys = set(_extract_physical_tables(facts.sql))
    if "t_station" in phys and _STATION_TABLE_DISPLAY not in seen:
        if labels:
            labels[0] = f"{labels[0]}（关联{_STATION_TABLE_DISPLAY}）"
        else:
            _add(_STATION_TABLE_DISPLAY)
    return labels


def _caliber_line(facts: Nl2sqlDataSourceFacts) -> str:
    if facts.ops_routed:
        return "运维监测（最新批次）"
    try:
        from app.llm.graphs.chatbot_business_profile import get_chatbot_business_profile

        if get_chatbot_business_profile().domain == "subsidence":
            from app.nl2sql.schema_routing import settlement_data_source_label_zh

            return settlement_data_source_label_zh()
    except Exception:  # noqa: BLE001
        pass
    return ""


def _filter_lines(facts: Nl2sqlDataSourceFacts) -> list[str]:
    parts: list[str] = []
    if facts.district:
        parts.append(f"行政区为{facts.district}")
    station = facts.station_name or facts.station_id
    if station:
        parts.append(f"监测站点为{station}")
    if facts.ops_routed:
        parts.append("取该表最新一批快照")
        if not facts.district and not station:
            parts.append("未限定单一行政区")
    elif facts.time_is_latest:
        parts.append("取各监测设备当前最新一条观测")
    elif facts.time_start and facts.time_end:
        start = facts.time_start
        end = facts.time_end
        # 缩短到日期即可时去掉时分秒噪声
        if " " in start:
            start = start.split(" ")[0]
        if " " in end:
            end = end.split(" ")[0]
        parts.append(f"时间范围为 {start} 至 {end}")
    return parts


def _logic_line(facts: Nl2sqlDataSourceFacts) -> str:
    sql_l = (facts.sql or "").lower()
    grain = (facts.grain or "station").strip().lower()
    is_period = (
        facts.period_deterministic == "hit"
        or facts.period_eligible
        or "period_settle" in sql_l
        or "period_" in sql_l
    )
    is_fcb = (facts.library_id or "").lower() == "fcb" or (
        facts.library_table or ""
    ).lower() == "t_data_wash_fcb"

    if facts.ops_routed:
        return "按仪器类型汇总当前在线或告警等情况，取最新一批运维快照；不涉及沉降量计算"

    chunks: list[str] = []
    if is_period and not facts.time_is_latest:
        chunks.append("按该时间窗计算周期变化（窗末观测减窗初观测）")
        if is_fcb:
            chunks.append("分层标仅使用层位 0 代表标")
    elif facts.time_is_latest or "distinct on" in sql_l:
        chunks.append("各监测设备取最新一条观测")
    else:
        chunks.append("按问句条件筛选监测观测记录")

    if grain == "district":
        chunks.append("结果按行政区汇总列出")
    elif grain == "city":
        chunks.append("结果按全市汇总")
    else:
        chunks.append("结果按监测设备列出")
    return "；".join(chunks)


def build_data_source_section(facts: Nl2sqlDataSourceFacts | None) -> str:
    """
    生成通俗「数据来源」Markdown 段。

    无足够事实（无库、无表、无 SQL、无运维标记）时返回空串。
    """
    if facts is None:
        return ""
    tables = _display_tables(facts)
    caliber = _caliber_line(facts)
    filters = _filter_lines(facts)
    # 至少要有数据表、筛选、库或运维标记；仅有全局口径不够（避免空壳段）
    if not tables and not filters and not facts.ops_routed and not facts.library_display_name:
        return ""

    lines: list[str] = ["**数据来源**", ""]
    if caliber:
        lines.append(f"- **数据口径**：{caliber}")
    if tables:
        lines.append(f"- **数据表**：{'；'.join(tables)}")
    if filters:
        lines.append(f"- **筛选条件**：{'；'.join(filters)}")
    logic = _logic_line(facts)
    if logic:
        lines.append(f"- **查询逻辑**：{logic}")
    return "\n".join(lines).strip()


def append_data_source_section(answer: str, facts: Nl2sqlDataSourceFacts | None) -> str:
    """在回答正文末尾追加数据来源；已存在同名板块则不重复追加。"""
    section = build_data_source_section(facts)
    if not section:
        return (answer or "").strip()
    body = (answer or "").rstrip()
    if "**数据来源**" in body:
        return body
    if not body:
        return section
    return f"{body}\n\n{section}"
