"""平原区监测情况：由层位0站点行生成正文（数字不经 LLM，报告中不出现 Δ/平均沉降量）。"""

from __future__ import annotations

from typing import Any


def _f(val: Any) -> float | None:
    if val is None or str(val).strip() == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _station_display(project_name: str) -> str:
    raw = (project_name or "").strip()
    if not raw:
        return "未命名站"
    if "(" in raw and raw.endswith(")"):
        inner = raw[raw.find("(") + 1 : -1].strip()
        if inner:
            return inner
    return raw


def _fmt_mm(val: float) -> str:
    return f"{abs(val):.2f}mm"


def _dominate_phrase(sink_n: int, rise_n: int) -> str:
    if sink_n > rise_n:
        return "整体以沉降为主"
    if rise_n > sink_n:
        return "整体以上升为主"
    if sink_n or rise_n:
        return "沉降与上升基本相当"
    return "整体基本持平"


def _rank_side(
    scored: list[tuple[float, str, str]],
    *,
    sink: bool,
    limit: int = 2,
) -> list[tuple[float, str, str]]:
    if sink:
        picked = [x for x in sorted(scored, key=lambda row: row[0], reverse=True) if x[0] > 0]
    else:
        picked = [x for x in sorted(scored, key=lambda row: row[0]) if x[0] < 0]
    return picked[:limit]


def _station_clause(items: list[tuple[float, str, str]], *, sink: bool) -> str:
    kind = "沉降" if sink else "上升"
    if not items:
        return f"本期未见{kind}测站。"
    parts: list[str] = []
    labels = ("最大", "次之")
    for i, (delta, name, area) in enumerate(items):
        rank = labels[i] if i < len(labels) else f"第{i + 1}"
        amount = _fmt_mm(delta)
        loc = f"{name}（{area}）"
        if i == 0:
            parts.append(f"{kind}{rank}的测站为{loc}，{kind}量为{amount}")
        else:
            parts.append(f"{rank}为{loc}，{kind}量为{amount}")
    return "；".join(parts) + "。"


def render_plain_monitoring_narrative(gathered_data: dict[str, list[dict[str, Any]]] | None) -> str:
    """全市格局 + 各区升降主导 + 最大/次之沉降与上升站点。"""
    rows = [r for r in ((gathered_data or {}).get("q_layer0") or []) if isinstance(r, dict)]
    scored: list[tuple[float, str, str]] = []
    by_area: dict[str, list[float]] = {}
    for row in rows:
        delta = _f(row.get("delta_mm"))
        if delta is None:
            continue
        area = str(row.get("area") or "").strip() or "未知区"
        name = _station_display(str(row.get("project_name") or row.get("station_name") or ""))
        scored.append((delta, name, area))
        by_area.setdefault(area, []).append(delta)
    if not scored:
        return "本期平原区暂无可用的监测站沉降数据，待补充。"

    n = len(scored)
    sink_n = sum(1 for d, _, _ in scored if d > 0)
    rise_n = sum(1 for d, _, _ in scored if d < 0)
    flat_n = n - sink_n - rise_n
    overall = _dominate_phrase(sink_n, rise_n)
    bits = [f"其中发生沉降的{sink_n}座、发生上升的{rise_n}座"]
    if flat_n:
        bits.append(f"基本持平的{flat_n}座")
    overall_line = f"本期平原区共有监测站{n}座，{'、'.join(bits)}。{overall}。"

    area_lines: list[str] = []
    area_order = sorted(
        by_area.keys(),
        key=lambda a: (
            -(sum(1 for v in by_area[a] if v > 0) - sum(1 for v in by_area[a] if v < 0)),
            a,
        ),
    )
    for area in area_order:
        vals = by_area[area]
        a_sink = sum(1 for v in vals if v > 0)
        a_rise = sum(1 for v in vals if v < 0)
        area_lines.append(
            f"{area}{len(vals)}座测站中{a_sink}座沉降、{a_rise}座上升，{_dominate_phrase(a_sink, a_rise)}。"
        )
    district_line = "分行政区看：" + "".join(area_lines) if area_lines else ""

    sink_clause = _station_clause(_rank_side(scored, sink=True), sink=True)
    rise_clause = _station_clause(_rank_side(scored, sink=False), sink=False)
    station_line = "站点方面：" + sink_clause + rise_clause

    return "\n\n".join(p for p in (overall_line, district_line, station_line) if p)
