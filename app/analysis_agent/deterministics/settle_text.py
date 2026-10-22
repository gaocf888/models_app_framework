"""地面沉降对外表述：表图保留有符号数，正文用绝对值 + 沉降/回弹。

计算仍是 Δ = 窗初 − 窗末。与季报模版一致：负值=沉降，正值=回弹。
对外正文禁止出现 Δ、公式和内部字段名。
"""

from __future__ import annotations

import re
from typing import Any

_FORMULA_PATTERNS = (
    re.compile(r"Δ\s*=\s*[^\n。；;]*"),
    re.compile(r"total_settle(?:\s*\([^)]*\))?"),
    re.compile(r"\bdelta_mm\b"),
    re.compile(r"窗初\s*[−\-－]\s*窗末"),
    re.compile(r"Δ"),
)


def is_sink(delta: float) -> bool:
    """有符号周期变化 <0 为沉降。"""
    return float(delta) < 0


def is_rebound(delta: float) -> bool:
    """有符号周期变化 >0 为回弹。"""
    return float(delta) > 0


def movement_word(delta: float) -> str:
    if is_sink(delta):
        return "沉降"
    if is_rebound(delta):
        return "回弹"
    return "持平"


def format_mm(val: float, *, signed: bool = False) -> str:
    """正文默认绝对值 + 毫米；表图用 signed=True 保留符号。"""
    n = float(val)
    if signed:
        return f"{n:.2f}"
    return f"{abs(n):.2f}毫米"


def format_mm_signed(val: float) -> str:
    return f"{float(val):.2f}"


def round_mm(val: Any) -> float | None:
    if val is None or str(val).strip() == "":
        return None
    try:
        return round(float(val), 2)
    except (TypeError, ValueError):
        return None


def strip_formula_text(text: str) -> str:
    out = text or ""
    for pat in _FORMULA_PATTERNS:
        out = pat.sub("", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()
