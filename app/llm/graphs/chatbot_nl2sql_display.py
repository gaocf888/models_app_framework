"""智能客服 NL2SQL：用户可见列过滤（隐藏主键/技术 ID / 无信息量短码列）。"""

from __future__ import annotations

import re
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# 注入 NL2SQL 生成 prompt（仅客服 data_query；不写死业务枚举）
CHATBOT_NL2SQL_SELECT_DISPLAY_RULES = """
【智能客服·SELECT 用户可读性（本场景强制）】
1) JOIN / WHERE / ON 可使用 id、外键等技术列做关联与过滤。
2) SELECT 列表（面向最终用户展示）：
   - 用户未明确要求「编号/ID/主键」时，禁止在 SELECT 中输出无业务含义的主键、UUID、纯技术 id（如 id、*_id、uuid 等）；
   - 若 catalog 中同一实体同时存在名称类列（如 *_name、*_label、*名称、*标题）与码值列，优先 SELECT 名称类列，不要只选码值列；
   - 仅当无名称类列可用时，才允许 SELECT 码值/状态码列；若同时用 CASE 转中文别名，必须依据 catalog/字段注释中的枚举说明，禁止臆造码表含义。
3) 优先输出业务可读列：名称、时间、描述、类型名称等；列别名用简短中文便于展示。
""".strip()

# 区级对比/排行：与 app.nl2sql.district_period_agg 同源（节点 ③b sql_gen_extra_hint）
def _district_period_agg_hint() -> str:
    from app.nl2sql.district_period_agg import district_period_prompt_block

    return (
        "【智能客服·行政区周期沉降对比（本问强制）】\n"
        + district_period_prompt_block(include_skeleton=True)
    )


# 兼容历史 import：模块加载时生成全文（含骨架）
CHATBOT_NL2SQL_DISTRICT_PERIOD_AGG_HINT = _district_period_agg_hint()

_EXACT_HIDE_NAMES = frozenset(
    {
        "id",
        "uuid",
        "guid",
        "pk",
        "row_id",
        "record_id",
        "primary_key",
    }
)

_HIDE_NAME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^id$"),
    re.compile(r".+_id$"),
    re.compile(r"^id_.+"),
    re.compile(r".+_uuid$"),
    re.compile(r"^uuid_.+"),
    re.compile(r".+_guid$"),
    re.compile(r"^pk_.+"),
    re.compile(r".+_pk$"),
)

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_HEX32_RE = re.compile(r"^[0-9a-fA-F]{32}$")

# 短码：纯数字，或长度 1～2 的字母数字（如 0、C、A1）
_SHORT_CODE_RE = re.compile(r"^(?:\d+|[A-Za-z0-9]{1,2})$")

# 码维列名闸门（归一化后子串匹配）
_CODE_DIM_MARKERS: tuple[str, ...] = (
    "状态",
    "等级",
    "级别",
    "标志",
    "标识符",  # 少见；避免单独「标识」误伤
    "status",
    "state",
    "level",
    "grade",
    "flag",
    "enabled",
    "deleted",
    "valid",
    "category",
    "mode",
    # type/kind：仅后缀或独立词，见 is_code_dimension_column_name
)

_CODE_DIM_SUFFIXES: tuple[str, ...] = (
    "_status",
    "_state",
    "_level",
    "_grade",
    "_flag",
    "_type",
    "_kind",
    "_mode",
    "_category",
)

# 强制保留：量纲/时间/名称等，即使取值为数字也不因「短码」隐藏
_FORCE_KEEP_MARKERS: tuple[str, ...] = (
    "年份",
    "年度",
    "年月",
    "日期",
    "时间",
    "year",
    "date",
    "time",
    "名称",
    "name",
    "title",
    "label",
    "描述",
    "说明",
    "intro",
    "desc",
    "remark",
    "数量",
    "次数",
    "个数",
    "根数",
    "处数",
    "count",
    "qty",
    "amount",
    "金额",
    "价格",
    "费用",
    "容量",
    "负荷",
    "蒸发量",
    "温度",
    "压力",
    "厚度",
    "时长",
    "秒",
    "分钟",
    "小时",
    "capacity",
    "load",
    "temp",
    "press",
    "mw",
    "型号",
    "model",
    "厂家",
    "producer",
)


def _norm_col(name: str) -> str:
    return str(name or "").strip().lower().replace(" ", "").replace("　", "")


def is_force_keep_display_column(col_name: str) -> bool:
    """年份/日期/量纲/名称等强制保留，不因短码启发式隐藏。"""
    low = _norm_col(col_name)
    if not low:
        return False
    return any(m in low for m in _FORCE_KEEP_MARKERS)


def is_code_dimension_column_name(col_name: str) -> bool:
    """列名是否像状态/等级/类型等码维。"""
    low = _norm_col(col_name)
    if not low:
        return False
    if is_force_keep_display_column(col_name):
        return False
    # 「类型名称」带名称 → 强制保留已覆盖；裸「类型」才作码维
    if low in {"type", "kind", "mode", "category", "等级", "状态", "级别", "标志"}:
        return True
    if any(low.endswith(suf) for suf in _CODE_DIM_SUFFIXES):
        return True
    if any(m in low for m in _CODE_DIM_MARKERS):
        # 避免「状态说明」「等级名称」等描述类（含名称/说明已 force keep）
        return True
    if "类型" in low and "名称" not in low:
        return True
    if low.startswith("is_") or low.startswith("has_"):
        return True
    return False


def is_technical_id_column_name(col_name: str) -> bool:
    """按列名判断是否为应对最终用户隐藏的技术 ID 列。"""
    raw = str(col_name or "").strip()
    if not raw:
        return False
    low = _norm_col(raw)
    if low in _EXACT_HIDE_NAMES:
        return True
    for pat in _HIDE_NAME_PATTERNS:
        if pat.match(low):
            return True
    if low.endswith("计划id") or low.endswith("记录id") or low.endswith("主键"):
        return True
    if "主键" in low or "唯一标识" in low:
        return True
    if low.endswith("id") and any(tok in low for tok in ("计划", "记录", "标识")):
        return True
    return False


def _looks_like_uuid_or_hex_id(val: Any) -> bool:
    if val is None:
        return False
    s = str(val).strip()
    if not s:
        return False
    return bool(_UUID_RE.match(s) or _HEX32_RE.match(s))


def is_uuid_like_display_column(col_name: str, values: list[Any]) -> bool:
    """样本值几乎全是 UUID/32hex 时，也视为技术标识列。"""
    samples = [v for v in values if v is not None and str(v).strip() != ""]
    if len(samples) < 2:
        return False
    hits = sum(1 for v in samples if _looks_like_uuid_or_hex_id(v))
    return hits / len(samples) >= 0.8


def _looks_like_short_code(val: Any) -> bool:
    if val is None:
        return False
    if isinstance(val, bool):
        return True
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        # 整数/浮点整值视为数字码；保留超大年份已由列名白名单兜底
        try:
            if float(val) == int(val):
                return True
        except (TypeError, ValueError, OverflowError):
            return False
        return False
    s = str(val).strip()
    if not s:
        return False
    return bool(_SHORT_CODE_RE.match(s))


def is_opaque_short_code_column(col_name: str, values: list[Any]) -> bool:
    """
    码维列名 + 样本几乎全是短码（数字或单/双字符字母数字）→ 对用户无信息量，隐藏。
    """
    if not is_code_dimension_column_name(col_name):
        return False
    if is_force_keep_display_column(col_name):
        return False
    samples = [v for v in values if v is not None and str(v).strip() != ""]
    if len(samples) < 2:
        return False
    hits = sum(1 for v in samples if _looks_like_short_code(v))
    return hits / len(samples) >= 0.8


def should_hide_chatbot_nl2sql_column(col_name: str, sample_values: list[Any] | None = None) -> bool:
    if is_force_keep_display_column(col_name):
        # 仍允许藏技术 id：年份列不会匹配 id 规则；若误配 uuid 则强制保留优先
        if is_technical_id_column_name(col_name):
            return True
        return False
    if is_technical_id_column_name(col_name):
        return True
    samples = list(sample_values or [])
    if samples and is_uuid_like_display_column(col_name, samples):
        return True
    if samples and is_opaque_short_code_column(col_name, samples):
        return True
    return False


def filter_chatbot_nl2sql_display_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    过滤智能客服 NL2SQL 展示列（不改 SQL）：
    1) 技术 ID / UUID；
    2) 状态/等级等码维列且取值几乎全是无信息量短码。
    若过滤后无剩余列，则回退为原行，避免空表。
    """
    if not rows:
        return rows
    col_order: list[str] = []
    seen: set[str] = set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        for k in r.keys():
            sk = str(k)
            if sk not in seen:
                seen.add(sk)
                col_order.append(sk)
    if not col_order:
        return rows

    hide: set[str] = set()
    for col in col_order:
        samples = [r.get(col) for r in rows if isinstance(r, dict)]
        if should_hide_chatbot_nl2sql_column(col, samples):
            hide.add(col)

    keep = [c for c in col_order if c not in hide]
    if not keep:
        logger.info(
            "chatbot.nl2sql_display_filter all columns hidden; keep original cols=%s",
            col_order,
        )
        return rows

    if hide:
        logger.info(
            "chatbot.nl2sql_display_filter hidden_cols=%s kept_cols=%s",
            sorted(hide),
            keep,
        )

    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            out.append(r)  # type: ignore[arg-type]
            continue
        out.append({c: r.get(c) for c in keep})
    return out


# 英文字段 → 中文表头兜底（沉降 libraries + 运维 om_result 8 表列并集）
# 运维列对齐：docs/…/统计信息中间库交付使用说明.md
_FALLBACK_COLUMN_TITLES: dict[str, str] = {
    # —— 沉降 wash ——
    "period_settle_mm": "周期沉降变化(mm，负沉正弹)",
    "period_settle": "周期沉降变化(mm，负沉正弹)",
    "annual_settle_mm": "年沉降量(mm，负沉正弹)",
    "annual_settle": "年沉降量(mm，负沉正弹)",
    "total_settle_mm": "历史累计沉降(mm)",
    "total_settle": "历史累计沉降(mm)",
    "station_name": "监测站名称",
    "station_id": "测位 ID",
    "area": "行政区",
    "data_time": "数据时间",
    "period_deep": "周期埋深变化(m)",
    "annual_deep": "年度埋深变化(m)",
    "period_pressure": "周期孔压变化",
    "annual_pressure": "年度孔压变化",
    "period_displacement_3d": "周期位移变化",
    "annual_displacement_3d": "年度位移变化",
    # —— 运维通用 ——
    "snapshot_time": "快照时间",
    "type": "仪器类型",
    # —— t_om_device_online_by_type ——
    "total": "总数",
    "online": "在线数",
    "offline": "离线数",
    "online_rate": "在线率(%)",
    # —— t_om_complete_rate_by_type / t_om_effective_rate_by_type ——
    "device_count": "设备数",
    "actual_count": "实际采集数",
    "invalid_count": "无效数据数",
    "theory_count": "应采集数",
    "rate": "比率(%)",
    "window_start": "窗口开始",
    "window_end": "窗口结束",
    # —— t_om_data_alarm_summary ——
    "total_count": "总仪器数",
    "alarm_count": "告警仪器数",
    "normal_count": "正常仪器数",
    "week_avg": "近7天告警占比(%)",
    "month_avg": "近30天告警占比(%)",
    "alarm_ratio_pct": "告警占比(%)",
    "alarm_ratio": "告警占比(%)",
    # —— t_om_device_alarm_list ——
    "device_id": "设备编号",
    "device_name": "标编号",
    "data_type": "告警指标",
    "source": "传感器名",
    "area_name": "位置",
    "status": "状态",
    "ss_value": "实时值",
    "yj_value": "预警阈值",
    "content": "告警内容",
    "off_time": "告警起始时间",
    "create_time": "创建/建档时间",
    "alarm_duration": "告警持续时长",
    "alarm_days": "告警持续天数",
    # —— t_om_offline_device_list / 月完整·有效列表 ——
    "device_key": "设备编号",
    "name": "标编号",
    "sn": "仪器机身号",
    "data_collect_time": "最后采集时间",
    "offline_duration": "离线时长",
    "offline_days": "离线天数",
    "project_key": "项目键",
    "monitor_count": "实际采集数",
    "complete_rate": "完整率/有效率(%)",
}


def _library_column_title_map() -> dict[str, str]:
    """从监测库 catalog 汇总 key/alias → 中文 title（再叠运维/沉降兜底）。"""
    out = dict(_FALLBACK_COLUMN_TITLES)
    try:
        from app.data_query_agent.catalog import get_library_catalog

        cat = get_library_catalog()
        for lib in cat.libraries:
            if lib.period_key:
                out.setdefault(lib.period_key, "周期变化")
            if lib.annual_key:
                out.setdefault(lib.annual_key, "年变化")
            for col in lib.columns:
                title = (col.title or "").strip()
                if not title:
                    continue
                out[col.key] = title
                for alias in col.aliases or ():
                    a = str(alias).strip()
                    if a:
                        out[a] = title
    except Exception:  # noqa: BLE001
        logger.debug("chatbot column title map: catalog unavailable", exc_info=True)
    # 英文字段统一小写键，便于 PG 返回列名大小写不一致时命中
    folded: dict[str, str] = {}
    for k, v in out.items():
        folded[k] = v
        if k.isascii() and k.lower() != k:
            folded.setdefault(k.lower(), v)
        elif k.isascii():
            folded.setdefault(k.lower(), v)
    return folded


def _column_display_title(title_map: dict[str, str], key: str) -> str:
    if key in title_map:
        return title_map[key]
    low = key.lower() if key.isascii() else key
    return title_map.get(low) or key


def apply_chatbot_nl2sql_column_titles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    将英文字段名换成中文 title（沉降 libraries + 运维 om 列并集）。
    若同行同时有 period_* 与 annual_*，隐藏 annual_*（区间问句优先周期列）。
    """
    if not rows:
        return rows
    title_map = _library_column_title_map()
    col_order: list[str] = []
    seen: set[str] = set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        for k in r.keys():
            sk = str(k)
            if sk not in seen:
                seen.add(sk)
                col_order.append(sk)
    if not col_order:
        return rows

    has_period = any(
        k.startswith("period_") or "周期" in str(_column_display_title(title_map, k))
        for k in col_order
    )
    drop: set[str] = set()
    if has_period:
        for k in col_order:
            if k.startswith("annual_") or k in ("annual_settle_mm", "annual_settle"):
                drop.add(k)
            title = _column_display_title(title_map, k)
            if "年沉降" in title or title.startswith("年度"):
                # 仅当存在 period 列时藏年列，避免误伤独立「年沉降」问句
                if any(x.startswith("period_") for x in col_order):
                    drop.add(k)

    keep = [c for c in col_order if c not in drop] or list(col_order)
    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            out.append(r)  # type: ignore[arg-type]
            continue
        renamed: dict[str, Any] = {}
        for c in keep:
            title = _column_display_title(title_map, c)
            # 避免同 title 覆盖：已有同名则保留原 key
            if title in renamed and title != c:
                renamed[c] = r.get(c)
            else:
                renamed[title] = r.get(c)
        out.append(renamed)
    return out
