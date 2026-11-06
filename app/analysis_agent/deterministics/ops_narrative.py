"""监测站点运行情况：按 q_ops_rates 全量类型生成段落（数字不经 LLM）。"""

from __future__ import annotations

from typing import Any

from app.analysis_agent.deterministics.om_type_display import sort_om_rows_by_report_type


def _f(val: Any) -> float | None:
    if val is None or str(val).strip() == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _i(val: Any) -> int | None:
    n = _f(val)
    if n is None:
        return None
    return int(round(n))


def _fmt_rate(val: Any) -> str:
    n = _f(val)
    if n is None:
        return "—"
    return f"{n:.2f}%"


def _quality_label(effective: float | None) -> str:
    """以数据有效率为唯一评价依据。"""
    if effective is None:
        return "暂无法评价数据质量"
    if effective >= 95.0:
        return "数据质量较好"
    if effective >= 90.0:
        return "数据质量合格"
    return "数据质量较差"


def _unit(device_type: str) -> str:
    return "套" if "气象" in (device_type or "") else "台"


def _type_name(row: dict[str, Any]) -> str:
    return str(row.get("device_type") or row.get("type") or "").strip()


def render_station_ops_narrative(gathered_data: dict[str, list[dict[str, Any]]] | None) -> str:
    """每种有三率行的仪器类型一段，覆盖表格全部类型。"""
    rates = sort_om_rows_by_report_type(
        [r for r in ((gathered_data or {}).get("q_ops_rates") or []) if isinstance(r, dict)]
    )
    offline = [r for r in ((gathered_data or {}).get("q_ops_offline_list") or []) if isinstance(r, dict)]
    if not rates:
        return "其他仪器类型本批暂无运维汇总。"

    offline_by_type: dict[str, list[dict[str, Any]]] = {}
    for row in offline:
        key = _type_name(row)
        if not key:
            continue
        offline_by_type.setdefault(key, []).append(row)

    paragraphs: list[str] = []
    for row in rates:
        name = _type_name(row)
        if not name:
            continue
        total = _i(row.get("total"))
        online_rate = _f(row.get("online_rate"))
        complete = _f(row.get("complete_rate"))
        effective = _f(row.get("effective_rate"))
        offline_n = _i(row.get("offline"))
        unit = _unit(name)
        total_txt = str(total) if total is not None else "—"
        quality = _quality_label(effective)
        line = (
            f"{name}共计设备{total_txt}{unit}，在线率{_fmt_rate(online_rate)}，"
            f"数据完整率{_fmt_rate(complete)}，数据有效率{_fmt_rate(effective)}，"
            f"{quality}。具体情况详见图1。"
        )
        type_offline = offline_by_type.get(name) or []
        need_offline = (online_rate is not None and online_rate < 100.0) or (offline_n or 0) > 0 or bool(type_offline)
        if need_offline and type_offline:
            bits: list[str] = []
            for item in type_offline:
                sta = str(item.get("device_name") or item.get("station_name") or "").strip()
                if not sta:
                    continue
                days = _i(item.get("offline_days"))
                if days is None:
                    bits.append(sta)
                else:
                    bits.append(f"{sta}离线{days}天")
            if bits:
                line += f"合计{len(bits)}台设备离线，分别为：{'、'.join(bits)}。"
        paragraphs.append(line)
    if not paragraphs:
        return "其他仪器类型本批暂无运维汇总。"
    return "\n\n".join(paragraphs)
