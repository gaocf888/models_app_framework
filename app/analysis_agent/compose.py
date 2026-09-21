"""地降报告 schema_version 3：板块库 + compose 展开为 V1 同构 chapters/plan/tables/charts。

无 compose 或 schema_version < 3 时不要调用本模块（由 report_spec 走原样 V1）。
封面 {period_label}/{issue_no}/{report_date} 留给 initialize 按请求替换，避免 registry 缓存串期。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import yaml

from app.core.logging import get_logger

logger = get_logger(__name__)

_REPORTS_DIR = Path(__file__).resolve().parents[2] / "configs" / "analysis_agent_reports"
_LIBRARY_DIR = _REPORTS_DIR / "section_library"
_SELF = "@self"

# 封面 TOC 不需要数据库；页码用破折号占位
_TOC_PAGE = "—"


def _library_index() -> dict[str, Any]:
    path = _LIBRARY_DIR / "_index.yaml"
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def _load_section(section_id: str) -> dict[str, Any]:
    path = _LIBRARY_DIR / f"{section_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"section_library_missing:{section_id}:{path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data.get("id"):
        raise ValueError(f"section_library_invalid:{section_id}")
    return data


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, val in overlay.items():
        if key == "variants":
            continue
        if isinstance(out.get(key), dict) and isinstance(val, dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


def _apply_variant(section: dict[str, Any], variant: str | None) -> dict[str, Any]:
    body = copy.deepcopy(section)
    variants = body.pop("variants", None) or {}
    if not variant:
        return body
    overlay = variants.get(variant)
    if not isinstance(overlay, dict):
        logger.warning("compose unknown variant=%s section=%s", variant, section.get("id"))
        return body
    return _deep_merge(body, overlay)


def _subst_str(text: str, mapping: dict[str, str]) -> str:
    out = text
    for src, dst in mapping.items():
        out = out.replace(src, dst)
    return out


def _subst_obj(obj: Any, mapping: dict[str, str]) -> Any:
    if isinstance(obj, str):
        return _subst_str(obj, mapping)
    if isinstance(obj, list):
        return [_subst_obj(x, mapping) for x in obj]
    if isinstance(obj, dict):
        return {k: _subst_obj(v, mapping) for k, v in obj.items()}
    return obj


def _item_mapping(item: dict[str, Any], params: dict[str, Any] | None = None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for key, val in item.items():
        mapping[f"{{item.{key}}}"] = str(val)
    for key, val in (params or {}).items():
        mapping[f"{{{key}}}"] = str(val)
    return mapping


def _suffix_id(base: str, suffix: str | None) -> str:
    if not suffix:
        return base
    return f"{base}__{suffix}"


def _normalize_viz_item(
    spec: dict[str, Any],
    *,
    chapter_id: str,
    id_suffix: str | None,
    iterate_item: dict[str, Any] | None,
) -> dict[str, Any]:
    out = copy.deepcopy(spec)
    orig_id = str(out.get("id") or "").strip()
    if orig_id:
        out["id"] = _suffix_id(orig_id, id_suffix)
    attach = str(out.get("attach_to_chapter") or "").strip()
    if attach == _SELF or not attach:
        out["attach_to_chapter"] = chapter_id
    filter_field = str(out.get("filter_field") or "").strip()
    filter_from = str(out.get("filter_from_iterate") or "").strip()
    if filter_field and filter_from and iterate_item is not None:
        equals = iterate_item.get(filter_from)
        if equals is not None:
            out["row_filter"] = {"field": filter_field, "equals": equals}
    return out


def _chapter_from_section(
    section: dict[str, Any],
    *,
    chapter_id: str,
    id_suffix: str | None,
    iterate_item: dict[str, Any] | None,
    params: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    mapping = _item_mapping(iterate_item or {}, params)
    body = _subst_obj(section, mapping)
    chapter: dict[str, Any] = {
        "id": chapter_id,
        "kind": str(body.get("kind") or "llm_section"),
        "title": str(body.get("title") or ""),
        "source_item_ids": list(body.get("source_item_ids") or []),
        "outline": list(body.get("outline") or []),
        "constraints": list(body.get("constraints") or []),
        "narrative_instruction": str(body.get("narrative_instruction") or ""),
        "allowed_outputs": list(body.get("allowed_outputs") or ["paragraph"]),
        "use_emit_tools": bool(body.get("use_emit_tools", False)),
        "static_body": str(body.get("static_body") or ""),
        "stream_live": bool(body.get("stream_live", False)),
    }
    tables = [
        _normalize_viz_item(t, chapter_id=chapter_id, id_suffix=id_suffix, iterate_item=iterate_item)
        for t in (body.get("tables") or [])
        if isinstance(t, dict)
    ]
    charts = [
        _normalize_viz_item(c, chapter_id=chapter_id, id_suffix=id_suffix, iterate_item=iterate_item)
        for c in (body.get("charts") or [])
        if isinstance(c, dict)
    ]
    plan_items = [dict(p) for p in (body.get("plan_items") or []) if isinstance(p, dict) and p.get("item_id")]
    return chapter, tables, charts, plan_items


def _dedupe_plan(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        iid = str(item.get("item_id") or "").strip()
        if not iid or iid in seen:
            continue
        seen.add(iid)
        out.append(dict(item))
    return out


def _build_toc(chapters: list[dict[str, Any]]) -> str:
    lines = ["目录"]
    n = 0
    for ch in chapters:
        if str(ch.get("id") or "") == "cover":
            continue
        title = str(ch.get("title") or "").strip()
        if not title:
            continue
        n += 1
        lines.append(f"{n}. {title} …… {_TOC_PAGE}")
    return "\n".join(lines)


def compose_report_dict(raw: dict[str, Any], *, analysis_type: str = "") -> dict[str, Any]:
    """把 schema 3 模版展开为可交给 slots_from_spec_dict 的字典。"""
    compose = raw.get("compose")
    if not isinstance(compose, list) or not compose:
        raise ValueError(f"compose_empty:{analysis_type}")

    index = _library_index()
    iterate_vocab = {
        "plain_districts": list(index.get("plain_districts") or []),
        "compress_groups": list(index.get("compress_groups") or []),
    }
    plan_items = _dedupe_plan(list(index.get("shared_plan_items") or []))
    plan_raw = raw.get("plan")
    if isinstance(plan_raw, dict) and isinstance(plan_raw.get("items"), list):
        plan_items = _dedupe_plan(plan_items + [x for x in plan_raw["items"] if isinstance(x, dict)])
    elif isinstance(raw.get("plan_items"), list):
        plan_items = _dedupe_plan(plan_items + [x for x in raw["plan_items"] if isinstance(x, dict)])

    chapters: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    charts: list[dict[str, Any]] = []

    for step in compose:
        if not isinstance(step, dict):
            continue
        section_id = str(step.get("section") or "").strip()
        if not section_id:
            continue
        params = dict(step.get("params") or {})
        variant = str(step.get("variant") or "").strip() or None
        section = _apply_variant(_load_section(section_id), variant)
        iterate_key = str(step.get("iterate") or section.get("iterate") or "").strip()
        items: list[dict[str, Any]]
        if iterate_key:
            raw_items = iterate_vocab.get(iterate_key) or []
            items = [x for x in raw_items if isinstance(x, dict) and x.get("id")]
            if not items:
                logger.warning("compose iterate empty key=%s section=%s", iterate_key, section_id)
                continue
        else:
            items = [{}]

        for item in items:
            suffix = str(item["id"]) if item.get("id") else None
            chapter_id = _suffix_id(section_id, suffix) if suffix else section_id
            chapter, ch_tables, ch_charts, ch_plan = _chapter_from_section(
                section,
                chapter_id=chapter_id,
                id_suffix=suffix,
                iterate_item=item or None,
                params=params,
            )
            chapters.append(chapter)
            tables.extend(ch_tables)
            charts.extend(ch_charts)
            plan_items = _dedupe_plan(plan_items + ch_plan)

    toc = _build_toc(chapters)
    title = str(raw.get("title") or "")
    for ch in chapters:
        body = str(ch.get("static_body") or "")
        if body:
            ch["static_body"] = body.replace("{toc}", toc).replace("{title}", title)

    out = dict(raw)
    out["schema_version"] = 3
    out["chapters"] = chapters
    out["tables"] = tables
    out["charts"] = charts
    out["plan"] = {"items": plan_items}
    return out
