"""地降自动报告：上一完整周期与行政区解析（V2-P0）。

确定性 SQL / python 计划项只读 options._resolved，不再从 query 猜时间。
时区与现网 NL2SQL 的 CURRENT_DATE 对齐：使用运行环境的 date.today()。
半开区间 [t_start, t_end)。日期-only 的 end 视为次日 0 点。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from app.analysis_agent.slots.specs import is_subsidence_type

_CITY_ALIASES = frozenset({"", "全市", "北京市", "北京", "all", "全市平原"})
_REPORT_KIND = {
    "subsidence_daily": "日报",
    "subsidence_weekly": "周报",
    "subsidence_monthly": "月报",
    "subsidence_quarterly": "季度报告",
    "subsidence_yearly": "年报",
}
FALLBACK_MAX_STEPS = {
    "subsidence_daily": 60,
    "subsidence_weekly": 26,
    "subsidence_monthly": 24,
    "subsidence_quarterly": 8,
    "subsidence_yearly": 5,
}
_RESOLVED_CORE = frozenset(
    {"t_start", "t_end", "area", "period_label", "report_date", "issue_no", "report_kind"}
)


class PeriodValidationError(ValueError):
    """起止或行政区不合法。"""


@dataclass(frozen=True)
class ResolvedPeriod:
    t_start: str
    t_end: str
    area: str | None
    period_label: str
    report_date: str
    issue_no: str
    report_kind: str


def _district_yaml_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "configs"
        / "nl2sql_business"
        / "subsidence"
        / "semantic"
        / "dimensions"
        / "district.yaml"
    )


def load_district_names() -> tuple[str, ...]:
    path = _district_yaml_path()
    if not path.is_file():
        return (
            "东城区",
            "西城区",
            "朝阳区",
            "丰台区",
            "石景山区",
            "海淀区",
            "门头沟区",
            "房山区",
            "通州区",
            "顺义区",
            "昌平区",
            "大兴区",
            "怀柔区",
            "平谷区",
            "密云区",
            "延庆区",
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    names: list[str] = []
    for ent in raw.get("entries") or []:
        if isinstance(ent, dict) and ent.get("name"):
            names.append(str(ent["name"]).strip())
        elif isinstance(ent, str) and ent.strip():
            names.append(ent.strip())
    return tuple(names)


def _parse_datetime(raw: str, *, as_end_date: bool = False) -> datetime:
    s = (raw or "").strip()
    if not s:
        raise PeriodValidationError("empty_datetime")
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        d = date.fromisoformat(s)
        if as_end_date:
            d = d + timedelta(days=1)
        return datetime(d.year, d.month, d.day)
    text = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise PeriodValidationError(f"invalid_datetime:{raw}") from exc
    return dt.replace(tzinfo=None)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _quarter_start(d: date) -> date:
    q = (d.month - 1) // 3
    return date(d.year, q * 3 + 1, 1)


def label_for_complete_window(analysis_type: str, start: datetime, end: datetime) -> str:
    """同粒度完整周期的展示名（日/周/月/季/年）。"""
    at = (analysis_type or "").strip()
    sd = start.date()
    ed = end.date()
    if at == "subsidence_daily":
        return f"{sd.year}年{sd.month}月{sd.day}日"
    if at == "subsidence_weekly":
        last = ed - timedelta(days=1)
        return f"{sd.year}年{sd.month}月{sd.day}日—{last.month}月{last.day}日"
    if at == "subsidence_monthly":
        return f"{sd.year}年{sd.month}月"
    if at == "subsidence_quarterly":
        qn = (sd.month - 1) // 3 + 1
        cn = {1: "一", 2: "二", 3: "三", 4: "四"}.get(qn, str(qn))
        return f"{sd.year}年第{cn}季度"
    if at == "subsidence_yearly":
        return f"{sd.year}年"
    return f"{sd.year}年{sd.month}月{sd.day}日—{ed.year}年{ed.month}月{ed.day}日"


def complete_window_containing(analysis_type: str, instant: datetime) -> tuple[datetime, datetime]:
    """包含 instant 的同粒度完整半开窗。"""
    at = (analysis_type or "").strip()
    d = instant.date() if isinstance(instant, datetime) else instant
    if at == "subsidence_weekly":
        mon = _week_monday(d)
        start = datetime(mon.year, mon.month, mon.day)
        return start, start + timedelta(days=7)
    if at == "subsidence_monthly":
        start = datetime(d.year, d.month, 1)
        if d.month == 12:
            end = datetime(d.year + 1, 1, 1)
        else:
            end = datetime(d.year, d.month + 1, 1)
        return start, end
    if at == "subsidence_quarterly":
        qs = _quarter_start(d)
        start = datetime(qs.year, qs.month, qs.day)
        if qs.month == 10:
            end = datetime(qs.year + 1, 1, 1)
        else:
            end = datetime(qs.year, qs.month + 3, 1)
        return start, end
    if at == "subsidence_yearly":
        start = datetime(d.year, 1, 1)
        return start, datetime(d.year + 1, 1, 1)
    start = datetime(d.year, d.month, d.day)
    return start, start + timedelta(days=1)


def step_back_complete_window(
    analysis_type: str, start: datetime, end: datetime
) -> tuple[datetime, datetime]:
    """同一粒度往过去整步一个完整周期。"""
    at = (analysis_type or "").strip()
    d = start.date()
    if at == "subsidence_weekly":
        prev = d - timedelta(days=7)
        new_start = datetime(prev.year, prev.month, prev.day)
        return new_start, start
    if at == "subsidence_monthly":
        if d.month == 1:
            prev = date(d.year - 1, 12, 1)
        else:
            prev = date(d.year, d.month - 1, 1)
        new_start = datetime(prev.year, prev.month, 1)
        return new_start, start
    if at == "subsidence_quarterly":
        if d.month == 1:
            prev = date(d.year - 1, 10, 1)
        else:
            prev = date(d.year, d.month - 3, 1)
        new_start = datetime(prev.year, prev.month, 1)
        return new_start, start
    if at == "subsidence_yearly":
        new_start = datetime(d.year - 1, 1, 1)
        return new_start, start
    prev = d - timedelta(days=1)
    new_start = datetime(prev.year, prev.month, prev.day)
    return new_start, start


def complete_window_ending_on_or_before(
    analysis_type: str, *, before: datetime, instant: datetime
) -> tuple[datetime, datetime]:
    """包含 instant 的完整周期，且 t_end <= before（不与请求窗重叠）。"""
    start, end = complete_window_containing(analysis_type, instant)
    guard = 0
    while end > before and guard < 400:
        start, end = step_back_complete_window(analysis_type, start, end)
        guard += 1
    return start, end


def previous_complete_before(analysis_type: str, before: datetime) -> tuple[datetime, datetime]:
    """结束于 before 或更早的上一同粒度完整周期。"""
    instant = before - timedelta(seconds=1)
    return complete_window_ending_on_or_before(analysis_type, before=before, instant=instant)


def default_window(analysis_type: str, *, today: date) -> tuple[datetime, datetime, str]:
    """未传起止时的上一完整周期。"""
    at = (analysis_type or "").strip()
    if at == "subsidence_daily":
        start_d = today - timedelta(days=1)
        start = datetime(start_d.year, start_d.month, start_d.day)
        end = datetime(today.year, today.month, today.day)
        return start, end, label_for_complete_window(at, start, end)
    if at == "subsidence_weekly":
        this_mon = _week_monday(today)
        last_mon = this_mon - timedelta(days=7)
        start = datetime(last_mon.year, last_mon.month, last_mon.day)
        end = datetime(this_mon.year, this_mon.month, this_mon.day)
        return start, end, label_for_complete_window(at, start, end)
    if at == "subsidence_monthly":
        this_month = date(today.year, today.month, 1)
        if this_month.month == 1:
            prev = date(this_month.year - 1, 12, 1)
        else:
            prev = date(this_month.year, this_month.month - 1, 1)
        start = datetime(prev.year, prev.month, 1)
        end = datetime(this_month.year, this_month.month, 1)
        return start, end, label_for_complete_window(at, start, end)
    if at == "subsidence_quarterly":
        this_q = _quarter_start(today)
        if this_q.month == 1:
            prev_q = date(this_q.year - 1, 10, 1)
        else:
            prev_q = date(this_q.year, this_q.month - 3, 1)
        start = datetime(prev_q.year, prev_q.month, 1)
        end = datetime(this_q.year, this_q.month, 1)
        return start, end, label_for_complete_window(at, start, end)
    if at == "subsidence_yearly":
        start = datetime(today.year - 1, 1, 1)
        end = datetime(today.year, 1, 1)
        return start, end, label_for_complete_window(at, start, end)
    start = datetime(today.year, today.month, today.day)
    end = start + timedelta(days=1)
    return start, end, label_for_complete_window(at or "subsidence_daily", start, end)


def resolve_area(area: str | None) -> str | None:
    raw = (area or "").strip()
    if raw in _CITY_ALIASES:
        return None
    names = load_district_names()
    if raw in names:
        return raw
    if not raw.endswith("区"):
        candidate = raw + "区"
        if candidate in names:
            return candidate
    raise PeriodValidationError(f"invalid_area:{raw}")


def resolve_period(
    analysis_type: str,
    options: dict[str, Any] | None,
    *,
    today: date | None = None,
) -> ResolvedPeriod:
    opts = dict(options or {})
    existing = opts.get("_resolved")
    if isinstance(existing, dict) and existing.get("t_start") and existing.get("t_end"):
        return ResolvedPeriod(
            t_start=str(existing["t_start"]),
            t_end=str(existing["t_end"]),
            area=existing.get("area"),
            period_label=str(existing.get("period_label") or ""),
            report_date=str(existing.get("report_date") or ""),
            issue_no=str(existing.get("issue_no") or "XX"),
            report_kind=str(existing.get("report_kind") or _REPORT_KIND.get(analysis_type, "报告")),
        )

    start_raw = str(opts.get("start_time") or "").strip()
    end_raw = str(opts.get("end_time") or "").strip()
    if bool(start_raw) ^ bool(end_raw):
        raise PeriodValidationError("start_end_must_be_paired")

    day = today or date.today()
    if start_raw and end_raw:
        start_dt = _parse_datetime(start_raw, as_end_date=False)
        end_dt = _parse_datetime(end_raw, as_end_date=len(end_raw) == 10)
        if end_dt <= start_dt:
            raise PeriodValidationError("end_must_be_after_start")
        label = f"{start_dt.year}年{start_dt.month}月{start_dt.day}日—{end_dt.year}年{end_dt.month}月{end_dt.day}日"
    else:
        start_dt, end_dt, label = default_window(analysis_type, today=day)

    issue = str(opts.get("issue_no") or "").strip() or "XX"
    area = resolve_area(str(opts.get("area") or "") if opts.get("area") is not None else "")
    return ResolvedPeriod(
        t_start=_fmt(start_dt),
        t_end=_fmt(end_dt),
        area=area,
        period_label=label,
        report_date=f"{day.year}年{day.month}月{day.day}日",
        issue_no=issue,
        report_kind=_REPORT_KIND.get(analysis_type, "报告"),
    )


def apply_resolved_period(
    analysis_type: str,
    options: dict[str, Any] | None,
    *,
    today: date | None = None,
) -> dict[str, Any]:
    opts = dict(options or {})
    if not is_subsidence_type(analysis_type):
        return opts
    extra: dict[str, Any] = {}
    existing = opts.get("_resolved")
    if isinstance(existing, dict):
        extra = {k: v for k, v in existing.items() if k not in _RESOLVED_CORE}
    resolved = resolve_period(analysis_type, opts, today=today)
    data = asdict(resolved)
    data.update(extra)
    opts["_resolved"] = data
    return opts


def overlay_resolved_window(
    resolved: dict[str, Any],
    *,
    analysis_type: str,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    out = dict(resolved)
    out["t_start"] = _fmt(start)
    out["t_end"] = _fmt(end)
    out["period_label"] = label_for_complete_window(analysis_type, start, end)
    return out


def parse_resolved_dt(raw: Any) -> datetime:
    if isinstance(raw, datetime):
        return raw.replace(tzinfo=None)
    if isinstance(raw, date):
        return datetime(raw.year, raw.month, raw.day)
    return _parse_datetime(str(raw or ""), as_end_date=False)


def period_sse_dict(resolved: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(resolved or {})
    out: dict[str, Any] = {
        "t_start": data.get("t_start"),
        "t_end": data.get("t_end"),
        "period_label": data.get("period_label"),
        "area": data.get("area"),
    }
    if data.get("fallback"):
        out["fallback"] = True
        out["fallback_reason"] = data.get("fallback_reason") or "no_paired_data_in_requested_window"
        req_s = data.get("requested_t_start")
        req_e = data.get("requested_t_end")
        if req_s or req_e:
            out["requested"] = {"t_start": req_s, "t_end": req_e}
    elif data.get("fallback_reason"):
        out["fallback"] = False
        out["fallback_reason"] = data.get("fallback_reason")
    return out


def canonical_query(analysis_type: str, resolved: ResolvedPeriod | dict[str, Any]) -> str:
    data = asdict(resolved) if isinstance(resolved, ResolvedPeriod) else dict(resolved)
    area = str(data.get("area") or "全市")
    label = str(data.get("period_label") or "")
    kind = str(data.get("report_kind") or _REPORT_KIND.get(analysis_type, "报告"))
    return f"生成{area}{label}地面沉降监测{kind}"


def fill_template_placeholders(text: str, resolved: dict[str, Any], *, title: str = "", toc: str = "") -> str:
    body = text or ""
    repl = {
        "{title}": title,
        "{issue_no}": str(resolved.get("issue_no") or "XX"),
        "{period_label}": str(resolved.get("period_label") or ""),
        "{report_date}": str(resolved.get("report_date") or ""),
        "{toc}": toc,
    }
    for key, val in repl.items():
        if key == "{toc}" and not val:
            continue
        body = body.replace(key, val)
    return body
