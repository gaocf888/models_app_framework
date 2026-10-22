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
    "q_layer0": "平原区地面沉降",
    "q_area_rank": "各行政区沉降汇总",
    "q_key_area": "重点监测区地面沉降",
    "q_compress": "压缩层组监测成果",
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


def sanitize_report_narrative(text: str) -> str:
    if not (text or "").strip():
        return text or ""
    out = text
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
    lines.append(f"- 全市：{n}座监测站，沉降{sink_n}座，回弹{rebound_n}座，{_dominate_cn(sink_n, rebound_n)}")
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


def _append_key_area_facts(lines: list[str], rows: list[dict]) -> None:
    """按 YAML 重点区 area_name 汇总 13 站；空 Δ 标明待补充。"""
    by_area: dict[str, list[dict]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        area = str(row.get("area_name") or "").strip() or "未分区"
        by_area.setdefault(area, []).append(row)
    if not by_area:
        return
    area_bits: list[str] = []
    for area, items in by_area.items():
        deltas = [_numeric(r.get("total_delta_mm")) for r in items]
        known = [d for d in deltas if d is not None]
        miss = len(deltas) - len(known)
        sink = sum(1 for d in known if is_sink(d))
        rebound = sum(1 for d in known if is_rebound(d))
        bit = f"{area}{len(items)}座（有数{len(known)}"
        if miss:
            bit += f"/待补充{miss}"
        if known:
            bit += f"/沉降{sink}/回弹{rebound}"
        area_bits.append(bit + "）")
    lines.append("- 按重点监测区: " + "；".join(area_bits))
    for area, items in by_area.items():
        for row in items:
            code = str(row.get("station_code") or "").strip()
            loc = str(row.get("location_raw") or "").strip()
            name = f"位于{loc}的{code}监测站" if loc and code else (code or "未命名监测站")
            delta = _numeric(row.get("total_delta_mm"))
            if delta is None:
                delta_txt = "待补充"
            else:
                delta_txt = f"{movement_word(delta)}{format_mm(delta)}"
            lines.append(f"- {area} {name} {delta_txt}")


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


def _append_compress_facts(lines: list[str], rows: list[dict], *, slot_id: str = "") -> None:
    layer_from = _LAYER_SLOT_FROM.get(slot_id)
    scored: list[tuple[float, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if layer_from is not None and int(row.get("layer_from") or -1) != layer_from:
            continue
        val = _numeric(row.get("compress_mm"))
        if val is None:
            continue
        scored.append((val, _station_clause_name(row)))
    if not scored:
        return
    sink_n = sum(1 for v, _ in scored if is_sink(v))
    rebound_n = sum(1 for v, _ in scored if is_rebound(v))
    lines.append(
        f"- 本压缩层组：{len(scored)}座，沉降{sink_n}座，回弹{rebound_n}座，{_dominate_cn(sink_n, rebound_n)}"
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
        if iid in ("q_layer0", "q_area_rank", "q_key_area", "q_compress"):
            lines.append(f"- [{label}] {len(rows)} 座/行（下列为已写好的中文事实，禁止抄英文字段或公式）")
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
