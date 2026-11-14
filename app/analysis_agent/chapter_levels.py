"""季报/年报正文目录层级：仅白名单章在 chapter_start 带 title_level。"""

from __future__ import annotations

from typing import Any

# 1=一级（一、二、… / 附表），2=二级（（一）（二）…）
_TITLE_LEVEL_BY_SLOT_ID: dict[str, int] = {
    "quarterly_preface": 1,
    "regional_results": 1,
    "city_plain": 2,
    "district_intro": 2,
    "layer_intro": 1,
    "key_area": 1,
    "subsidence_forecast": 1,
    "conclusion": 1,
    "annex_intro": 1,
}


def title_level_for_slot(slot_id: str) -> int | None:
    """命中白名单返回 1/2；分区正文、附表细表等返回 None（事件不带该字段）。"""
    sid = (slot_id or "").strip()
    if not sid:
        return None
    if sid in _TITLE_LEVEL_BY_SLOT_ID:
        return _TITLE_LEVEL_BY_SLOT_ID[sid]
    if sid.startswith("layer_results__"):
        return 2
    return None


def chapter_start_payload(
    *,
    slot_id: str,
    title: str,
    kind: str,
    slot_index: int,
) -> dict[str, Any]:
    """组装 analysis_agent_chapter_start；仅白名单附带 title_level。"""
    ev: dict[str, Any] = {
        "event": "analysis_agent_chapter_start",
        "slot_id": slot_id,
        "chapter_id": slot_id,
        "slot_index": slot_index,
        "chapter_index": slot_index,
        "kind": kind,
        "title": title,
    }
    level = title_level_for_slot(slot_id)
    if level is not None:
        ev["title_level"] = level
    return ev
