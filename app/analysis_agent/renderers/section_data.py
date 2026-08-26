"""章节 Agent 取数切片、事实清单与叙述 Markdown 工具（与超温确定性渲染解耦）。"""
from __future__ import annotations

import re
from typing import Any

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
    return out.strip()


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


def wrap_narrative_markdown(title: str, body: str) -> str:
    cleaned = sanitize_report_narrative(strip_leading_duplicate_heading((body or "").strip(), title))
    if not cleaned:
        return f"### {title}\n\n（待补充）\n\n" if title else "（待补充）\n\n"
    if title:
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
