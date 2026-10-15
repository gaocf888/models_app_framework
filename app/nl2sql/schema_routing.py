"""
沉降 / 运维库 schema 路由。

约定（与业务库设计一致）：
- 准实时沉降：文档中的 ``default`` → PostgreSQL 真实 schema ``public``
- 人工校核沉降：同构表白名单，独立 schema（未就绪时可留空，启用时用配置名）
- 运维中间库：``om_result``（``t_om_*``；与沉降白名单分列，按问句意图收窄）

取数互斥：``realtime`` 与 ``calibrated`` 二选一；运维固定 ``om_result``（与沉降开关无关）。
沉降会话 search_path 仅沉降 schema；运维表执行前 qualify 为 ``om_result.t_om_*``。
"""
from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from typing import Any, Literal

logger = logging.getLogger(__name__)

SettlementSource = Literal["realtime", "calibrated"]

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_ident(name: str, *, label: str) -> str:
    s = (name or "").strip()
    if not s or not _IDENT_RE.match(s):
        raise ValueError(f"非法 schema 名 ({label}): {name!r}")
    return s


def _env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def _profile_schemas() -> dict[str, Any]:
    try:
        from app.nl2sql.nl2sql_business_profile import get_nl2sql_business_profile

        p = get_nl2sql_business_profile()
        if p is None:
            return {}
        schemas = getattr(p, "schemas", None)
        return dict(schemas) if isinstance(schemas, dict) else {}
    except Exception:
        return {}


@lru_cache(maxsize=1)
def get_settlement_data_source() -> SettlementSource:
    """当前沉降取数源：realtime（准实时）| calibrated（人工校核）。"""
    raw = _env("SUBSIDENCE_DATA_SOURCE") or str(
        _profile_schemas().get("settlement_data_source") or "realtime"
    )
    src = raw.strip().lower()
    if src in ("realtime", "default", "public"):
        return "realtime"
    if src in ("calibrated", "checked", "manual"):
        return "calibrated"
    logger.warning("未知 SUBSIDENCE_DATA_SOURCE=%r，回退 realtime", raw)
    return "realtime"


@lru_cache(maxsize=1)
def get_settlement_schema() -> str:
    """
    当前生效的沉降 schema（互斥）。

    - realtime → ``SUBSIDENCE_SCHEMA_REALTIME`` / profile，默认 ``public``
      （业务文档里的 default 即此 schema）
    - calibrated → ``SUBSIDENCE_SCHEMA_CALIBRATED`` / profile；未配置则报错
    """
    schemas = _profile_schemas()
    src = get_settlement_data_source()
    if src == "realtime":
        name = (
            _env("SUBSIDENCE_SCHEMA_REALTIME")
            or str(schemas.get("settlement_realtime") or "")
            or "public"
        )
        return _safe_ident(name, label="settlement_realtime")
    name = _env("SUBSIDENCE_SCHEMA_CALIBRATED") or str(
        schemas.get("settlement_calibrated") or ""
    )
    if not name:
        raise ValueError(
            "已选择 calibrated 沉降源，但未配置 SUBSIDENCE_SCHEMA_CALIBRATED "
            "/ profile.schemas.settlement_calibrated"
        )
    return _safe_ident(name, label="settlement_calibrated")


@lru_cache(maxsize=1)
def get_om_result_schema() -> str:
    """运维中间库 schema，默认 om_result（与沉降白名单分列，按问句意图收窄）。"""
    schemas = _profile_schemas()
    name = (
        _env("OM_RESULT_SCHEMA")
        or str(schemas.get("om_result") or "")
        or "om_result"
    )
    return _safe_ident(name, label="om_result")


def get_postgres_search_path(*, include_om_result: bool = False) -> str:
    """
    供 ``SET LOCAL search_path TO ...`` 使用的 schema 列表（逗号分隔）。

    默认**仅**当前沉降 schema，避免裸表名被 om_result 同名表误解析。
    运维表须显式 ``om_result.t_om_*``（``include_om_result=True`` 仅供显式运维会话）。
    """
    parts = [get_settlement_schema()]
    if include_om_result:
        om = get_om_result_schema()
        if om not in parts:
            parts.append(om)
    return ", ".join(parts)


def is_om_result_logical_table(table: str) -> bool:
    """运维中间库逻辑表名约定：``t_om_*``。"""
    return (table or "").strip().lower().startswith("t_om_")


def settlement_data_source_label_zh() -> str:
    """给用户/话术用的取数口径短名。"""
    if get_settlement_data_source() == "calibrated":
        return "人工校核沉降数据"
    return "准实时沉降数据"


def settlement_data_source_prompt_hint() -> str:
    """
    注入 NL2SQL Prompt 的沉降口径说明（运维问句另见 om_intent.ops_snapshot_prompt_hint）。
    """
    label = settlement_data_source_label_zh()
    schema = get_settlement_schema()
    src = get_settlement_data_source()
    om = get_om_result_schema()
    return (
        f"【数据口径】沉降取数为「{label}」"
        f"（SUBSIDENCE_DATA_SOURCE={src}，schema={schema}）；"
        f"运维中间库固定 schema={om}（与沉降开关无关）。"
        "沉降问句用 t_data_wash_* / t_station（逻辑表名，search_path 落到沉降 schema）；"
        "运维问句用 om_result.t_om_* 且必须按 snapshot_time 过滤最新批次；"
        "禁止在同一条 SQL 中混查沉降事实表与运维表；勿自行切换沉降 schema。"
    )


def validate_schema_table_pair(schema: str, table: str) -> str | None:
    """
    校验 ``schema.table`` 组合；合法返回 None，否则返回错误文案。

    - active 沉降 schema：禁止 ``t_om_*``
    - om_result：仅允许 ``t_om_*``
    - 其它 schema：拒绝
    """
    sch = (schema or "").strip().lower()
    tab = (table or "").strip().lower()
    if not sch or not tab:
        return "empty schema or table"
    try:
        settlement = get_settlement_schema().lower()
        om = get_om_result_schema().lower()
    except ValueError as e:
        return str(e)
    if sch == settlement:
        if is_om_result_logical_table(tab):
            return (
                f"settlement schema {settlement!r} cannot reference om_result table {tab!r}"
            )
        return None
    if sch == om:
        if not is_om_result_logical_table(tab):
            return (
                f"om_result schema cannot reference settlement table {tab!r}"
            )
        return None
    return f"unknown schemas: {sch}"


def should_apply_subsidence_search_path() -> bool:
    """仅当 NL2SQL 业务域为 subsidence 且 DB 为 PostgreSQL 时注入 search_path。"""
    domain = (_env("NL2SQL_BUSINESS_DOMAIN") or "").strip().lower()
    if domain != "subsidence":
        return False
    try:
        from app.core.config import get_app_config

        dialect = (getattr(get_app_config().db, "dialect", None) or "").strip().lower()
    except Exception:
        dialect = (_env("NL2SQL_SQL_DIALECT") or "tidb").strip().lower()
    return dialect in {"postgres", "postgresql", "pg"} or dialect.startswith("postgres")


def qualify_settlement_table(table: str) -> str:
    """逻辑表名 → ``{active_settlement_schema}.{table}``（标识符已校验）。"""
    bare = (table or "").strip()
    if not bare or not _IDENT_RE.match(bare):
        raise ValueError(f"非法表名: {table!r}")
    return f"{get_settlement_schema()}.{bare}"


def qualify_om_result_table(table: str) -> str:
    """运维表名 → ``{om_result_schema}.{table}``。"""
    bare = (table or "").strip()
    if not bare or not _IDENT_RE.match(bare):
        raise ValueError(f"非法表名: {table!r}")
    return f"{get_om_result_schema()}.{bare}"


def allowed_sql_schemas() -> frozenset[str]:
    """
    SQL 中允许出现的 schema 前缀。

    沉降 active + om_result；禁止模型 invent 其它 schema。
    """
    schemas = {get_settlement_schema(), get_om_result_schema()}
    return frozenset(schemas)


def clear_schema_routing_cache() -> None:
    get_settlement_data_source.cache_clear()
    get_settlement_schema.cache_clear()
    get_om_result_schema.cache_clear()


def schema_routing_summary() -> dict[str, Any]:
    """调试 / 健康检查用快照。"""
    try:
        settlement = get_settlement_schema()
    except ValueError as e:
        settlement = f"<error:{e}>"
    domain = _env("NL2SQL_BUSINESS_DOMAIN") or None
    try:
        from app.core.config import get_app_config

        dialect = getattr(get_app_config().db, "dialect", None)
    except Exception:
        dialect = _env("NL2SQL_SQL_DIALECT") or None
    return {
        "domain": domain,
        "dialect": dialect,
        "settlement_data_source": get_settlement_data_source(),
        "settlement_schema": settlement,
        "om_result_schema": get_om_result_schema(),
        "search_path": (
            get_postgres_search_path()
            if should_apply_subsidence_search_path()
            else None
        ),
        "apply_search_path": should_apply_subsidence_search_path(),
    }
