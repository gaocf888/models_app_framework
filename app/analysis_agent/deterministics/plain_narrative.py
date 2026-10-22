"""平原区监测情况：由层位0站点行生成正文（数字不经 LLM，报告中不出现公式）。"""

from __future__ import annotations

from typing import Any

from app.analysis_agent.deterministics.settle_text import format_mm, is_rebound, is_sink
from app.analysis_agent.deterministics.site_display import attach_site_display


def _f(val: Any) -> float | None:
    if val is None or str(val).strip() == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _station_display(row: dict[str, Any]) -> str:
    clause = str(row.get("display_clause") or "").strip()
    if clause:
        return clause
    project = str(row.get("project_name") or row.get("station_name") or "").strip()
    area = str(row.get("area") or "").strip()
    extra = attach_site_display(project, area)
    return extra.get("display_clause") or project or "未命名监测站"


def _dominate_phrase(sink_n: int, rebound_n: int) -> str:
    if sink_n > rebound_n:
        return "整体以沉降为主"
    if rebound_n > sink_n:
        return "整体以回弹为主"
    if sink_n or rebound_n:
        return "沉降与回弹基本相当"
    return "整体基本持平"


def _rank_side(
    scored: list[tuple[float, str, str]],
    *,
    sink: bool,
    limit: int = 2,
) -> list[tuple[float, str, str]]:
    if sink:
        picked = [x for x in sorted(scored, key=lambda row: row[0]) if is_sink(x[0])]
    else:
        picked = [x for x in sorted(scored, key=lambda row: row[0], reverse=True) if is_rebound(x[0])]
    return picked[:limit]


def _station_clause(items: list[tuple[float, str, str]], *, sink: bool) -> str:
    kind = "沉降" if sink else "回弹"
    if not items:
        return f"本期未见{kind}测站。"
    parts: list[str] = []
    labels = ("最大", "次之")
    for i, (delta, name, _area) in enumerate(items):
        rank = labels[i] if i < len(labels) else f"第{i + 1}"
        amount = format_mm(delta)
        if i == 0:
            parts.append(f"{kind}{rank}的测站为{name}，{kind}量为{amount}")
        else:
            parts.append(f"{rank}为{name}，{kind}量为{amount}")
    return "；".join(parts) + "。"


def render_plain_monitoring_narrative(gathered_data: dict[str, list[dict[str, Any]]] | None) -> str:
    """全市格局 + 各区沉降/回弹主导 + 最大/次之站点。"""
    rows = [r for r in ((gathered_data or {}).get("q_layer0") or []) if isinstance(r, dict)]
    scored: list[tuple[float, str, str]] = []
    by_area: dict[str, list[float]] = {}
    for row in rows:
        delta = _f(row.get("delta_mm"))
        if delta is None:
            continue
        area = str(row.get("area") or "").strip()
        if not area or area in {"未知", "未知区"}:
            continue
        name = _station_display(row)
        scored.append((delta, name, area))
        by_area.setdefault(area, []).append(delta)
    if not scored:
        return "本期平原区暂无可用的监测站沉降数据，待补充。"

    n = len(scored)
    sink_n = sum(1 for d, _, _ in scored if is_sink(d))
    rebound_n = sum(1 for d, _, _ in scored if is_rebound(d))
    flat_n = n - sink_n - rebound_n
    overall = _dominate_phrase(sink_n, rebound_n)
    bits = [f"其中发生沉降的{sink_n}座、发生回弹的{rebound_n}座"]
    if flat_n:
        bits.append(f"基本持平的{flat_n}座")
    overall_line = f"本期平原区共有监测站{n}座，{'、'.join(bits)}。{overall}。"

    area_lines: list[str] = []
    area_order = sorted(
        by_area.keys(),
        key=lambda a: (
            -(sum(1 for v in by_area[a] if is_sink(v)) - sum(1 for v in by_area[a] if is_rebound(v))),
            a,
        ),
    )
    for area in area_order:
        vals = by_area[area]
        a_sink = sum(1 for v in vals if is_sink(v))
        a_rebound = sum(1 for v in vals if is_rebound(v))
        area_lines.append(
            f"{area}{len(vals)}座测站中{a_sink}座沉降、{a_rebound}座回弹，{_dominate_phrase(a_sink, a_rebound)}。"
        )
    district_line = "分行政区看：" + "".join(area_lines) if area_lines else ""

    sink_clause = _station_clause(_rank_side(scored, sink=True), sink=True)
    rebound_clause = _station_clause(_rank_side(scored, sink=False), sink=False)
    station_line = "站点方面：" + sink_clause + rebound_clause

    return "\n\n".join(p for p in (overall_line, district_line, station_line) if p)
