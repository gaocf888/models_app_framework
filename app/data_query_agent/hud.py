"""P2-2：Java 默认表按实体补拉 HUD（解析路径仍一组 payload，不走本接口）。"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.data_query_agent.acquire import acquire_data
from app.data_query_agent.annual import resolve_annual_window
from app.data_query_agent.assemble import CITY_ENTITY_ID, assemble_entity_hud
from app.data_query_agent.catalog import CatalogError, get_library_catalog
from app.data_query_agent.scope_intent import ScopeIntentResult
from app.services.nl2sql_service import NL2SQLService

logger = get_logger(__name__)

HUD_ENTITY_TYPES = ("station", "district", "city")
_CITY_ALIASES = {
    CITY_ENTITY_ID: CITY_ENTITY_ID,
    "北京": CITY_ENTITY_ID,
    "全市": CITY_ENTITY_ID,
    "全北京": CITY_ENTITY_ID,
}
DEFAULT_HUD_USER_ID = "dqa-hud"
DEFAULT_HUD_SESSION_ID = "dqa-hud-default"


class HudRequestError(ValueError):
    """GET /hud 入参或库配置不合法。"""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def normalize_hud_entity(entity_type: str, entity_id: str) -> tuple[str, str]:
    """校验并规范化 entity_type / entity_id；市实体固定 beijing。"""
    et = (entity_type or "").strip().lower()
    eid = (entity_id or "").strip()
    if et not in HUD_ENTITY_TYPES:
        if et == "layer":
            raise HudRequestError(
                400,
                "hud_layer_not_enabled",
                "层位 HUD 未启用（无层位表）",
            )
        raise HudRequestError(422, "invalid_entity_type", "entity_type 须为 station、district 或 city")
    if not eid:
        raise HudRequestError(422, "invalid_entity_id", "entity_id 不能为空")
    if et == "city":
        alias_key = eid.lower() if eid.isascii() else eid
        mapped = _CITY_ALIASES.get(alias_key) or _CITY_ALIASES.get(eid)
        if mapped is None:
            raise HudRequestError(
                422,
                "invalid_city_entity_id",
                f"全市实体 id 须为 {CITY_ENTITY_ID}",
            )
        eid = mapped
    return et, eid


def _scope_for_hud(library, entity_type: str, entity_id: str) -> tuple[ScopeIntentResult, str]:
    """按实体构造意图 2；grain 由 entity_type 决定，不从问句推断。"""
    annual = resolve_annual_window("")
    confirmed: dict[str, Any] = {
        "device_type": library.device_type,
        "district": None,
        "station_id": None,
        "station_name": None,
    }
    if entity_type == "station":
        confirmed["station_id"] = entity_id
        grain = "station"
        query = f"查询监测点 {entity_id} 的时序"
    elif entity_type == "district":
        confirmed["district"] = entity_id
        grain = "district"
        query = f"各区平均，仅{entity_id}"
    else:
        grain = "city"
        query = "全市平均"
    snapshot = {
        "district": confirmed["district"],
        "station_id": confirmed["station_id"],
        "station_name": confirmed["station_name"],
        "device_type": library.device_type,
    }
    scope = ScopeIntentResult(
        confirmed_scope=confirmed,
        parse_mode="hud",
        grain=grain,
        time_snapshot={
            "time_window_tag": annual.get("tag") or "",
            "time_window": None,
            "statistical_time_range": None,
            "annual_window": annual,
        },
        scope_snapshot=snapshot,
        annual_window=annual,
        warnings=None,
    )
    return scope, query


async def fetch_entity_hud(
    *,
    nl2sql: NL2SQLService,
    library_id: str,
    entity_type: str,
    entity_id: str,
    user_id: str | None = None,
    session_id: str | None = None,
    expose_sql: bool = False,
) -> dict[str, Any]:
    """锁单库表取 q_list + q_hud_series，返回与解析路径相同的单块 HUD。"""
    et, eid = normalize_hud_entity(entity_type, entity_id)
    lid = (library_id or "").strip().lower()
    if not lid:
        raise HudRequestError(422, "library_id_required", "library_id 不能为空")
    try:
        catalog = get_library_catalog()
    except CatalogError as exc:
        raise HudRequestError(503, "catalog_unavailable", str(exc)) from exc
    library = catalog.get(lid)
    if library is None:
        raise HudRequestError(404, "library_not_found", f"未知 library_id={lid}")
    if not library.hud_supported:
        raise HudRequestError(400, "hud_not_supported", f"库 {lid} 不支持 HUD")
    cfg = get_app_config().data_query_agent
    if et == "city" and not bool(getattr(cfg, "hud_city_enabled", True)):
        raise HudRequestError(400, "hud_city_disabled", "全市 HUD 已关闭")

    scope, query = _scope_for_hud(library, et, eid)
    uid = (user_id or "").strip() or DEFAULT_HUD_USER_ID
    sid = (session_id or "").strip() or DEFAULT_HUD_SESSION_ID
    request_id = uuid.uuid4().hex
    logger.info(
        "data_query_agent hud fetch request_id=%s library=%s entity_type=%s entity_id=%s",
        request_id,
        library.id,
        et,
        eid,
    )
    acquire = await acquire_data(
        nl2sql=nl2sql,
        user_id=uid,
        session_id=sid,
        request_id=request_id,
        query=query,
        library=library,
        scope=scope,
        include_hud=True,
        max_rows=max(1, int(cfg.default_max_rows)),
    )
    list_fail = not acquire.list_item.ok
    series_fail = acquire.series_item is None or not acquire.series_item.ok
    if list_fail and series_fail:
        raise HudRequestError(
            502,
            acquire.error or "nl2sql_failed",
            acquire.list_item.error or acquire.error or "HUD 取数失败",
        )
    extra: list[str] = []
    if list_fail:
        extra.append("hud_list_failed")
    payload = assemble_entity_hud(
        library=library,
        grain=scope.grain,
        entity_id=eid,
        acquire=acquire,
        expose_sql=expose_sql,
        extra_warnings=extra,
        annual_window=scope.annual_window,
    )
    payload["request_id"] = request_id
    return payload
