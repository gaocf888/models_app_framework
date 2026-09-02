"""意图 2：行政区 / 站点 / 时间（不改写库）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.data_query_agent.annual import resolve_annual_window
from app.data_query_agent.catalog import LibraryDef
from app.nl2sql.question_intent import resolve_question_intent
from app.nl2sql.question_intent_display import question_intent_to_dict
from app.nl2sql.scope_parser_subsidence import parse_scope_subsidence


_DISTRICT_GRAIN_RE = re.compile(r"各区|按区|按行政区|行政区.{0,8}平均")
_CITY_GRAIN_RE = re.compile(r"全市|全北京")
_AGG_RE = re.compile(r"平均|汇总|合计")
_SUBSIDENCE_LEXICON = (
    Path(__file__).resolve().parents[2]
    / "configs/nl2sql_business/subsidence/scope_lexicon.json"
)


@dataclass
class ScopeIntentResult:
    """意图 2：区/站/时间；grain 决定列表与 HUD 实体，不改写 library。"""

    confirmed_scope: dict[str, Any]
    parse_mode: str
    grain: str
    time_snapshot: dict[str, Any]
    scope_snapshot: dict[str, Any]
    lithology_warning: bool = False
    annual_window: dict[str, str] | None = None
    warnings: list[str] | None = None


def infer_result_grain(query: str) -> str:
    """计划侧粒度：全市汇总 > 各区 > 默认站点。"""
    q = query or ""
    if _CITY_GRAIN_RE.search(q) and _AGG_RE.search(q):
        return "city"
    if _DISTRICT_GRAIN_RE.search(q) or ("各区" in q and _AGG_RE.search(q)) or "各区平均" in q:
        return "district"
    if "各区" in q:
        return "district"
    return "station"


def resolve_scope_intent(
    query: str,
    library: LibraryDef,
    *,
    district: str | None = None,
    station_id: str | None = None,
) -> ScopeIntentResult:
    """解析区/站/时间；device_type 只来自已锁定的库。

    P1-3：请求显式 district/station_id 优先于问句解析（覆盖，非 AND）。
    """
    intent = resolve_question_intent(query)
    payload = question_intent_to_dict(intent)
    # 查询台固定地降词表：不依赖进程 NL2SQL_BUSINESS_DOMAIN，避免锅炉域把区/站吃掉。
    sub = parse_scope_subsidence(
        query,
        lexicon_file=str(_SUBSIDENCE_LEXICON) if _SUBSIDENCE_LEXICON.is_file() else None,
    )
    scope = payload.get("scope") or {}
    confirmed = {
        "device_type": library.device_type,
        "district": sub.district or scope.get("district") or None,
        "station_id": sub.station_id or scope.get("station_id") or None,
        "station_name": sub.station_name or scope.get("station_name") or None,
    }
    # 库表无岩性/层位列：只记 warning，不 HITL、不编造。
    lithology = bool(re.search(r"岩性|优势层|第[一二三四五六七八九十\d]+层", query or ""))
    grain = infer_result_grain(query)
    annual_window = resolve_annual_window(query)
    warnings: list[str] = []
    req_district = (district or "").strip() or None
    req_station = (station_id or "").strip() or None
    if req_district:
        nl_d = confirmed.get("district")
        if nl_d and str(nl_d) != req_district:
            warnings.append("scope_nl_overridden")
        confirmed["district"] = req_district
    if req_station:
        nl_s = confirmed.get("station_id") or confirmed.get("station_name")
        if nl_s and str(nl_s) != req_station:
            warnings.append("scope_nl_overridden")
        confirmed["station_id"] = req_station
    time_snapshot = {
        "time_window_tag": payload.get("time_window_tag"),
        "time_window": payload.get("time_window"),
        "statistical_time_range": payload.get("statistical_time_range"),
        "annual_window": annual_window,
    }
    scope_snapshot = {
        "district": confirmed["district"],
        "station_id": confirmed["station_id"],
        "station_name": confirmed["station_name"],
        "device_type": library.device_type,
    }
    return ScopeIntentResult(
        confirmed_scope=confirmed,
        parse_mode=str(payload.get("parse_mode") or "rule"),
        grain=grain,
        time_snapshot=time_snapshot,
        scope_snapshot=scope_snapshot,
        lithology_warning=lithology,
        annual_window=annual_window,
        warnings=warnings or None,
    )
