"""
§3.2 指代类型工程清单（封闭枚举）。

扩展类型时须先更新文档 §3.2 与本枚举，再调整 yaml / 规则 / P3 schema。
"""

from __future__ import annotations

from enum import StrEnum


class AnaphoraType(StrEnum):
    NONE = "none"
    META_CONFIRM = "meta_confirm"
    PAIR_COMPARE = "pair_compare"
    ORDINAL = "ordinal"
    SINGLE_ENTITY = "single_entity"
    ELLIPSIS = "ellipsis"
    CONTINUATION = "continuation"


ANAPHORA_TYPE_CODES: frozenset[str] = frozenset({m.value for m in AnaphoraType})
