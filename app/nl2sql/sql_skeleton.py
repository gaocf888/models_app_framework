"""
NL2SQL L1：时间相关 SQL 骨架（意图键 + 模板占位符 + 按新问题渲染）。

与 L2 叠加；意图键将「同类时间说法」折叠（相对日 <R>、本周/上周 <ISO_WEEK>、
本月/上月 <MONTH_* >、近 N 天 <ROLLING_N:N>），命中后按当前问句解析的时间语义渲染字面量 / DATE_SUB。
"""

from __future__ import annotations

import calendar
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal

from app.nl2sql.sql_cache import normalize_nl2sql_question, strip_plan_context_guide_suffix
from app.nl2sql.time_intent_display import extract_numeric_window

_DATE_SUB_RX = re.compile(
    r"DATE_SUB\s*\(\s*CURDATE\s*\(\s*\)\s*,\s*INTERVAL\s+(\d+)\s+DAY\s*\)",
    re.IGNORECASE,
)
_QUOTED_DT_RX = re.compile(r"'(?:\d{4}-\d{2}-\d{2})(?:[ T]\d{2}:\d{2}:\d{2})?'")

_REL_WORD_ORDER: tuple[tuple[str, int], ...] = (
    ("大前天", 3),
    ("前天", 2),
    ("昨日", 1),
    ("昨天", 1),
    ("今日", 0),
    ("今天", 0),
)

# normalize：先长词、避免「上个月」被「上月」截断
_INTENT_STATIC_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("上个月", "<MONTH_-1>"),
    ("本季度", "<QUARTER_0>"),
    ("上季度", "<QUARTER_-1>"),
    ("第一季度", "<QUARTER_1>"),
    ("第二季度", "<QUARTER_2>"),
    ("第三季度", "<QUARTER_3>"),
    ("第四季度", "<QUARTER_4>"),
    ("上半年", "<HALF_1>"),
    ("下半年", "<HALF_2>"),
    ("大前天", "<R>"),
    ("前天", "<R>"),
    ("昨天", "<R>"),
    ("昨日", "<R>"),
    ("今日", "<R>"),
    ("今天", "<R>"),
    ("本周", "<ISO_WEEK>"),
    ("这周", "<ISO_WEEK>"),
    ("上周", "<ISO_WEEK>"),
    ("本月", "<MONTH_0>"),
    ("上月", "<MONTH_-1>"),
    ("前年", "<YEAR_-2>"),
)


@dataclass(frozen=True)
class TimeIntent:
    """从问句解析出的时间语义（与 L1 键占位对应）。"""

    mode: Literal[
        "day", "iso_week", "month", "rolling", "rolling_year", "quarter", "half_year", "year_rel"
    ]
    day_off: int | None = None
    iso_which: Literal["this", "last"] | None = None
    month_rel: Literal[0, -1] | None = None
    rolling_n: int | None = None
    quarter_rel: Literal["this", "last"] | int | None = None
    half_which: Literal["first", "second"] | None = None
    year_rel: Literal[-2] | None = None


def normalize_nl2sql_question_intent(text: str) -> str:
    """
    折叠时间说法为稳定占位符，使同类意图在 L1 键上对齐。
    顺序：近 N 天/年正则 → 静态词表（含近义与长度序）。
    """
    s = normalize_nl2sql_question(text)
    s = re.sub(r"近\s*(\d+)\s*天", lambda m: f"<ROLLING_N:{m.group(1)}>", s)
    n_year = extract_numeric_window(s, ("年", "year", "years"))
    if n_year:
        s = re.sub(
            r"(?:近|最近|过去)\s*(?:[0-9]{1,3}|[一二两三四五六七八九十百]+)\s*年",
            f"<ROLLING_Y:{n_year}>",
            s,
            count=1,
        )
    else:
        s = re.sub(r"近\s*(\d+)\s*年", lambda m: f"<ROLLING_Y:{m.group(1)}>", s)
    for old, new in _INTENT_STATIC_REPLACEMENTS:
        s = s.replace(old, new)
    return s


def resolve_time_intent(question: str) -> TimeIntent | None:
    """
    从问句解析时间语义（首命中优先）。
    优先级：近 N 天 → 近 N 年 → 本周/上周 → 季度 → 半年 → 本月/上月 → 前年 → 相对日。
    """
    s = normalize_nl2sql_question(question)
    m = re.search(r"近\s*(\d+)\s*天", s)
    if m:
        try:
            n = int(m.group(1))
            if 1 <= n <= 366:
                return TimeIntent(mode="rolling", rolling_n=n)
        except ValueError:
            pass
    my = re.search(r"近\s*(\d+)\s*年", s)
    if not my:
        n_year = extract_numeric_window(question, ("年", "year", "years"))
        if n_year:
            return TimeIntent(mode="rolling_year", rolling_n=n_year)
    elif my:
        try:
            n = int(my.group(1))
            if 1 <= n <= 20:
                return TimeIntent(mode="rolling_year", rolling_n=n)
        except ValueError:
            pass
    if "本周" in s or "这周" in s:
        return TimeIntent(mode="iso_week", iso_which="this")
    if "上周" in s:
        return TimeIntent(mode="iso_week", iso_which="last")
    if "本季度" in s or "这个季度" in s:
        return TimeIntent(mode="quarter", quarter_rel="this")
    if "上季度" in s:
        return TimeIntent(mode="quarter", quarter_rel="last")
    for qn, token in ((1, "一"), (2, "二"), (3, "三"), (4, "四")):
        if f"第{token}季度" in s or f"第{qn}季度" in s:
            return TimeIntent(mode="quarter", quarter_rel=qn)
    if "上半年" in s:
        return TimeIntent(mode="half_year", half_which="first")
    if "下半年" in s:
        return TimeIntent(mode="half_year", half_which="second")
    if "本月" in s:
        return TimeIntent(mode="month", month_rel=0)
    if "上个月" in s or "上月" in s:
        return TimeIntent(mode="month", month_rel=-1)
    if "前年" in s:
        return TimeIntent(mode="year_rel", year_rel=-2)
    for word, off in _REL_WORD_ORDER:
        if word in s:
            return TimeIntent(mode="day", day_off=off)
    return None


def resolve_relative_day_offset(question: str) -> int | None:
    """兼容旧 API：仅相对日词。"""
    ti = resolve_time_intent(question)
    if ti is None or ti.mode != "day":
        return None
    return ti.day_off


def _iso_week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _bounds_iso_week(which: Literal["this", "last"], ref: date) -> tuple[date, date]:
    mon_this = _iso_week_monday(ref)
    start = mon_this if which == "this" else mon_this - timedelta(days=7)
    end = start + timedelta(days=6)
    return start, end


def _bounds_month(rel: Literal[0, -1], ref: date) -> tuple[date, date]:
    y, m = ref.year, ref.month
    if rel == -1:
        if m == 1:
            y, m = y - 1, 12
        else:
            m -= 1
    _, last_day = calendar.monthrange(y, m)
    start = date(y, m, 1)
    end = date(y, m, last_day)
    return start, end


def _bounds_rolling(n: int, ref: date) -> tuple[date, date]:
    """近 N 天：含当天共 N 天 → [ref-(N-1), ref]。"""
    start = ref - timedelta(days=max(0, n - 1))
    return start, ref


def _parse_quoted_inner(orig: str) -> tuple[date | None, str]:
    inner = orig.strip()
    if inner.startswith("'") and inner.endswith("'"):
        inner = inner[1:-1]
    inner = inner.strip()
    day_part = inner.replace("T", " ")[:10]
    try:
        d = datetime.strptime(day_part, "%Y-%m-%d").date()
        return d, orig
    except ValueError:
        return None, orig


def _render_quoted_literal(orig_match: str, anchor: date) -> str:
    inner = orig_match.strip()
    if inner.startswith("'") and inner.endswith("'"):
        core = inner[1:-1]
    else:
        core = inner
    core = core.strip().replace("T", " ")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", core):
        return f"'{anchor.isoformat()}'"
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})", core)
    if m:
        return f"'{anchor.isoformat()} {m.group(2)}'"
    return orig_match


def _render_quoted_with_date(orig_match: str, d: date) -> str:
    return _render_quoted_literal(orig_match, d)


def _is_full_month_span(lo: date, hi: date) -> bool:
    if lo > hi:
        lo, hi = hi, lo
    if lo.day != 1 or lo.month != hi.month or lo.year != hi.year:
        return False
    _, last = calendar.monthrange(lo.year, lo.month)
    return hi.day == last


def _classify_quote_pair(lo: date, hi: date) -> Literal["same_day", "week", "month", "rolling"] | None:
    if lo > hi:
        lo, hi = hi, lo
    span = (hi - lo).days
    if span == 0:
        return "same_day"
    if 6 <= span <= 8:
        return "week"
    # 自然月整段优先于「滚动 N 天」（避免 28～31 天跨度被误判为 rolling）
    if _is_full_month_span(lo, hi):
        return "month"
    if 1 <= span <= 62:
        return "rolling"
    return None


def extract_time_skeleton_from_sql(sql: str) -> dict[str, Any] | None:
    """抽取时间骨架；支持 v1 单日形与 v2 周/月/滚动区间（保守拒绝不确定结构）。"""
    if not sql or not sql.strip():
        return None
    text = sql.strip()
    matches: list[tuple[int, int, str, re.Match[str]]] = []
    for m in _DATE_SUB_RX.finditer(text):
        matches.append((m.start(), m.end(), "sub", m))
    for m in _QUOTED_DT_RX.finditer(text):
        matches.append((m.start(), m.end(), "q", m))
    if not matches:
        return None
    matches.sort(key=lambda x: x[0])
    filtered: list[tuple[int, int, str, re.Match[str]]] = []
    last_end = -1
    for start, end, typ, m in matches:
        if start < last_end:
            continue
        filtered.append((start, end, typ, m))
        last_end = end

    subs: list[int] = []
    quoted_vals: list[tuple[str, date]] = []
    for _s, _e, typ, m in filtered:
        if typ == "sub":
            mm = _DATE_SUB_RX.search(m.group(0))
            if not mm:
                return None
            subs.append(int(mm.group(1)))
        else:
            d, lit = _parse_quoted_inner(m.group(0))
            if d is None:
                return None
            quoted_vals.append((lit, d))

    if len(set(subs)) > 1:
        return None

    slots = []
    # 仅 DATE_SUB、无字面量
    if not quoted_vals:
        sub_positions: list[int] = []
        for fi, (_s, _e, typ, m) in enumerate(filtered):
            if typ != "sub":
                continue
            mm = _DATE_SUB_RX.search(m.group(0))
            if not mm:
                return None
            sub_positions.append(fi)
            slots.append(
                {
                    "kind": "date_sub_curdate",
                    "orig": m.group(0),
                    "interval_n": int(mm.group(1)),
                }
            )
        buf = text
        for fi in sorted(sub_positions, reverse=True):
            start, end, _, _ = filtered[fi]
            sk_i = sub_positions.index(fi)
            token = f"__NL2SQL_SK_{sk_i}__"
            buf = buf[:start] + token + buf[end:]
        return {"version": 2, "pattern": buf, "slots": slots}

    uniq_dates = {d for _lit, d in quoted_vals}
    # 单日多字面量（同一日历日）
    if len(uniq_dates) == 1:
        for _s, _e, typ, m in filtered:
            if typ != "sub":
                slots.append({"kind": "quoted_literal", "orig": m.group(0)})
            else:
                mm = _DATE_SUB_RX.search(m.group(0))
                slots.append(
                    {
                        "kind": "date_sub_curdate",
                        "orig": m.group(0),
                        "interval_n": int(mm.group(1)) if mm else 0,
                    }
                )
        buf = text
        for i in range(len(filtered) - 1, -1, -1):
            start, end, _typ, _m = filtered[i]
            token = f"__NL2SQL_SK_{i}__"
            buf = buf[:start] + token + buf[end:]
        return {"version": 2, "pattern": buf, "slots": slots}

    # 两日字面量：跨自然日
    if len(quoted_vals) == 2 and not subs:
        lit_a, d_a = quoted_vals[0]
        lit_b, d_b = quoted_vals[1]
        shape = _classify_quote_pair(d_a, d_b)
        if shape is None:
            return None
        inclusive = (max(d_a, d_b) - min(d_a, d_b)).days + 1
        gid = 0
        slots = [
            {
                "kind": "quoted_pair_seg",
                "orig": lit_a,
                "shape": shape,
                "seg": "lo",
                "group": gid,
                "inclusive_days": inclusive,
            },
            {
                "kind": "quoted_pair_seg",
                "orig": lit_b,
                "shape": shape,
                "seg": "hi",
                "group": gid,
                "inclusive_days": inclusive,
            },
        ]
        buf = text
        q_positions = [(i, filtered[i]) for i in range(len(filtered)) if filtered[i][2] == "q"]
        if len(q_positions) != 2:
            return None
        i0, _ = q_positions[0]
        i1, _ = q_positions[1]
        # 先替换第二个 quoted，再第一个，避免索引错位
        for idx_pair in sorted([i0, i1], reverse=True):
            start, end, _, _ = filtered[idx_pair]
            token_idx = 0 if idx_pair == i0 else 1
            token = f"__NL2SQL_SK_{token_idx}__"
            buf = buf[:start] + token + buf[end:]
        slots_ordered = [slots[0], slots[1]]
        return {"version": 2, "pattern": buf, "slots": slots_ordered}

    # DATE_SUB + 字面量混合：仅允许单日字面量集且 subs 单一
    if subs and quoted_vals:
        if len(uniq_dates) != 1:
            return None
        for _s, _e, typ, m in filtered:
            if typ == "sub":
                mm = _DATE_SUB_RX.search(m.group(0))
                slots.append(
                    {
                        "kind": "date_sub_curdate",
                        "orig": m.group(0),
                        "interval_n": int(mm.group(1)) if mm else 0,
                    }
                )
            else:
                slots.append({"kind": "quoted_literal", "orig": m.group(0)})
        buf = text
        for i in range(len(filtered) - 1, -1, -1):
            start, end, _, _ = filtered[i]
            token = f"__NL2SQL_SK_{i}__"
            buf = buf[:start] + token + buf[end:]
        return {"version": 2, "pattern": buf, "slots": slots}

    return None


def _pair_bounds(shape: str, intent: TimeIntent, ref: date) -> tuple[date, date] | None:
    if shape == "same_day":
        if intent.mode != "day" or intent.day_off is None:
            return None
        d = ref - timedelta(days=intent.day_off)
        return d, d
    if shape == "week":
        if intent.mode != "iso_week" or intent.iso_which is None:
            return None
        return _bounds_iso_week(intent.iso_which, ref)
    if shape == "month":
        if intent.mode != "month" or intent.month_rel is None:
            return None
        return _bounds_month(intent.month_rel, ref)
    if shape == "rolling":
        if intent.mode != "rolling" or intent.rolling_n is None:
            return None
        return _bounds_rolling(intent.rolling_n, ref)
    return None


def render_sql_time_skeleton(payload: dict[str, Any], question: str) -> str | None:
    ver = payload.get("version")
    if ver == 1:
        return _render_v1(payload, question)
    if ver != 2:
        return None
    intent = resolve_time_intent(question)
    if intent is None:
        return None
    pattern = payload.get("pattern")
    slots = payload.get("slots")
    if not isinstance(pattern, str) or not isinstance(slots, list):
        return None
    ref = date.today()
    out = pattern
    pair_cache: dict[tuple[int, str], tuple[date, date]] = {}

    for i, slot in enumerate(slots):
        if not isinstance(slot, dict):
            return None
        kind = slot.get("kind")
        ph = f"__NL2SQL_SK_{i}__"
        if ph not in out:
            return None

        if kind == "date_sub_curdate":
            interval_n = int(slot.get("interval_n") or 0)
            orig = str(slot.get("orig") or "")
            if intent.mode == "day" and intent.day_off is not None:
                rep = f"DATE_SUB(CURDATE(), INTERVAL {intent.day_off} DAY)"
            elif intent.mode == "rolling" and intent.rolling_n is not None:
                # 近 N 天常见 INTERVAL N-1；若与缓存不一致则仍按意图重写
                rep = f"DATE_SUB(CURDATE(), INTERVAL {max(0, intent.rolling_n - 1)} DAY)"
                if interval_n and intent.rolling_n and interval_n != max(0, intent.rolling_n - 1):
                    # 允许 LLM 使用 INTERVAL N 表示「过去 N 天不含今天」等变体：优先对齐意图
                    pass
            else:
                return None
            out = out.replace(ph, rep, 1)
            continue

        if kind == "quoted_literal":
            if intent.mode != "day" or intent.day_off is None:
                return None
            anchor = ref - timedelta(days=intent.day_off)
            rep = _render_quoted_literal(str(slot.get("orig") or ""), anchor)
            out = out.replace(ph, rep, 1)
            continue

        if kind == "quoted_pair_seg":
            shape = str(slot.get("shape") or "")
            gid = int(slot.get("group") or 0)
            seg = str(slot.get("seg") or "")
            key = (gid, shape)
            if key not in pair_cache:
                b = _pair_bounds(shape, intent, ref)
                if b is None:
                    return None
                lo_d, hi_d = b
                inc = int(slot.get("inclusive_days") or 0)
                if shape == "rolling" and intent.mode == "rolling" and intent.rolling_n is not None:
                    if inc and inc != intent.rolling_n:
                        return None
                pair_cache[key] = (lo_d, hi_d)
            lo_d, hi_d = pair_cache[key]
            use = lo_d if seg == "lo" else hi_d
            rep = _render_quoted_with_date(str(slot.get("orig") or ""), use)
            out = out.replace(ph, rep, 1)
            continue

        return None

    if "__NL2SQL_SK_" in out:
        return None
    return out


def _render_v1(payload: dict[str, Any], question: str) -> str | None:
    pattern = payload.get("pattern")
    slots = payload.get("slots")
    if not isinstance(pattern, str) or not isinstance(slots, list):
        return None
    off = resolve_relative_day_offset(question)
    if off is None:
        return None
    anchor = date.today() - timedelta(days=off)
    out = pattern
    for i, slot in enumerate(slots):
        if not isinstance(slot, dict):
            return None
        kind = slot.get("kind")
        ph = f"__NL2SQL_SK_{i}__"
        if ph not in out:
            return None
        if kind == "date_sub_curdate":
            rep = f"DATE_SUB(CURDATE(), INTERVAL {off} DAY)"
        elif kind == "quoted_literal":
            rep = _render_quoted_literal(str(slot.get("orig") or ""), anchor)
        else:
            return None
        out = out.replace(ph, rep, 1)
    if "__NL2SQL_SK_" in out:
        return None
    return out


def skeleton_payload_to_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def skeleton_payload_from_json(raw: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def build_nl2sql_l1_cache_key(
    *,
    data_source_fp: str,
    analysis_type: str | None,
    plan_item_id: str | None,
    question: str,
    schema_fp: str,
    policy_fp: str,
) -> str:
    qn = normalize_nl2sql_question_intent(strip_plan_context_guide_suffix(question))
    raw = (
        f"{data_source_fp}\0{(analysis_type or '').strip()}\0{(plan_item_id or '').strip()}\0"
        f"{qn}\0{schema_fp}\0{policy_fp}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
