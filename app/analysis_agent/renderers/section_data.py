"""章节 Agent 取数切片、事实清单与叙述 Markdown 工具（与超温确定性渲染解耦）。"""
from __future__ import annotations

import re
from typing import Any

from app.analysis_agent.deterministics.settle_text import (
    format_mm,
    is_rebound,
    is_sink,
    movement_word,
    strip_formula_text,
)
from app.analysis_agent.deterministics.site_display import township_breakdown

_PLAN_ITEM_LABELS: dict[str, str] = {
    "q0": "统一检修优先级",
    "q1": "报告基础信息",
    "q2a": "测点超温时段",
    "q2b": "全事件运行工况",
    "q2c": "超温测点分级汇总",
    "q2d": "设计实测壁温极值",
    "q3a": "区域汇总统计",
    "q3b": "尖峰频次统计",
    "q4a": "壁温时序",
    "q4b": "SIS关联参数时序",
    "q5a": "历史缺陷检修记录",
    "q5b": "整改效果验证",
    "q6a": "壁温趋势",
    "q6b": "多测点对照",
    "q6c": "历史同类对标",
    "q6d": "DCS参数联动趋势",
    "q_ops_rates": "各仪器类型运行三率",
    "q_ops_offline_list": "离线设备明细",
    "q_ops_month_offline_alarm": "本月设备离线告警汇总",
    "q_layer0": "平原区地面沉降",
    "q_area_rank": "各行政区沉降汇总",
    "q_key_area": "重点监测区地面沉降",
    "q_compress": "压缩层组监测成果",
    "q_forecast": "沉降趋势预测",
    # 地降季报等（与 report plan.items 对齐；同号多义时用业务语义覆盖展示）
    "q2": "行政区沉降汇总",
    "q3": "分区站点统计",
    "q4": "分层标含水层",
    "q5": "重点区域站点",
    "q6": "全市站点清单",
    "q7": "地下水辅助",
    "q8": "气象辅助",
}

_CHAPTER_PREFIX_RE = re.compile(r"^[一二三四五六七八九十百]+、")

_FIELD_UNIT_HINTS: dict[str, str] = {
    "highest_temp": "一般为 ℃，勿写作 MPa/MW",
    "limit_temp": "一般为 ℃",
    "temperature": "一般为 ℃",
    "temp": "一般为 ℃",
    "steam_pressure_value": "按数据原值书写，勿猜测单位",
    "main_steam_pressure": "按数据原值书写，勿猜测单位",
    "load_mw": "一般为 MW，勿与 MPa 混淆",
    "power_mw": "一般为 MW",
}


def data_source_label(item_id: str) -> str:
    return _PLAN_ITEM_LABELS.get(item_id, "业务数据")


def replace_viz_config_ids(
    text: str,
    id_to_title: dict[str, str] | None,
) -> str:
    """把正文里的表/图配置 id（如 c_typical_dual）替换为中文标题。"""
    if not (text or "").strip() or not id_to_title:
        return text or ""
    out = text
    # 长 id 优先，避免前缀误伤
    for cid, title in sorted(id_to_title.items(), key=lambda kv: len(kv[0]), reverse=True):
        key = (cid or "").strip()
        name = (title or "").strip()
        if not key or not name or key == name:
            continue
        out = re.sub(rf"`{re.escape(key)}`", name, out)
        out = re.sub(rf"「{re.escape(key)}」", f"「{name}」", out)
        out = re.sub(rf"『{re.escape(key)}』", f"『{name}』", out)
        out = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", name, out)
    return out


def sanitize_report_narrative(
    text: str,
    *,
    viz_id_titles: dict[str, str] | None = None,
) -> str:
    if not (text or "").strip():
        return text or ""
    out = replace_viz_config_ids(text, viz_id_titles)
    out = re.sub(r"\bq[0-9][a-z]?\b", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\[q[0-9][a-z]?\]", "", out, flags=re.IGNORECASE)
    out = re.sub(r"[\[（(]?置信度[：:\s]*[高中低][）)\]]?", "", out)
    out = re.sub(r"[（(]依据[：:][^）)\n]+[）)]", "", out)
    out = re.sub(r"依据[：:][^\n。；;]+", "", out)
    out = re.sub(r"数据依据[：:][^\n]+", "", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return strip_formula_text(out.strip())


def _normalize_heading_text(text: str) -> str:
    s = re.sub(r"[、：:（）()\s]", "", (text or "").strip())
    return s.casefold()


def strip_leading_duplicate_heading(md: str, slot_title: str) -> str:
    if not (md or "").strip():
        return ""
    lines = md.split("\n")
    idx = 0
    skipped = 0
    target = _normalize_heading_text(slot_title) if slot_title else ""
    chapter_prefix = (
        _CHAPTER_PREFIX_RE.match(slot_title).group(0)
        if slot_title and _CHAPTER_PREFIX_RE.match(slot_title)
        else ""
    )
    while idx < len(lines) and skipped < 6:
        line = lines[idx].strip()
        if not line:
            idx += 1
            skipped += 1
            continue
        m = re.match(r"^\s{0,3}(#{1,6})\s+(.+)$", line)
        if m:
            heading = _normalize_heading_text(m.group(2))
            if target and (target in heading or heading in target):
                idx += 1
                skipped += 1
                continue
            if chapter_prefix and chapter_prefix in m.group(2):
                idx += 1
                skipped += 1
                continue
            if not slot_title:
                idx += 1
                skipped += 1
                continue
        break
    return "\n".join(lines[idx:]).strip()


def wrap_narrative_markdown(title: str, body: str, *, include_heading: bool = True) -> str:
    cleaned = sanitize_report_narrative(strip_leading_duplicate_heading((body or "").strip(), title))
    if not cleaned:
        if include_heading and title:
            return f"### {title}\n\n（待补充）\n\n"
        return "（待补充）\n\n"
    if include_heading and title:
        return f"### {title}\n\n{cleaned}\n\n"
    return f"{cleaned}\n\n"


def gather_item_rows(gathered_data: dict[str, list[dict]], item_ids: tuple[str, ...]) -> list[dict]:
    out: list[dict] = []
    for iid in item_ids:
        chunk = gathered_data.get(iid) or []
        if isinstance(chunk, list):
            out.extend([r for r in chunk if isinstance(r, dict)])
    return out


def resolve_data_subset(
    gathered_data: dict[str, list[dict]],
    item_ids: tuple[str, ...],
    *,
    strict: bool,
) -> dict[str, list[dict]]:
    if not item_ids:
        return dict(gathered_data) if gathered_data else {}
    subset: dict[str, list[dict]] = {}
    for iid in item_ids:
        chunk = gathered_data.get(iid)
        if isinstance(chunk, list):
            subset[iid] = [r for r in chunk if isinstance(r, dict)]
        else:
            subset[iid] = []
    if subset or strict:
        return subset
    return dict(gathered_data)


def _numeric(val: Any) -> float | None:
    if val is None or str(val).strip() == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _normalize_steam_pressure_mpa(val: Any) -> tuple[float | None, bool]:
    n = _numeric(val)
    if n is None:
        return None, False
    if n > 50:
        return n / 1000.0, True
    return n, False


def _audit_preview_row_limit(item_id: str, row_count: int, *, slot_id: str = "") -> int:
    if item_id == "q2a" and slot_id.startswith(("ch2", "s02")):
        return min(max(3, row_count), 12)
    if item_id in ("q2b", "q2d", "q5b"):
        return min(1, row_count)
    if item_id == "q2c":
        return min(3, row_count)
    if item_id in ("q_ops_rates", "q_ops_offline_list"):
        return min(max(row_count, 0), 40)
    if item_id in ("q_layer0", "q_area_rank", "q_key_area"):
        return min(max(row_count, 0), 80)
    return min(3, row_count)


def _audit_format_field(key: str, val: Any) -> str:
    kl = str(key).lower()
    if kl in ("全事件主汽压力_mpa", "主汽压力_mpa", "steam_pressure_value"):
        mpa, converted = _normalize_steam_pressure_mpa(val)
        if mpa is None:
            return f"{key}=待补充"
        note = "（kPa→MPa）" if converted else ""
        return f"{key}={mpa:.3f}MPa{note}"
    hint = _FIELD_UNIT_HINTS.get(kl, "")
    suffix = f" （{hint}）" if hint else ""
    return f"{key}={val}{suffix}"


def _append_q2a_top_points(lines: list[str], rows: list[dict]) -> None:
    scored: list[tuple[float, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        dur = _numeric(row.get("超温总时长_秒")) or 0.0
        code = str(row.get("测点编号") or "").strip()
        if code:
            scored.append((dur, code))
    if not scored:
        return
    scored.sort(key=lambda x: x[0], reverse=True)
    lines.append(f"- 尖峰测点(时长排序): {'、'.join(c for _, c in scored[:5])}")


def _station_clause_name(row: dict) -> str:
    clause = str(row.get("display_clause") or "").strip()
    if clause:
        return clause
    annex = str(row.get("annex_label") or "").strip()
    if annex:
        return annex
    return str(row.get("project_name") or row.get("station_name") or "").strip() or "未命名监测站"


def _dominate_cn(sink_n: int, rebound_n: int) -> str:
    if sink_n > rebound_n:
        return "以沉降为主"
    if rebound_n > sink_n:
        return "以回弹为主"
    if sink_n or rebound_n:
        return "沉降与回弹基本相当"
    return "整体基本持平"


def _append_layer0_plain_facts(lines: list[str], rows: list[dict], *, area_filter: str = "") -> None:
    """全市/分区主导方向与极值；正文用中文站名和毫米，不写公式。"""
    scored: list[tuple[float, str, str, dict]] = []
    by_area: dict[str, list[dict]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        delta = _numeric(row.get("delta_mm"))
        if delta is None:
            continue
        area = str(row.get("area") or "").strip()
        if area_filter and area != area_filter:
            continue
        if not area or area in {"未知", "未知区"}:
            continue
        scored.append((delta, _station_clause_name(row), area, row))
        by_area.setdefault(area, []).append(row)
    if not scored:
        return
    n = len(scored)
    sink_n = sum(1 for d, *_ in scored if is_sink(d))
    rebound_n = sum(1 for d, *_ in scored if is_rebound(d))
    flat_n = n - sink_n - rebound_n
    city_bits = f"沉降{sink_n}座，回弹{rebound_n}座"
    if flat_n:
        city_bits += f"，无垂向形变{flat_n}座"
    lines.append(f"- 全市：{n}座监测站，{city_bits}，{_dominate_cn(sink_n, rebound_n)}")
    top_sink = [x for x in sorted(scored, key=lambda row: row[0]) if is_sink(x[0])][:2]
    top_rebound = [x for x in sorted(scored, key=lambda row: row[0], reverse=True) if is_rebound(x[0])][:2]
    if top_sink:
        parts = [f"{name}沉降{format_mm(delta)}" for delta, name, _area, _row in top_sink]
        lines.append(f"- 最大/次之沉降站点: {'；'.join(parts)}")
    if top_rebound:
        parts = [f"{name}回弹{format_mm(delta)}" for delta, name, _area, _row in top_rebound]
        lines.append(f"- 最大/次之回弹站点: {'；'.join(parts)}")
    area_bits: list[str] = []
    for area in sorted(by_area.keys()):
        items = by_area[area]
        vals = [_numeric(r.get("delta_mm")) for r in items]
        known = [v for v in vals if v is not None]
        a_sink = sum(1 for v in known if is_sink(v))
        a_rebound = sum(1 for v in known if is_rebound(v))
        bit = f"{area}{len(items)}座（沉降{a_sink}/回弹{a_rebound}，{_dominate_cn(a_sink, a_rebound)}）"
        towns = township_breakdown(items)
        if towns:
            bit += f"；{towns}"
        area_bits.append(bit)
    if area_bits:
        lines.append("- 分行政区: " + "；".join(area_bits))


_COMPRESS_COL_LABELS = (
    ("c01", "第一压缩层组"),
    ("c12", "第二压缩层组"),
    ("c23", "第三压缩层组"),
    ("c34", "第四压缩层组"),
)

_COMPRESS_LAYER_NAMES = (
    (0, "第一压缩层组"),
    (1, "第二压缩层组"),
    (2, "第三压缩层组"),
    (3, "第四压缩层组"),
)


def _format_signed_mm(val: float | None) -> str:
    if val is None:
        return "待补充"
    return f"{movement_word(val)}{format_mm(val)}"


def _key_area_station_phrase(row: dict) -> str:
    code = str(row.get("station_code") or "").strip()
    loc = str(row.get("location_raw") or "").strip()
    if loc and code:
        return f"位于{loc}的{code}监测站"
    return code or "未命名监测站"


def _append_key_area_facts(lines: list[str], rows: list[dict]) -> None:
    """按重点区写整体/极值/分层概况；禁止诱导逐站罗列四层数值。"""
    by_area: dict[str, list[dict]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        area = str(row.get("area_name") or "").strip() or "未分区"
        by_area.setdefault(area, []).append(row)
    if not by_area:
        return
    lines.append(
        "- 撰写口径：每个重点监测区一段——"
        "①整体沉降/回弹座数与主导方向；②最大/次之地面沉降与回弹站；"
        "③四个压缩层组整体概况+少数例外；禁止逐站罗列第一至第四压缩层数值"
    )
    for area, items in by_area.items():
        scored: list[tuple[float, dict]] = []
        for row in items:
            delta = _numeric(row.get("total_delta_mm"))
            if delta is None:
                continue
            scored.append((delta, row))
        n = len(items)
        known_n = len(scored)
        miss = n - known_n
        sink_n = sum(1 for d, _ in scored if is_sink(d))
        rebound_n = sum(1 for d, _ in scored if is_rebound(d))
        head = f"- 【{area}】共{n}座监测站"
        if miss:
            head += f"（有数{known_n}/待补充{miss}）"
        head += f"，沉降{sink_n}座、回弹{rebound_n}座，{_dominate_cn(sink_n, rebound_n)}"
        lines.append(head)

        top_sink = [x for x in sorted(scored, key=lambda t: t[0]) if is_sink(x[0])][:2]
        top_rebound = [
            x for x in sorted(scored, key=lambda t: t[0], reverse=True) if is_rebound(x[0])
        ][:2]
        if top_sink:
            lines.append(
                "  - 最大/次之地面沉降: "
                + "；".join(
                    f"{_key_area_station_phrase(r)}沉降{format_mm(d)}" for d, r in top_sink
                )
            )
        if top_rebound:
            lines.append(
                "  - 最大/次之地面回弹: "
                + "；".join(
                    f"{_key_area_station_phrase(r)}回弹{format_mm(d)}" for d, r in top_rebound
                )
            )
        if not scored:
            lines.append("  - 地面总沉降：待补充")

        layer_bits: list[str] = []
        exceptions: list[str] = []
        ground_dominate_sink = sink_n >= rebound_n and sink_n > 0
        ground_dominate_rebound = rebound_n > sink_n
        for col, label in _COMPRESS_COL_LABELS:
            vals: list[tuple[float, dict]] = []
            for row in items:
                val = _numeric(row.get(col))
                if val is None:
                    continue
                vals.append((val, row))
            if not vals:
                layer_bits.append(f"{label}待补充")
                continue
            l_sink = sum(1 for v, _ in vals if is_sink(v))
            l_rebound = sum(1 for v, _ in vals if is_rebound(v))
            layer_bits.append(f"{label}沉降{l_sink}座/回弹{l_rebound}座")
            for val, row in vals:
                phrase = _key_area_station_phrase(row)
                if ground_dominate_sink and is_rebound(val):
                    exceptions.append(f"{phrase}的{label}发生回弹")
                elif ground_dominate_rebound and is_sink(val):
                    exceptions.append(f"{phrase}的{label}发生沉降")
        lines.append("  - 分层监测概况: " + "；".join(layer_bits))
        if exceptions:
            lines.append("  - 分层例外: " + "；".join(exceptions[:10]))
        else:
            lines.append("  - 分层例外: 有数层组方向与本区地面主导方向大体一致（或本层无数）")


_DISTRICT_ID_TO_NAME = {
    "chaoyang": "朝阳区",
    "haidian": "海淀区",
    "tongzhou": "通州区",
    "shunyi": "顺义区",
    "changping": "昌平区",
    "daxing": "大兴区",
    "pinggu": "平谷区",
    "yanqing": "延庆区",
}

_LAYER_SLOT_FROM = {
    "layer_results__l01": 0,
    "layer_results__l12": 1,
    "layer_results__l23": 2,
    "layer_results__l34": 3,
    "annex_layer__l01": 0,
    "annex_layer__l12": 1,
    "annex_layer__l23": 2,
    "annex_layer__l34": 3,
}


def _district_name_from_slot(slot_id: str) -> str:
    suffix = (slot_id or "").split("__", 1)[-1]
    return _DISTRICT_ID_TO_NAME.get(suffix, "")


def _append_area_rank_facts(lines: list[str], rows: list[dict]) -> None:
    for row in rows:
        if not isinstance(row, dict):
            continue
        area = str(row.get("area") or "").strip()
        if not area:
            continue
        avg = _numeric(row.get("avg_delta_mm"))
        n = int(row.get("station_count") or 0)
        avg_txt = format_mm(avg, signed=True) + "毫米" if avg is not None else "待补充"
        lines.append(f"- {area} {n}座，平均沉降量{avg_txt}（负值为沉降，正值为回弹）")


def _row_layer_from(row: dict) -> int:
    """layer_from=0 合法，禁止用 `or -1`（0 会被当成缺省）。"""
    raw = row.get("layer_from")
    if raw is None or raw == "":
        return -1
    try:
        return int(raw)
    except (TypeError, ValueError):
        return -1


def _filter_compress_rows(rows: list[dict], *, layer_from: int | None) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if layer_from is not None and _row_layer_from(row) != layer_from:
            continue
        if _numeric(row.get("compress_mm")) is None:
            continue
        out.append(row)
    return out


def _compress_scored(rows: list[dict]) -> list[tuple[float, str]]:
    scored: list[tuple[float, str]] = []
    for row in rows:
        val = _numeric(row.get("compress_mm"))
        if val is None:
            continue
        scored.append((val, _station_clause_name(row)))
    return scored


def _append_one_compress_layer_facts(
    lines: list[str],
    rows: list[dict],
    *,
    title: str,
) -> None:
    scored = _compress_scored(rows)
    if not scored:
        lines.append(f"- {title}：本层组暂无有效压缩量，写待补充")
        return
    # 按监测站去重计座，禁止把多行压缩对累加成「总站点数」
    station_n = len({name for _val, name in scored})
    sink_n = sum(1 for v, _ in scored if is_sink(v))
    rebound_n = sum(1 for v, _ in scored if is_rebound(v))
    lines.append(
        f"- {title}：{station_n}座，沉降{sink_n}座，回弹{rebound_n}座，{_dominate_cn(sink_n, rebound_n)}"
    )
    top_sink = [x for x in sorted(scored, key=lambda row: row[0]) if is_sink(x[0])][:2]
    top_rebound = [x for x in sorted(scored, key=lambda row: row[0], reverse=True) if is_rebound(x[0])][:2]
    if top_sink:
        lines.append(
            "- 最大/次之沉降: "
            + "；".join(f"{name}沉降{format_mm(val)}" for val, name in top_sink)
        )
    if top_rebound:
        lines.append(
            "- 最大/次之回弹: "
            + "；".join(f"{name}回弹{format_mm(val)}" for val, name in top_rebound)
        )


def _append_compress_facts(lines: list[str], rows: list[dict], *, slot_id: str = "") -> None:
    if slot_id == "conclusion":
        lines.append("- 压缩层组须按层分别计座，禁止把四个层组行数相加当总站点数（全市地面站约42座）")
        for layer_from, name in _COMPRESS_LAYER_NAMES:
            subset = _filter_compress_rows(rows, layer_from=layer_from)
            _append_one_compress_layer_facts(lines, subset, title=name)
        return
    layer_from = _LAYER_SLOT_FROM.get(slot_id)
    subset = _filter_compress_rows(rows, layer_from=layer_from)
    title = "本压缩层组"
    for lf, name in _COMPRESS_LAYER_NAMES:
        if layer_from is not None and lf == layer_from:
            title = name
            break
    _append_one_compress_layer_facts(lines, subset, title=title)


def _append_q3b_top_points(lines: list[str], rows: list[dict]) -> None:
    scored: list[tuple[float, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cnt = _numeric(row.get("瞬时尖峰超温次数")) or 0.0
        code = str(row.get("测点编号") or "").strip()
        if code:
            scored.append((cnt, code))
    if not scored:
        return
    scored.sort(key=lambda x: x[0], reverse=True)
    lines.append(f"- 尖峰频次测点: {'、'.join(c for _, c in scored[:5])}")


def _append_forecast_facts(lines: list[str], rows: list[dict]) -> None:
    metas = [
        r
        for r in rows
        if isinstance(r, dict) and str(r.get("row_kind") or "") == "meta"
    ]
    if not metas:
        lines.append("- 沉降趋势预测：暂无有效结果，写待补充")
        return
    meta = metas[0]
    if str(meta.get("status") or "") != "ok":
        reason = str(meta.get("reason") or "样本不足").strip()
        lines.append(f"- 沉降趋势预测：依据不足（{reason}），正文写待补充，禁止编造预测数值")
        disc = str(meta.get("disclaimer") or "").strip()
        if disc:
            lines.append(f"- 说明: {disc}")
        return
    project = str(meta.get("project_name") or "").strip() or "代表站"
    settle_st = str(meta.get("settle_station_name") or "").strip()
    water_st = str(meta.get("water_station_name") or "").strip()
    hist_n = int(_numeric(meta.get("history_month_count")) or 0)
    start_m = str(meta.get("history_start_month") or "").strip()
    end_m = str(meta.get("history_end_month") or "").strip()
    slope = _numeric(meta.get("slope_mm_per_month"))
    r2 = _numeric(meta.get("r2"))
    pred_delta = _numeric(meta.get("pred_delta_next_q_mm"))
    direction = str(meta.get("pred_direction") or "").strip()
    corr_w = _numeric(meta.get("corr_water"))
    rain_note = str(meta.get("rain_note") or "").strip()
    pressure_note = str(meta.get("pressure_note") or "").strip()
    forecast_months = str(meta.get("forecast_months") or "").strip()
    disc = str(meta.get("disclaimer") or "").strip()
    lines.append(
        f"- 代表站: {project}"
        + (f"，分层标{settle_st}" if settle_st else "")
        + (f"，配对水位井{water_st}" if water_st else "")
    )
    if start_m and end_m:
        lines.append(f"- 历史数据窗: {start_m}至{end_m}，共{hist_n}个自然月月均点（回溯约12个月）")
    if slope is not None:
        lines.append(f"- 月均线性趋势斜率: {slope:.4f}毫米/月（负为沉降、正为回弹）")
    if r2 is not None:
        lines.append(f"- 拟合决定系数R²: {r2:.4f}")
    if corr_w is not None:
        lines.append(f"- 与地下水标高相关绝对值约{corr_w:.2f}")
    if pressure_note:
        lines.append(f"- 孔隙水依据: {pressure_note}")
    if rain_note:
        lines.append(f"- 降水依据: {rain_note}")
    if forecast_months:
        lines.append(f"- 预测月份: {forecast_months}")
    if pred_delta is not None:
        abs_mm = format_mm(abs(pred_delta))
        word = direction or movement_word(pred_delta)
        if word == "基本持平":
            lines.append(f"- 下一季度预测变化: 相对窗末累计沉降基本持平（约{format_mm(pred_delta)}）")
        else:
            lines.append(f"- 下一季度预测变化: 相对窗末累计沉降预计{word}{abs_mm}")
    if disc:
        lines.append(f"- 方法说明: {disc}")


def build_audit_facts(
    subset: dict[str, list[dict]],
    query: str,
    *,
    slot_id: str = "",
    task_status: dict[str, str] | None = None,
) -> str:
    lines: list[str] = ["【可引用事实（正文数值仅可来自下列键值；勿在报告中写数据源编号）】"]
    status = task_status or {}
    for iid, rows in subset.items():
        label = data_source_label(iid)
        st = status.get(iid, "")
        if st in ("mandatory_failed", "optional_failed"):
            lines.append(f"- [{label}] 查询失败 → 须写明查询失败（非无数据）")
            continue
        if not rows:
            lines.append(f"- [{label}] 无数据行 → 不得编造该部分明细")
            continue
        if iid in (
            "q_layer0",
            "q_area_rank",
            "q_key_area",
            "q_compress",
            "q_ops_month_offline_alarm",
            "q_forecast",
        ):
            if iid == "q_compress":
                layer_from = _LAYER_SLOT_FROM.get(slot_id)
                if slot_id == "conclusion":
                    n_show = len({str(r.get("project_name") or "") for r in rows if isinstance(r, dict)})
                    lines.append(
                        f"- [{label}] 涉及约{n_show}座监测站的压缩层对（下列按层汇总，禁止抄未过滤总行数）"
                    )
                else:
                    n_show = len(_filter_compress_rows(rows, layer_from=layer_from))
                    lines.append(
                        f"- [{label}] 本层组{n_show}座（下列为已写好的中文事实，禁止抄英文字段或公式）"
                    )
            elif iid == "q_ops_month_offline_alarm":
                lines.append(f"- [{label}] {len(rows)} 行（下列为已写好的中文事实，禁止抄英文字段或公式）")
                row0 = rows[0] if isinstance(rows[0], dict) else {}
                alarm_n = int(_numeric(row0.get("offline_alarm_count")) or 0)
                station_n = int(_numeric(row0.get("station_count")) or 0)
                # 对外统一句式，不向叙述暴露月窗/最新批次回退来源
                lines.append(
                    f"- 本月发生设备离线告警{alarm_n}次，涉及地面沉降监测站{station_n}座"
                )
            elif iid == "q_forecast":
                lines.append(f"- [{label}]（下列为已写好的中文事实，禁止抄英文字段或公式）")
            else:
                lines.append(
                    f"- [{label}] {len(rows)} 座/行（下列为已写好的中文事实，禁止抄英文字段或公式）"
                )
            if iid == "q_layer0":
                area_filter = ""
                if slot_id.startswith("district_results__"):
                    area_filter = _district_name_from_slot(slot_id)
                _append_layer0_plain_facts(lines, rows, area_filter=area_filter)
            elif iid == "q_area_rank":
                _append_area_rank_facts(lines, rows)
            elif iid == "q_key_area":
                _append_key_area_facts(lines, rows)
            elif iid == "q_compress":
                _append_compress_facts(lines, rows, slot_id=slot_id)
            elif iid == "q_forecast":
                _append_forecast_facts(lines, rows)
            continue
        preview_n = _audit_preview_row_limit(iid, len(rows), slot_id=slot_id)
        lines.append(f"- [{label}] {len(rows)} 行")
        for row in rows[:preview_n]:
            if not isinstance(row, dict):
                continue
            shown = 0
            for key, val in row.items():
                if val is None or str(val).strip() == "":
                    continue
                lines.append(f"  {_audit_format_field(str(key), val)}")
                shown += 1
                if shown >= 18:
                    break
        if len(rows) > preview_n:
            lines.append(f"  ……共 {len(rows)} 行，以上仅预览前 {preview_n} 行")
        if iid == "q2a":
            _append_q2a_top_points(lines, rows)
        if iid == "q3b":
            _append_q3b_top_points(lines, rows)
    q = (query or "").strip()
    if q:
        lines.append(f"- 用户问题约束: {q[:500]}")
    lines.append("- 主汽压力已换算为 MPa 的以事实清单为准；禁止自造 MPa/MW/℃")
    return "\n".join(lines)


def build_data_coverage_note(
    subset: dict[str, list[dict]],
    *,
    task_status: dict[str, str] | None = None,
) -> str:
    status = task_status or {}
    parts: list[str] = []
    for iid, rows in subset.items():
        label = data_source_label(iid)
        st = status.get(iid, "")
        if st in ("mandatory_failed", "optional_failed"):
            parts.append(f"{label}=查询失败")
        else:
            parts.append(f"{label}={'有' if rows else '无'}数据")
    if not parts:
        return ""
    return "【本槽数据覆盖】" + "；".join(parts)


def rag_snippets_for_slot(
    context_snippets: list[str],
    gathered_data: dict[str, list[dict]],
    item_ids: tuple[str, ...],
) -> list[str]:
    if not context_snippets:
        return []
    if not item_ids:
        return context_snippets[:8]
    if any(gather_item_rows(gathered_data, item_ids)):
        return context_snippets[:8]
    return context_snippets[:2]
