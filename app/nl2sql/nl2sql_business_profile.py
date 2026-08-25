"""NL2SQL 部署级业务配置包加载（``configs/nl2sql_business/<domain>/``）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.core.logging import get_logger

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_BUSINESS_ROOT = _REPO_ROOT / "configs" / "nl2sql_business"

_VALID_DOMAINS = frozenset({"boiler_four_tube", "subsidence"})


@dataclass(frozen=True)
class NL2SQLBusinessProfile:
    business_domain: str
    display_name: str = ""
    semantic_dict_path: str = ""
    table_allowlist: tuple[str, ...] = ()
    join_whitelist: tuple[str, ...] = ()
    scope_lexicon_file: str | None = None
    entity_rules_file: str | None = None
    prompt_default_version: str = "v2"
    sql_dialect: str = "tidb"
    # 业务库连接默认（密码禁止写入配置包，仅 DB_PASSWORD / DB_URL）
    db_host: str | None = None
    db_port: int | None = None
    db_name: str | None = None
    db_user: str | None = None
    db_async_driver: str | None = None
    semantic_link_enabled: bool = False
    schema_link_catalog_mode: str = "linked_only"
    on_link_failure: str = "refuse"
    inject_parsed_intent: bool = False
    intent_parse_mode: str = "rule"
    scope_sql_rewrite_enabled: bool = True
    reject_unresolved_time_placeholders: bool = True
    anchor_fallback_now_enabled: bool = True
    anchor_fallback_analysis_types: str = ""
    profile_path: str = ""

    def allowlist_version_fp(self) -> str:
        return "|".join(sorted(self.table_allowlist))

    def is_postgres(self) -> bool:
        return self.sql_dialect in {"postgres", "postgresql", "pg"}

    def resolved_async_driver(self) -> str:
        if self.db_async_driver:
            return self.db_async_driver.strip()
        if self.is_postgres():
            return "postgresql+asyncpg"
        return "mysql+aiomysql"


def _resolve_path(raw: str) -> Path:
    p = Path(raw)
    if p.is_absolute():
        return p
    return (_REPO_ROOT / raw).resolve()


def _read_lines_file(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        return ()
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        lines.append(s)
    return tuple(lines)


def _read_join_whitelist(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        return ()
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return tuple(out)


def _load_profile_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=4)
def get_nl2sql_business_profile(domain: str | None = None) -> NL2SQLBusinessProfile | None:
    """
    加载当前部署 domain 对应的 NL2SQL 业务配置包。

    未设置 ``NL2SQL_BUSINESS_DOMAIN`` 或 domain 无效时返回 ``None``（保持现网锅炉默认行为）。
    """
    dom = (domain or os.getenv("NL2SQL_BUSINESS_DOMAIN") or "").strip().lower()
    if not dom or dom not in _VALID_DOMAINS:
        return None

    profile_dir = _DEFAULT_BUSINESS_ROOT / dom
    profile_yaml = profile_dir / "profile.yaml"
    raw = _load_profile_yaml(profile_yaml)

    tables_file = raw.get("tables", {}) if isinstance(raw.get("tables"), dict) else {}
    allowlist_rel = str(tables_file.get("allowlist_file") or f"configs/nl2sql_business/{dom}/table_scope.txt")
    join_rel = str(tables_file.get("join_whitelist_file") or f"configs/nl2sql_business/{dom}/join_whitelist.txt")

    nl2sql_raw = raw.get("nl2sql", {}) if isinstance(raw.get("nl2sql"), dict) else {}

    semantic_path = str(
        nl2sql_raw.get("semantic_dict_path") or f"configs/nl2sql_business/{dom}/semantic"
    )
    scope_lex = nl2sql_raw.get("scope_lexicon_file")
    entity_rules = nl2sql_raw.get("entity_rules_file")

    db_raw = raw.get("db", {}) if isinstance(raw.get("db"), dict) else {}
    dialect = str(db_raw.get("dialect") or nl2sql_raw.get("sql_dialect") or "tidb").strip().lower()

    db_port_raw = db_raw.get("port")
    db_port: int | None = None
    if db_port_raw is not None and str(db_port_raw).strip():
        try:
            db_port = int(db_port_raw)
        except (TypeError, ValueError):
            db_port = None

    db_host = str(db_raw.get("host") or "").strip() or None
    db_name = str(db_raw.get("name") or db_raw.get("database") or "").strip() or None
    db_user = str(db_raw.get("user") or "").strip() or None
    db_async_driver = str(db_raw.get("async_driver") or "").strip() or None

    anchor_types = nl2sql_raw.get("anchor_fallback_analysis_types")
    if anchor_types is None:
        if dom == "subsidence":
            anchor_types = ""
        else:
            anchor_types = "img_diag_leakage_burst,img_diag_defect_ident"

    entity_rules_resolved: str | None
    if entity_rules is None or str(entity_rules).strip() == "":
        # 锅炉可显式留空（沿用 env / 无规则）；地降默认包内文件
        if entity_rules is not None and str(entity_rules).strip() == "":
            entity_rules_resolved = None
        else:
            entity_rules_resolved = f"configs/nl2sql_business/{dom}/entity_rules.json"
    else:
        entity_rules_resolved = str(entity_rules)

    profile = NL2SQLBusinessProfile(
        business_domain=dom,
        display_name=str(raw.get("display_name") or dom),
        semantic_dict_path=semantic_path,
        table_allowlist=_read_lines_file(_resolve_path(allowlist_rel)),
        join_whitelist=_read_join_whitelist(_resolve_path(join_rel)),
        scope_lexicon_file=str(scope_lex) if scope_lex else f"configs/nl2sql_business/{dom}/scope_lexicon.json",
        entity_rules_file=entity_rules_resolved,
        prompt_default_version=str(nl2sql_raw.get("prompt_default_version") or "v2"),
        sql_dialect=dialect,
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
        db_user=db_user,
        db_async_driver=db_async_driver,
        semantic_link_enabled=bool(nl2sql_raw.get("semantic_link_enabled", False)),
        schema_link_catalog_mode=str(
            nl2sql_raw.get("schema_link_catalog_mode") or "linked_only"
        ).strip().lower(),
        on_link_failure=str(nl2sql_raw.get("on_link_failure") or "refuse").strip().lower(),
        inject_parsed_intent=bool(nl2sql_raw.get("inject_parsed_intent", False)),
        intent_parse_mode=str(nl2sql_raw.get("intent_parse_mode") or "rule").strip().lower(),
        scope_sql_rewrite_enabled=bool(nl2sql_raw.get("scope_sql_rewrite_enabled", True)),
        reject_unresolved_time_placeholders=bool(
            nl2sql_raw.get("reject_unresolved_time_placeholders", True)
        ),
        anchor_fallback_now_enabled=bool(nl2sql_raw.get("anchor_fallback_now_enabled", dom != "subsidence")),
        anchor_fallback_analysis_types=str(anchor_types or ""),
        profile_path=str(profile_yaml),
    )
    logger.info(
        "NL2SQL business profile loaded domain=%s tables=%d joins=%d semantic=%s",
        dom,
        len(profile.table_allowlist),
        len(profile.join_whitelist),
        profile.semantic_dict_path,
    )
    return profile


def get_business_domain() -> str | None:
    profile = get_nl2sql_business_profile()
    return profile.business_domain if profile else None


def clear_nl2sql_business_profile_cache() -> None:
    get_nl2sql_business_profile.cache_clear()

