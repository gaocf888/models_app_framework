from __future__ import annotations

import re

from app.nl2sql.chain import NL2SQLChain
from app.nl2sql.question_scope_models import QuestionScopeIntent
from app.nl2sql.scope_lexicon import ScopeLexicon, get_scope_lexicon

_INT_TO_CN_DIGIT: dict[int, str] = {
    0: "零",
    1: "一",
    2: "二",
    3: "三",
    4: "四",
    5: "五",
    6: "六",
    7: "七",
    8: "八",
    9: "九",
}

_BOILER_UNIT_PREFIX_RE = re.compile(
    r"(?:"
    r"(?:\d+|[一二两三四五六七八九十百]+)号锅炉"
    r"|(?:\d+|[一二两三四五六七八九十百]+)号机组"
    r"|(?:\d+)#机组"
    r"|#(?:\d+)机组"
    r")"
)

_LAYER_DIRECTION_RE = re.compile(
    r"第\s*([0-9一二两三四五六七八九十百]+)\s*层\s*"
    r"(炉[前后左右]\s*向\s*炉[前后左右]\s*数)"
)
_DIRECTION_COUNT_RE = re.compile(r"(炉[前后左右]\s*向\s*炉[前后左右]\s*数)")
_LAYER_RE = re.compile(r"第\s*([0-9一二两三四五六七八九十百]+)\s*层(?!\s*炉)")
_SCREEN_RE = re.compile(r"第\s*([0-9一二两三四五六七八九十百]+)\s*屏")
_SCREEN_ALIAS_RE = re.compile(r"(前屏|后屏)")
_ROW_RE = re.compile(r"第\s*(\d+|[一二两三四五六七八九十百]+)\s*排")
_ROW_LINE_RE = re.compile(r"第\s*(\d+|[一二两三四五六七八九十百]+)\s*行")
_TUBE_RE = re.compile(r"第\s*(\d+|[一二两三四五六七八九十百]+)\s*(?:根|管)")


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").strip())


def _int_to_cn_ordinal(n: int) -> str:
    if n <= 0:
        return str(n)
    if n < 10:
        return _INT_TO_CN_DIGIT[n]
    if n == 10:
        return "十"
    if n < 20:
        return "十" + (_INT_TO_CN_DIGIT[n - 10] if n > 10 else "")
    tens, ones = divmod(n, 10)
    head = _INT_TO_CN_DIGIT[tens] + "十"
    if ones:
        head += _INT_TO_CN_DIGIT[ones]
    return head


def _format_layer_label(raw_index: str) -> str:
    n = NL2SQLChain._cn_unit_index_to_int(raw_index)
    if n is not None and n > 0:
        return f"第{_int_to_cn_ordinal(n)}层"
    return f"第{raw_index.strip()}层"


def _format_screen_label(raw_index: str) -> str:
    n = NL2SQLChain._cn_unit_index_to_int(raw_index)
    if n is not None and n > 0:
        return f"第{_int_to_cn_ordinal(n)}屏"
    return f"第{raw_index.strip()}屏"


def _parse_ordinal_int(raw: str) -> int | None:
    s = (raw or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    return NL2SQLChain._cn_unit_index_to_int(s)


def expand_abbreviations(text: str, abbreviations: dict[str, str]) -> str:
    out = text or ""
    for abbr in sorted(abbreviations, key=len, reverse=True):
        full = abbreviations[abbr]
        out = out.replace(abbr, full)
    return out


def _find_device(text: str, lexicon: ScopeLexicon) -> tuple[str | None, str | None]:
    """最长匹配设备名；返回 (canonical, matched_substring)。"""
    for dev in lexicon.devices_by_length:
        if dev in text:
            return lexicon.device_canonical.get(dev, dev), dev
    return None, None


def _strip_device_and_boiler_prefix(
    text: str,
    *,
    device_match: str | None,
    boiler: str | None,
) -> str:
    """设备命中后从工作副本剥离设备片段及锅炉/机组前缀，避免与管排/排数正则冲突。"""
    work = text
    if device_match:
        work = work.replace(device_match, "", 1)
    work = _BOILER_UNIT_PREFIX_RE.sub("", work, count=1)
    if boiler and boiler in work:
        work = work.replace(boiler, "", 1)
    return work


def _has_explicit_row_no(text: str) -> bool:
    return bool(_ROW_RE.search(text) or _ROW_LINE_RE.search(text))


def _extract_piperow_name(text: str, lexicon: ScopeLexicon) -> str | None:
    m = _LAYER_DIRECTION_RE.search(text)
    if m:
        layer = _format_layer_label(m.group(1))
        direction = _collapse_ws(m.group(2))
        return f"{layer}{direction}"

    m = _DIRECTION_COUNT_RE.search(text)
    if m:
        return _collapse_ws(m.group(1))

    m = _LAYER_RE.search(text)
    if m:
        return _format_layer_label(m.group(1))

    m = _SCREEN_RE.search(text)
    if m:
        return _format_screen_label(m.group(1))

    m = _SCREEN_ALIAS_RE.search(text)
    if m:
        alias = m.group(1)
        return lexicon.piperow_aliases.get(alias, alias)

    return None


def _extract_row_no(text: str) -> int | None:
    m = _ROW_RE.search(text) or _ROW_LINE_RE.search(text)
    if not m:
        return None
    n = _parse_ordinal_int(m.group(1))
    return n if n is not None and n > 0 else None


def _extract_tube_no(text: str) -> int | None:
    m = _TUBE_RE.search(text)
    if not m:
        return None
    n = _parse_ordinal_int(m.group(1))
    return n if n is not None and n > 0 else None


def _is_wall_device(device_name: str | None, lexicon: ScopeLexicon) -> bool:
    if not device_name:
        return False
    return any(marker in device_name for marker in lexicon.wall_row1_markers)


def parse_scope_rule(
    scope_question: str,
    *,
    lexicon: ScopeLexicon | None = None,
) -> QuestionScopeIntent:
    """
    从问句解析实体范围（程序规则）。
    调用方应传入经 ``_resolve_entity_scope_question`` 清洗后的 ``scope_question``。
    """
    lex = lexicon or get_scope_lexicon()
    q = (scope_question or "").strip()
    if not q:
        return QuestionScopeIntent()

    boiler = NL2SQLChain._extract_unit_keyword_from_question(q)
    expanded = expand_abbreviations(q, lex.abbreviations)
    device_name, device_match = _find_device(expanded, lex)
    work = _strip_device_and_boiler_prefix(
        expanded,
        device_match=device_match,
        boiler=boiler,
    )
    piperow_name = _extract_piperow_name(work, lex)
    row_no = _extract_row_no(work)
    tube_no = _extract_tube_no(work)

    if _is_wall_device(device_name, lex) and not _has_explicit_row_no(work):
        row_no = 1

    return QuestionScopeIntent(
        boiler=boiler,
        device_name=device_name,
        piperow_name=piperow_name,
        row_no=row_no,
        tube_no=tube_no,
    )
