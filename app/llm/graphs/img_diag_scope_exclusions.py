"""看图诊断 scope HITL：用户声明不解析的字段 → 强制 NULL。"""

from __future__ import annotations

import re
from typing import Any

SCOPE_EXCLUDABLE_FIELDS: frozenset[str] = frozenset(
    {"check_location_name", "row_no", "tube_no"}
)

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "row_no": ("排数", "管排", "第几排"),
    "tube_no": ("管数", "管号", "根管", "第几根"),
    "check_location_name": ("检测位置", "测厚位置", "点位"),
}

_EXCLUDE_VERB = re.compile(
    r"(?:去除|去掉|不要|无需|不解析|不填|不含|忽略|取消|删除|排除|不用|不需要|别解析|别填)"
)
_ONLY_BOILER_DEVICE = re.compile(
    r"(?:仅|只)(?:保留|解析|按|需要|查|看)?"
    r"(?:机组|锅炉).{0,16}(?:和|与|、|,)?(?:受热面|设备)"
    r"|(?:仅|只)(?:保留|解析|按|需要|查|看)?(?:受热面|设备)(?!.*(?:排|管|位置|点位))"
)


def detect_scope_field_exclusions_from_text(text: str) -> frozenset[str]:
    """从用户补充口语中识别需强制 NULL 的可选 scope 字段。"""
    t = (text or "").strip()
    if not t:
        return frozenset()

    if _ONLY_BOILER_DEVICE.search(t):
        return SCOPE_EXCLUDABLE_FIELDS

    excluded: set[str] = set()
    for field, aliases in _FIELD_ALIASES.items():
        for alias in aliases:
            idx = t.find(alias)
            if idx < 0:
                continue
            window = t[max(0, idx - 18) : idx + len(alias) + 4]
            if _EXCLUDE_VERB.search(window):
                excluded.add(field)
                break
    return frozenset(excluded)


def detect_scope_field_exclusions_from_patch(patch: dict[str, Any] | None) -> frozenset[str]:
    """scope_patch 中显式置空的字段视为用户排除。"""
    if not patch:
        return frozenset()
    excluded: set[str] = set()
    for field in SCOPE_EXCLUDABLE_FIELDS:
        if field not in patch:
            continue
        val = patch[field]
        if val is None or (isinstance(val, str) and not val.strip()):
            excluded.add(field)
    return frozenset(excluded)


def merge_scope_field_exclusions(
    existing: list[str] | None,
    new: frozenset[str] | set[str],
) -> list[str]:
    merged = set(existing or ()) | set(new)
    return sorted(merged & SCOPE_EXCLUDABLE_FIELDS)
