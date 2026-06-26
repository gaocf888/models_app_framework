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
    "q0": "超温事件时间包络",
    "q1": "测点超温明细",
    "q2": "周区域概览",
    "q3": "周趋势按日",
    "q4": "全事件运行工况",
    "q5": "SIS关联参数汇总",
    "q6": "吹灰区域汇总",
    "q7": "磨煤机运行汇总",
}

# 报告正文仅输出章节标题；下列规则来自 docx 红色底纹/红字说明，仅供程序与 LLM 参考，禁止写入报告。
OVERHEAT_CH1_INTRO = "## 一、超温情况概览\n\n"

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
    """从用户问题推断按日/按周模式；未指定时间时默认昨天（按日章节）。"""
    from app.nl2sql.time_intent_display import (
        DEFAULT_TIME_WINDOW_TAG,
        extract_time_window_tag,
        resolve_statistical_time_range_display,
    )

    q = (query or "").strip()
    parsed_tag = extract_time_window_tag(q)
    mode = "weekly"
    if any(k in q for k in _DAILY_KW):
        mode = "daily"
    elif any(k in q for k in _WEEKLY_KW):
        mode = "weekly"
    elif not parsed_tag:
        mode = "daily"
    unit_scope = "all" if re.search(r"(所有|全部|各|全厂).{0,6}(锅炉|机组)", q) else "single"
    if re.search(r"未指定.{0,4}机组", q):
        unit_scope = "all"
    t_start, t_end = resolve_statistical_time_range_display(q)
    time_window_tag = parsed_tag or DEFAULT_TIME_WINDOW_TAG
    return {
        "analysis_mode": mode,
        "unit_scope": unit_scope,
        "t_start": t_start,
        "t_end": t_end,
        "time_window_tag": time_window_tag,
    }


def filter_overheat_slot_ids(slots: list[Any], report_context: dict[str, Any] | None) -> list[Any]:
    """按日/按周跳过不适用的概览槽位。"""
    ctx = report_context or {}
    mode = ctx.get("analysis_mode", "weekly")
    skip: set[str] = set()
    if mode == "daily":
        # 去掉 ”--按日超温分析“静态槽位的渲染
        # skip = {"s02_weekly_marker", "s04_weekly_section"}
        skip = {"s04_weekly_section"}
    else:
        # 去掉 ”--按周超温分析“静态槽位的渲染
        # skip = {"s02_daily_marker", "s03_daily_section"}
        skip = {"s03_daily_section"}
    return [s for s in slots if getattr(s, "id", "") not in skip]


def filter_overheat_synthesis_slots(
    slots: list[Any],
    report_context: dict[str, Any] | None,
    gathered_data: dict[str, list[dict]] | None = None,
) -> list[Any]:
    """按日/周过滤 + 按机组展开原因剖析槽。"""
    filtered = filter_overheat_slot_ids(slots, report_context)
    return expand_overheat_cause_slots(filtered, gathered_data)


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


def _fmt_boiler_time_range(ctx: dict[str, Any], boiler: str) -> tuple[str, str]:
    """优先 report_context 统计口径时间窗；无则回落 q0 事件包络；再则占位符。"""
    t0 = str(ctx.get("t_start") or "").strip()
    t1 = str(ctx.get("t_end") or "").strip()
    if t0 and t1:
        return t0, t1
    ranges = ctx.get("boiler_time_ranges")
    if isinstance(ranges, dict) and boiler in ranges:
        entry = ranges[boiler]
        if isinstance(entry, dict):
            q0_start = str(entry.get("t_start") or entry.get("最早超温开始时间") or "").strip()
            q0_end = str(entry.get("t_end") or entry.get("最晚超温结束时间") or "").strip()
            if q0_start and q0_end:
                return q0_start, q0_end
    return _fmt_time_range(ctx)


def build_boiler_time_ranges_from_q0(q0_rows: list[dict]) -> dict[str, dict[str, str]]:
    """从 q0 行构建 {机组名称: {t_start, t_end}}。"""
    out: dict[str, dict[str, str]] = {}
    for row in q0_rows:
        if not isinstance(row, dict):
            continue
        boiler = str(row.get("机组名称") or "").strip()
        if not boiler:
            continue
        t0 = str(
            row.get("最早超温开始时间") or row.get("最早开始时间") or row.get("t_start") or ""
        ).strip()
        t1 = str(
            row.get("最晚超温结束时间") or row.get("最晚结束时间") or row.get("t_end") or ""
        ).strip()
        if t0 and t1:
            out[boiler] = {"t_start": t0, "t_end": t1}
    return out


def enrich_overheat_report_context_from_gathered(
    ctx: dict[str, Any],
    gathered_data: dict[str, list[dict]] | None,
) -> dict[str, Any]:
    """将 q0 包络时间写入 report_context.boiler_time_ranges。"""
    if not gathered_data:
        return ctx
    q0_rows = gathered_data.get("q0") or []
    if not isinstance(q0_rows, list):
        return ctx
    ranges = build_boiler_time_ranges_from_q0([r for r in q0_rows if isinstance(r, dict)])
    if ranges:
        ctx = dict(ctx)
        ctx["boiler_time_ranges"] = ranges
    return ctx


def list_overheat_boilers_from_gathered(gathered_data: dict[str, list[dict]] | None) -> list[str]:
    """与第一章概览一致：从 q0/q1 去重机组名称并排序。"""
    if not gathered_data:
        return []
    names: set[str] = set()
    for key in ("q0", "q1"):
        for row in gathered_data.get(key) or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("机组名称") or "").strip()
            if name:
                names.add(name)
    return sorted(names)


def overheat_cause_narrative_instruction(boiler_name: str) -> str:
    """单机组原因剖析 LLM 任务（docx 97～110 行结构）。"""
    return (
        f"撰写「{boiler_name}」的超温原因剖析正文，"
        f"禁止输出 docx 模板红色说明或示例段落。"
        "【内部分析·禁止写入报告正文】须结合 q4/q5 理解负荷、主汽压力、炉膛负压、氧量，"
        "并结合超温分布特征与日趋势（周报告时）形成判断；"
        "上述内容不得单独成段，禁止出现「负荷分析」「主汽压力分析」「炉膛负压分析」"
        "「氧量分析」「超温分布特征」「日超温趋势分析」等小节标题或编号列表。"
        f"直接从概括句开始生成（用 1～3 句概括性分析，禁止输出「### 超温原因剖析」）；"
        "跨区关联仅可在此概括句简述，不得展开。"
        "然后依次输出 plain 小标题行："
        "烟气侧："
        "介质侧："
        "运行操作："
        "设备本体："
        "（以上四段写全炉/多测点共性诱因，须结合 q4/q5/q7 及机组级工况；"
        "「设备本体」段须结合 q1/事实包中的规格材质分布，说明各材质在本次超温工况下的设备侧敏感性，"
        "须挂钩具体区域或材质牌号，可引用 RAG 机理，无材质写「规格材质待补充」，禁止空泛罗列；"
        "可概括性提及吹灰/磨煤机整体情况，但禁止按区域逐条展开，禁止在此四段写「综上」内容）。"
        "最后单独以「综上：」起头，严格按用户消息中【按区域事实包】逐区输出专属诱因分析；"
        "每个区域块必须以事实包中的区域名起头，须先写「本区规格材质：…」（来自事实包），"
        "再写本区测点极值/等级/时长与诱因；"
        "每个区域块仅允许引用该块「本区测点」「本区规格材质」与「本区吹灰」；"
        "禁止在 A 区域块出现 B 区域名称、B 区域测点或 B 区域吹灰次数；"
        "禁止「A 区域积灰间接导致 B 区域超温」等跨区因果（除非开头概括句已简述）。"
        "本区无吹灰记录时禁止写吹灰频次诱因；无数据写待补充。"
        "禁止置信度、依据、q 编号、结论摘要。"
        "正文引用机组/受热面/测点/SIS 参数时须用中文名称，编号或编码放全角括号内（如 测点名称（测点编号））；"
        "禁止正文单独使用测点编号/测点编码作为主称谓（无名称时方可仅写编号）。"
        "禁止「可能、或许、大概、疑似、或与…有关、一般、通常、倾向于」等无数据支撑的泛化措辞；"
        "每条归因须点名测点展示名/数值/等级/时长或工况字段，数据不足写「待补充」。"
    )


_LIMIT_SUFFIX_RE = re.compile(r"\s+限[\d.]+\s*℃?\s*$")


def normalize_overheat_region_key(row: dict) -> str:
    """从 q1 行提取区域分组键（优先受热面名称，否则剥离区域名称中的限温后缀）。"""
    device = str(row.get("受热面名称") or "").strip()
    if device:
        return device
    region = str(row.get("区域名称") or "").strip()
    if region:
        return _LIMIT_SUFFIX_RE.sub("", region).strip() or region
    return "未知区域"


def _row_numeric(val: Any) -> float | None:
    if val is None or str(val).strip() == "":
        return None
    s = str(val).strip().replace("℃", "").replace("分", "")
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _match_q6_for_region(region_key: str, q6_rows: list[dict]) -> dict | None:
    """将 q6 吹灰汇总行与 q1 区域键匹配（精确优先，其次包含关系）。"""
    key = (region_key or "").strip()
    if not key or not q6_rows:
        return None
    exact: dict | None = None
    partial: dict | None = None
    for row in q6_rows:
        if not isinstance(row, dict):
            continue
        dev = str(row.get("受热面名称") or "").strip()
        if not dev:
            continue
        if dev == key:
            exact = row
            break
        if key in dev or dev in key:
            if partial is None or len(dev) < len(str(partial.get("受热面名称") or "")):
                partial = row
    return exact or partial


def group_q1_rows_by_region(q1_rows: list[dict]) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in q1_rows:
        if not isinstance(row, dict):
            continue
        buckets[normalize_overheat_region_key(row)].append(row)
    return dict(buckets)


def format_overheat_entity_label(
    name: str | None,
    code: str | None,
    *,
    fallback: str = "未知测点",
) -> str:
    """
    叙述章引用格式：优先中文名称，括号内标注编号/编码（与概览表口径一致）。
    仅有名称或仅有编号时单列；均无则 fallback。
    """
    n = (name or "").strip()
    c = (code or "").strip()
    if n and c and n != c:
        return f"{n}（{c}）"
    if n:
        return n
    if c:
        return c
    return fallback


def _point_display_label(row: dict) -> str:
    """从 q1 行提取测点展示名：测点名称（测点编号）。"""
    return format_overheat_entity_label(
        str(row.get("测点名称") or "").strip() or None,
        str(row.get("测点编号") or "").strip() or None,
    )


def _format_region_point(row: dict) -> str:
    label = _point_display_label(row)
    max_t = row.get("最大超温值_℃") or row.get("最大超温值")
    delta = row.get("最大监测超温差值_℃")
    level = str(row.get("异常等级") or "").strip()
    parts = [label]
    if max_t is not None and str(max_t).strip():
        parts.append(f"最高{max_t}℃" if "℃" not in str(max_t) else f"最高{max_t}")
    if level:
        parts.append(level)
    if delta is not None and str(delta).strip():
        parts.append(f"差值{delta}℃" if "℃" not in str(delta) else f"差值{delta}")
    return "、".join(parts)


def _format_region_materials(rows: list[dict]) -> str:
    """从 q1 行提取本区规格材质（去重）。"""
    seen: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        mat = str(row.get("规格材质") or "").strip()
        if mat and mat not in seen:
            seen.append(mat)
    if not seen:
        return "待补充"
    if len(seen) == 1:
        return seen[0]
    return f"{'、'.join(seen)}（本区存在多种材质，分析须分别说明）"


def _format_q6_summary(row: dict | None) -> str:
    if not row:
        return "无对应吹灰汇总记录→综上块禁止写该区域吹灰频次诱因"
    try:
        n = int(row.get("吹灰次数") or 0)
    except (TypeError, ValueError):
        n = 0
    days = row.get("吹灰天数")
    dur = row.get("总吹灰时长_分钟")
    parts = [f"吹灰{n}次"]
    if days is not None and str(days).strip() != "":
        parts.append(f"{days}天")
    if dur is not None and str(dur).strip() != "":
        parts.append(f"总时长{dur}分")
    return "，".join(parts)


def build_overheat_region_fact_packages(
    q1_rows: list[dict],
    q6_rows: list[dict] | None = None,
) -> str:
    """
    按 q1 区域分组构建「综上」专用事实包（方案 A）。
    每区仅含本区测点与本区 q6 吹灰匹配结果，供 LLM 逐区归因。
    """
    grouped = group_q1_rows_by_region([r for r in q1_rows if isinstance(r, dict)])
    if not grouped:
        return (
            "【按区域事实包·综上块仅可引用下列分区数据】\n"
            "（无 q1 测点明细，综上须写待补充）\n"
            "【跨区域禁令】综上每个区域块仅允许引用该区域「本区测点」「本区规格材质」与「本区吹灰」；"
            "禁止在 A 区域块出现 B 区域名称、测点或吹灰数据。"
        )

    def _region_sort_key(item: tuple[str, list[dict]]) -> float:
        _name, rows = item
        best = 0.0
        for r in rows:
            d = _row_numeric(r.get("最大监测超温差值_℃"))
            if d is not None:
                best = max(best, d)
            t = _row_numeric(r.get("最大超温值_℃") or r.get("最大超温值"))
            if t is not None:
                best = max(best, t)
        return best

    q6_list = [r for r in (q6_rows or []) if isinstance(r, dict)]
    lines: list[str] = [
        "【按区域事实包·综上块仅可引用下列分区数据】",
        "（「烟气侧/介质侧/运行操作/设备本体」四段写共性；「综上」须逐区复述下列分区，不得串区）",
    ]
    for idx, (region_key, rows) in enumerate(
        sorted(grouped.items(), key=_region_sort_key, reverse=True), start=1
    ):
        region_label = region_key
        for r in rows:
            rn = str(r.get("区域名称") or "").strip()
            if rn:
                region_label = rn
                break
        point_parts = [_format_region_point(r) for r in rows[:8]]
        if len(rows) > 8:
            point_parts.append(f"…共{len(rows)}个测点")
        max_temp = max(
            (t for t in (_row_numeric(r.get("最大超温值_℃") or r.get("最大超温值")) for r in rows) if t is not None),
            default=None,
        )
        max_dur = max(
            (d for d in (_row_numeric(r.get("最大连续超温时长_分钟") or r.get("最大连续超温时长")) for r in rows) if d is not None),
            default=None,
        )
        q6_row = _match_q6_for_region(region_key, q6_list)
        lines.append(f"区域{idx}：{region_key}（表格区域名称：{region_label}）")
        lines.append(f"- 本区测点（仅此区域，综上块仅可引用下列测点）: {'；'.join(point_parts)}")
        stat_parts: list[str] = []
        if max_temp is not None:
            stat_parts.append(f"本区最高壁温{int(max_temp) if max_temp == int(max_temp) else max_temp}℃")
        if max_dur is not None:
            stat_parts.append(f"本区最长连续超温{int(max_dur)}分")
        if stat_parts:
            lines.append(f"- 本区极值: {'；'.join(stat_parts)}")
        lines.append(f"- 本区规格材质: {_format_region_materials(rows)}")
        lines.append(f"- 本区吹灰: {_format_q6_summary(q6_row)}")
    lines.append(
        "【跨区域禁令】综上每个区域块仅允许引用该区域「本区测点」「本区规格材质」与「本区吹灰」；"
        "禁止在 A 区域块出现 B 区域名称、B 区域测点或 B 区域吹灰次数；"
        "本区测点展示格式为「测点名称（测点编号）」，正文引用须与此一致；"
        "禁止「A 区域问题间接导致 B 区域超温」类跨区因果（跨区关联仅可在开头概括句一句带过）。"
    )
    return "\n".join(lines)


def expand_overheat_cause_slots(
    slots: list[Any],
    gathered_data: dict[str, list[dict]] | None,
) -> list[Any]:
    """将 s06_cause 按 q0/q1 机组列表展开为多槽 LLM 调用。"""
    boilers = list_overheat_boilers_from_gathered(gathered_data)
    if not boilers:
        return slots
    out: list[Any] = []
    for slot in slots:
        if getattr(slot, "id", "") != "s06_cause":
            out.append(slot)
            continue
        for idx, boiler in enumerate(boilers):
            if len(boilers) > 1:
                out.append(
                    slot.__class__(
                        id=f"s06_cause_hdr__{idx}",
                        kind="static_markdown",
                        title="",
                        static_body=f"\n### {boiler}超温原因剖析\n\n",
                    )
                )
            out.append(
                slot.__class__(
                    id=f"s06_cause__{idx}",
                    kind=slot.kind,
                    title=slot.title,
                    source_item_ids=slot.source_item_ids,
                    narrative_instruction=overheat_cause_narrative_instruction(boiler),
                    table_id=slot.table_id,
                    template_id=slot.template_id,
                    static_body=slot.static_body,
                    stream_live=bool(slot.stream_live and idx == 0),
                    boiler_name=boiler,
                )
            )
    return out


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
        # parts.append("机组信息：（待补充）\n")
        # parts.append(f"开始时间：{t_start}     结束时间：{t_end}\n\n")
        parts.append(f"**机组信息：（待补充）        开始时间：{t_start}     结束时间：{t_end}**\n\n")
        parts.append(_render_data_table([], POINT_TABLE_COLUMNS, render_table=render_table, max_rows=max_rows, empty_message=empty_message))
        return "".join(parts)

    for idx, (boiler, chunk) in enumerate(sorted(_partition_by_boiler(rows).items())):
        if idx > 0:
            parts.append("\n")
        b_start, b_end = _fmt_boiler_time_range(report_context or {}, boiler)
        # parts.append(f"机组信息：{boiler}\n")
        # parts.append(f"开始时间：{b_start}     结束时间：{b_end}\n\n")
        parts.append(f"**机组信息：{boiler}        开始时间：{b_start}     结束时间：{b_end}**\n\n")
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
            # "机组信息：（待补充）\n",
            # f"周超温概览：开始时间：{t_start}     结束时间：{t_end}\n\n",
            f"**机组信息：（待补充）        周超温概览：开始时间：{t_start}     结束时间：{t_end}**\n\n",
            _render_data_table([], WEEKLY_REGION_COLUMNS, render_table=render_table, max_rows=max_rows, empty_message=empty_message),
            "\n\n**周超温详情：**\n\n",
            _render_data_table([], POINT_TABLE_COLUMNS, render_table=render_table, max_rows=max_rows, empty_message=empty_message),
        ]
        return "".join(parts)

    parts: list[str] = []
    for idx, boiler in enumerate(boilers):
        if idx > 0:
            parts.append("\n")
        b_start, b_end = _fmt_boiler_time_range(ctx, boiler)
        # parts.append(f"机组信息：{boiler}\n")
        # parts.append(f"周超温概览：开始时间：{b_start}     结束时间：{b_end}\n\n")
        parts.append(f"**机组信息：{boiler}        周超温概览：开始时间：{b_start}     结束时间：{b_end}**\n\n")

        region_rows = q2_by_boiler.get(boiler) or []
        mapped_region = [
            _map_weekly_region_row(r, boiler=boiler, q3_rows=q3_rows, t_start=b_start, t_end=b_end)
            for r in region_rows if isinstance(r, dict)
        ]
        parts.append(
            _render_data_table(
                mapped_region, WEEKLY_REGION_COLUMNS,
                render_table=render_table, max_rows=max_rows, empty_message=empty_message,
            )
        )
        parts.append("\n\n**周超温详情：**\n\n")
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
