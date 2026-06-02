"""
20260602 版超温分析报告：槽位辅助渲染与 query 上下文推断。

严格对齐《模板---锅炉管壁超温智能分析报告-最新.docx》章节结构与表头。
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Callable

OVERHEAT_DATA_SOURCE_LABELS: dict[str, str] = {
    "q1": "测点超温明细",
    "q2": "周区域概览",
    "q3": "周趋势按日",
    "q4": "全事件运行工况",
    "q5": "SIS关联参数汇总",
    "q6": "吹灰区域汇总",
    "q7": "磨煤机运行汇总",
}

# 报告正文仅输出章节标题；下列规则来自 docx 红色底纹/红字说明，仅供程序与 LLM 参考，禁止写入报告。
OVERHEAT_CH1_INTRO = "## 超温情况概览\n\n"

OVERHEAT_DOCX_AUTHORING_RULES = (
    "【docx 模板红色说明文字（禁止出现在报告正文）】\n"
    "1. 概览章：未指定锅炉时输出全部机组；按日/按周/机组范围由 Query 与 report_context 决定，"
    "多机组须分表；异常等级Ⅰ～Ⅳ已在表格列中体现，勿重复输出等级定义段落。\n"
    "2. 原因剖析章：须结合负荷/主汽压力/主汽温度/炉膛负压/氧量等工况与 q4～q7 数据；"
    "周报告须分析日趋势；禁止照搬模板示例句（如「炉膛整体火焰中心上移…」）除非有数据或 RAG 依据。\n"
    "3. 措施章：「关联本次…以下内容为示例」等为写作指引，禁止原样输出；"
    "禁止输出 docx 中的示例措施全文。\n"
)

_DAILY_KW = ("今天", "今日", "昨天", "昨日", "前天", "前日", "当天", "当日", "某一天", "一天")
_WEEKLY_KW = (
    "本周", "这周", "上周", "上上周", "上星期", "本周内", "上周内",
    "本月", "这个月", "上月", "上个月", "上上月",
)

# 测点明细表列（与 docx 模板一致）
POINT_TABLE_COLUMNS = (
    "区域名称",
    "测点名称",
    "最大超温值",
    "最小超温值",
    "最大连续超温时长",
    "超温日期",
    "异常等级",
)

# 周区域概览表列
WEEKLY_REGION_COLUMNS = (
    "区域名称",
    "超温点数",
    "周最大超温值",
    "周最小超温值",
    "周最大连续超温时长",
    "周趋势",
    "异常等级",
)


def overheat_data_source_label(item_id: str) -> str:
    return OVERHEAT_DATA_SOURCE_LABELS.get(item_id, "业务数据")


def infer_overheat_report_context(query: str) -> dict[str, Any]:
    """从用户问题推断按日/按周模式（默认 weekly）。"""
    q = (query or "").strip()
    mode = "weekly"
    if any(k in q for k in _DAILY_KW):
        mode = "daily"
    elif any(k in q for k in _WEEKLY_KW):
        mode = "weekly"
    unit_scope = "all" if re.search(r"(所有|全部|各|全厂).{0,6}(锅炉|机组)", q) else "single"
    if re.search(r"未指定.{0,4}机组", q):
        unit_scope = "all"
    return {
        "analysis_mode": mode,
        "unit_scope": unit_scope,
        "t_start": "",
        "t_end": "",
    }


def filter_overheat_slot_ids(slots: list[Any], report_context: dict[str, Any] | None) -> list[Any]:
    """按日/按周跳过不适用的概览槽位。"""
    ctx = report_context or {}
    mode = ctx.get("analysis_mode", "weekly")
    skip: set[str] = set()
    if mode == "daily":
        skip = {"s02_weekly_marker", "s04_weekly_section"}
    else:
        skip = {"s02_daily_marker", "s03_daily_section"}
    return [s for s in slots if getattr(s, "id", "") not in skip]


def _partition_by_boiler(rows: list[dict], key: str = "机组名称") -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get(key) or "未知机组").strip()
        buckets[name].append(row)
    return dict(buckets)


def _fmt_time_range(ctx: dict[str, Any]) -> tuple[str, str]:
    t0 = str(ctx.get("t_start") or "").strip()
    t1 = str(ctx.get("t_end") or "").strip()
    if t0 and t1:
        return t0, t1
    return "____年__月__日 __时__分", "____年__月__日 __时__分"


def _fmt_temp_cell(val: Any) -> str:
    if val is None or str(val).strip() == "":
        return ""
    s = str(val).strip()
    if s.endswith("℃"):
        return s
    try:
        num = float(s.replace("℃", ""))
        if num == int(num):
            return f"{int(num)}℃"
        return f"{num}℃"
    except ValueError:
        return s if "℃" in s else f"{s}℃"


def _fmt_duration_cell(val: Any) -> str:
    if val is None or str(val).strip() == "":
        return ""
    s = str(val).strip()
    if s.endswith("分"):
        return s
    try:
        return f"{int(float(s))}分"
    except ValueError:
        return s


def _map_point_row(row: dict) -> dict[str, Any]:
    max_t = row.get("最大超温值") or row.get("最大超温值_℃")
    min_t = row.get("最小超温值") or row.get("最小超温值_℃")
    dur = row.get("最大连续超温时长") or row.get("最大连续超温时长_分钟")
    return {
        "区域名称": row.get("区域名称"),
        "测点名称": row.get("测点名称") or row.get("测点编号"),
        "最大超温值": _fmt_temp_cell(max_t),
        "最小超温值": _fmt_temp_cell(min_t),
        "最大连续超温时长": _fmt_duration_cell(dur),
        "超温日期": row.get("超温日期"),
        "异常等级": row.get("异常等级"),
    }


def _render_data_table(
    rows: list[dict],
    columns: tuple[str, ...],
    *,
    render_table: Callable[..., tuple[str, dict[str, Any]]],
    max_rows: int,
    empty_message: str,
) -> str:
    """渲染无额外 Markdown 小标题的数据表（对齐 docx 表头）。"""
    if not rows:
        md, _ = render_table(
            [],
            max_rows=max_rows,
            title="",
            empty_message=empty_message,
            subsection=False,
        )
        return md
    md, _ = render_table(
        rows,
        max_rows=max_rows,
        title="",
        empty_message=empty_message,
        subsection=False,
    )
    return md


def _build_weekly_trend_text(
    boiler: str,
    device: str,
    q3_rows: list[dict],
    *,
    t_start: str = "",
    t_end: str = "",
) -> str:
    device = (device or "").strip()
    matched = [
        r for r in q3_rows
        if isinstance(r, dict)
        and str(r.get("机组名称") or "").strip() == boiler
        and str(r.get("受热面名称") or "").strip() == device
    ]
    by_date: dict[date, int] = {}
    for r in matched:
        d = r.get("超温日期")
        if isinstance(d, datetime):
            day = d.date()
        elif isinstance(d, date):
            day = d
        else:
            s = str(d or "")[:10]
            try:
                day = datetime.strptime(s, "%Y-%m-%d").date()
            except ValueError:
                continue
        cnt = int(r.get("当日超温测点数") or 0)
        by_date[day] = by_date.get(day, 0) + cnt

    start_day = end_day = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        if t_start and start_day is None:
            try:
                start_day = datetime.strptime(t_start[:19], fmt).date()
            except ValueError:
                pass
        if t_end and end_day is None:
            try:
                end_day = datetime.strptime(t_end[:19], fmt).date()
            except ValueError:
                pass
    if start_day and end_day and start_day <= end_day:
        cur = start_day
        while cur <= end_day:
            by_date.setdefault(cur, 0)
            cur += timedelta(days=1)
    if not by_date:
        return "（无周趋势数据）"
    parts = [f"{d.day}日 {by_date[d]}个" for d in sorted(by_date.keys())]
    return "  |  ".join(parts)


def _format_level_counts(row: dict) -> str:
    parts = []
    for key, label in (
        ("Ⅰ级数量", "Ⅰ级（轻微超温）"),
        ("Ⅱ级数量", "Ⅱ级（中度超温）"),
        ("Ⅲ级数量", "Ⅲ级（严重超温）"),
        ("Ⅳ级数量", "Ⅳ级（临界爆管）"),
    ):
        n = row.get(key)
        if n is None:
            n = 0
        parts.append(f"{label}{int(n)}个")
    return " ".join(parts)


def _map_weekly_region_row(
    row: dict,
    *,
    boiler: str,
    q3_rows: list[dict],
    t_start: str,
    t_end: str,
) -> dict[str, Any]:
    device = str(row.get("受热面名称") or "").strip()
    trend = _build_weekly_trend_text(boiler, device, q3_rows, t_start=t_start, t_end=t_end)
    pts = row.get("超温点数")
    pts_s = f"{int(pts)}个" if pts is not None and str(pts).strip() != "" else ""
    return {
        "区域名称": row.get("区域名称"),
        "超温点数": pts_s,
        "周最大超温值": _fmt_temp_cell(row.get("周最大超温值_℃") or row.get("周最大超温值")),
        "周最小超温值": _fmt_temp_cell(row.get("周最小超温值_℃") or row.get("周最小超温值")),
        "周最大连续超温时长": _fmt_duration_cell(
            row.get("周最大连续超温时长_分钟") or row.get("周最大连续超温时长")
        ),
        "周趋势": trend,
        "异常等级": _format_level_counts(row),
    }


def render_overheat_daily_section(
    rows: list[dict],
    *,
    report_context: dict[str, Any] | None,
    render_table: Callable[..., tuple[str, dict[str, Any]]],
    max_rows: int,
    empty_message: str,
) -> str:
    """--按日超温分析-- 整节：按机组分块，每块含机组信息、起止时间与测点表。"""
    t_start, t_end = _fmt_time_range(report_context or {})
    parts: list[str] = []
    if not rows:
        parts.append("机组信息：（待补充）\n")
        parts.append(f"开始时间：{t_start}     结束时间：{t_end}\n\n")
        parts.append(_render_data_table([], POINT_TABLE_COLUMNS, render_table=render_table, max_rows=max_rows, empty_message=empty_message))
        return "".join(parts)

    for idx, (boiler, chunk) in enumerate(sorted(_partition_by_boiler(rows).items())):
        if idx > 0:
            parts.append("\n")
        parts.append(f"机组信息：{boiler}\n")
        parts.append(f"开始时间：{t_start}     结束时间：{t_end}\n\n")
        mapped = [_map_point_row(r) for r in chunk if isinstance(r, dict)]
        parts.append(
            _render_data_table(
                mapped, POINT_TABLE_COLUMNS,
                render_table=render_table, max_rows=max_rows, empty_message=empty_message,
            )
        )
    return "".join(parts)


def render_overheat_weekly_section(
    q1_rows: list[dict],
    q2_rows: list[dict],
    q3_rows: list[dict],
    *,
    report_context: dict[str, Any] | None,
    render_table: Callable[..., tuple[str, dict[str, Any]]],
    max_rows: int,
    empty_message: str,
) -> str:
    """--按周超温分析-- 整节：每机组含周概览表 + 测点详情表（模板 39～90 行结构）。"""
    ctx = report_context or {}
    t_start, t_end = _fmt_time_range(ctx)
    q1_by_boiler = _partition_by_boiler(q1_rows)
    q2_by_boiler = _partition_by_boiler(q2_rows)
    boilers = sorted(set(q1_by_boiler) | set(q2_by_boiler))
    if not boilers:
        parts = [
            "机组信息：（待补充）\n",
            f"周超温概览：开始时间：{t_start}     结束时间：{t_end}\n\n",
            _render_data_table([], WEEKLY_REGION_COLUMNS, render_table=render_table, max_rows=max_rows, empty_message=empty_message),
            _render_data_table([], POINT_TABLE_COLUMNS, render_table=render_table, max_rows=max_rows, empty_message=empty_message),
        ]
        return "".join(parts)

    parts: list[str] = []
    for idx, boiler in enumerate(boilers):
        if idx > 0:
            parts.append("\n周超温详情：\n\n")
        parts.append(f"机组信息：{boiler}\n")
        parts.append(f"周超温概览：开始时间：{t_start}     结束时间：{t_end}\n\n")

        region_rows = q2_by_boiler.get(boiler) or []
        mapped_region = [
            _map_weekly_region_row(r, boiler=boiler, q3_rows=q3_rows, t_start=t_start, t_end=t_end)
            for r in region_rows if isinstance(r, dict)
        ]
        parts.append(
            _render_data_table(
                mapped_region, WEEKLY_REGION_COLUMNS,
                render_table=render_table, max_rows=max_rows, empty_message=empty_message,
            )
        )
        parts.append("\n\n")
        point_rows = q1_by_boiler.get(boiler) or []
        mapped_points = [_map_point_row(r) for r in point_rows if isinstance(r, dict)]
        parts.append(
            _render_data_table(
                mapped_points, POINT_TABLE_COLUMNS,
                render_table=render_table, max_rows=max_rows, empty_message=empty_message,
            )
        )
    return "".join(parts)


def build_overheat_distribution_note(
    q1_rows: list[dict],
    infer_distribution: Callable[[list[dict]], tuple[str, str]],
) -> str:
    pattern, reason = infer_distribution(q1_rows)
    return f"超温分布特征：{pattern}（{reason}）"
