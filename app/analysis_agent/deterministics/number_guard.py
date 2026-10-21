"""叙述数字白名单：LLM 正文里的阿拉伯数字必须能在事实/硬稿中找到对应值。"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

# 12.50mm / 100.00% / 92 → 抽出 12.50、100.00、92
_NUM_RE = re.compile(r"(?<![\d.])(\d+\.\d+|\d+)(?![\d.])")


def extract_number_values(text: str) -> set[Decimal]:
    found: set[Decimal] = set()
    for raw in _NUM_RE.findall(text or ""):
        try:
            found.add(Decimal(raw))
        except InvalidOperation:
            continue
    return found


def collect_allowed_numbers(*texts: str) -> set[Decimal]:
    allowed: set[Decimal] = set()
    for text in texts:
        if text:
            allowed |= extract_number_values(text)
    return allowed


def extra_numbers(text: str, allowed: set[Decimal]) -> set[Decimal]:
    return extract_number_values(text) - set(allowed)


def narrative_numbers_allowed(text: str, allowed: set[Decimal]) -> bool:
    return not extra_numbers(text, allowed)
