"""加载 libraries.yaml，并与 device_type.yaml / scope_lexicon 交叉校验。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.core.config import get_app_config
from app.core.logging import get_logger

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEVICE_TYPE_YAML = (
    _REPO_ROOT / "configs/nl2sql_business/subsidence/semantic/dimensions/device_type.yaml"
)
_SCOPE_LEXICON = _REPO_ROOT / "configs/nl2sql_business/subsidence/scope_lexicon.json"


class CatalogError(RuntimeError):
    """库注册表非法或与 device_type 不一致。"""


@dataclass(frozen=True)
class LibraryColumn:
    """列表列：key 给前端，aliases 对齐 NL2SQL 返回列名。"""

    key: str
    title: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class LibraryCoreMetric:
    """HUD 核心指标：id 为展示字段，source 为库表物理列。"""

    id: str
    name: str
    unit: str
    source: str


@dataclass(frozen=True)
class LibrarySeriesMetric:
    """HUD 时序度量；气象等可配置多列，组装为 series_list。"""

    id: str
    name: str
    unit: str


@dataclass(frozen=True)
class LibraryDef:
    """一份监测库：library_id ↔ 物理表，与树节点 / HITL 选项同源。"""

    id: str
    display_name: str
    table: str
    device_type: str
    hud_supported: bool
    core_metric: str
    series_column: str
    series_unit: str
    synonyms: tuple[str, ...]
    columns: tuple[LibraryColumn, ...]
    core_metrics: tuple[LibraryCoreMetric, ...]
    group_id: str | None = None
    group_title: str | None = None
    annual_key: str | None = None
    annual_source: str | None = None
    series_metrics: tuple[LibrarySeriesMetric, ...] = ()
    hud_title_template: str = "{entity} · {display_name}"


@dataclass(frozen=True)
class LibraryGroup:
    id: str
    title: str
    library_ids: tuple[str, ...]


@dataclass
class LibraryCatalog:
    version: str
    default_library_id: str
    hitl_prompt: str
    generic_settle_words: tuple[str, ...]
    groups: tuple[LibraryGroup, ...]
    libraries: tuple[LibraryDef, ...]
    by_id: dict[str, LibraryDef] = field(default_factory=dict)
    phrases: tuple[tuple[str, str], ...] = ()

    def get(self, library_id: str | None) -> LibraryDef | None:
        if not library_id:
            return None
        return self.by_id.get(str(library_id).strip().lower())

    def library_options(self, *, candidates: list[str] | None = None) -> list[dict[str, str]]:
        """HITL 下拉与 GET /libraries 共用；candidates 仅作 suggested 高亮。"""
        highlight = {str(x).lower() for x in (candidates or [])}
        out: list[dict[str, str]] = []
        for lib in self.libraries:
            item = {
                "id": lib.id,
                "display_name": lib.display_name,
                "group": lib.group_title or "",
                "table": lib.table,
            }
            if highlight:
                item["suggested"] = "true" if lib.id in highlight else "false"
            out.append(item)
        return out

    def public_payload(self) -> dict[str, Any]:
        """公开给前端的七库注册表（不含内部列映射细节）。"""
        return {
            "version": self.version,
            "default_library_id": self.default_library_id,
            "hitl_prompt": self.hitl_prompt,
            "groups": [
                {"id": g.id, "title": g.title, "libraries": list(g.library_ids)} for g in self.groups
            ],
            "libraries": [
                {
                    "id": lib.id,
                    "display_name": lib.display_name,
                    "table": lib.table,
                    "device_type": lib.device_type,
                    "hud_supported": lib.hud_supported,
                    "group": lib.group_title or "",
                    "synonyms": list(lib.synonyms),
                }
                for lib in self.libraries
            ],
            "library_options": self.library_options(),
        }


def _repo_path(raw: str) -> Path:
    p = Path(raw)
    if p.is_absolute():
        return p
    return (_REPO_ROOT / p).resolve()


def _load_device_type_tables() -> dict[str, str]:
    if not _DEVICE_TYPE_YAML.is_file():
        raise CatalogError(f"device_type.yaml missing: {_DEVICE_TYPE_YAML}")
    data = yaml.safe_load(_DEVICE_TYPE_YAML.read_text(encoding="utf-8")) or {}
    out: dict[str, str] = {}
    for ent in data.get("entries") or []:
        if not isinstance(ent, dict):
            continue
        lid = str(ent.get("id") or "").strip().lower()
        tbl = str(ent.get("table") or "").strip().lower()
        if lid and tbl:
            out[lid] = tbl
    return out


def _load_lexicon_phrases() -> dict[str, list[str]]:
    if not _SCOPE_LEXICON.is_file():
        return {}
    data = json.loads(_SCOPE_LEXICON.read_text(encoding="utf-8"))
    mapping = data.get("device_types") or {}
    by_id: dict[str, list[str]] = {}
    if isinstance(mapping, dict):
        for phrase, lid in mapping.items():
            key = str(lid or "").strip().lower()
            p = str(phrase or "").strip()
            if key and p:
                by_id.setdefault(key, []).append(p)
    return by_id


def load_library_catalog(*, path: str | None = None) -> LibraryCatalog:
    """加载 libraries.yaml，并强制与 device_type.yaml 的 id/表名一致。"""
    cfg = get_app_config().data_query_agent
    file_path = _repo_path(path or cfg.libraries_file)
    if not file_path.is_file():
        raise CatalogError(f"libraries.yaml missing: {file_path}")
    raw = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    device_tables = _load_device_type_tables()
    lexicon = _load_lexicon_phrases()

    groups_raw = raw.get("groups") or []
    group_title_by_lib: dict[str, tuple[str, str]] = {}
    groups: list[LibraryGroup] = []
    for g in groups_raw:
        if not isinstance(g, dict):
            continue
        gid = str(g.get("id") or "").strip()
        title = str(g.get("title") or gid)
        lids = tuple(str(x).strip().lower() for x in (g.get("libraries") or []) if str(x).strip())
        groups.append(LibraryGroup(id=gid, title=title, library_ids=lids))
        for lid in lids:
            group_title_by_lib[lid] = (gid, title)

    libraries: list[LibraryDef] = []
    phrases: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in raw.get("libraries") or []:
        if not isinstance(item, dict):
            continue
        lid = str(item.get("id") or "").strip().lower()
        table = str(item.get("table") or "").strip().lower()
        if not lid or not table:
            raise CatalogError("library entry missing id or table")
        if lid in seen:
            raise CatalogError(f"duplicate library_id={lid}")
        # 查询台 library_id 必须能落到 NL2SQL 语义层同一张 wash 表。
        expected = device_tables.get(lid)
        if expected is None:
            raise CatalogError(f"library_id={lid} not in device_type.yaml")
        if expected != table:
            raise CatalogError(
                f"library_id={lid} table={table} != device_type.yaml table={expected}"
            )
        seen.add(lid)
        syn = [str(s).strip() for s in (item.get("synonyms") or []) if str(s).strip()]
        for extra in lexicon.get(lid) or []:
            if extra not in syn:
                syn.append(extra)
        display = str(item.get("display_name") or lid)
        for p in [lid, display, table, *syn]:
            if p:
                phrases.append((p, lid))
        cols: list[LibraryColumn] = []
        for c in item.get("columns") or []:
            if not isinstance(c, dict):
                continue
            key = str(c.get("key") or "").strip()
            if not key:
                continue
            aliases = tuple(str(a).strip() for a in (c.get("aliases") or [key]) if str(a).strip())
            cols.append(LibraryColumn(key=key, title=str(c.get("title") or key), aliases=aliases))
        metrics: list[LibraryCoreMetric] = []
        for m in item.get("core_metrics") or []:
            if not isinstance(m, dict):
                continue
            mid = str(m.get("id") or "").strip()
            if not mid:
                continue
            metrics.append(
                LibraryCoreMetric(
                    id=mid,
                    name=str(m.get("name") or mid),
                    unit=str(m.get("unit") or ""),
                    source=str(m.get("source") or mid),
                )
            )
        annual_raw = item.get("annual_metric") if isinstance(item.get("annual_metric"), dict) else {}
        annual_key = str(annual_raw.get("key") or "").strip() or None
        annual_source = str(annual_raw.get("source") or "").strip() or None
        series_col = str(item.get("series_column") or item.get("core_metric") or "").strip()
        series_unit = str(item.get("series_unit") or "")
        series_metrics: list[LibrarySeriesMetric] = []
        for sm in item.get("series_columns") or []:
            if not isinstance(sm, dict):
                continue
            sid = str(sm.get("id") or "").strip()
            if not sid:
                continue
            series_metrics.append(
                LibrarySeriesMetric(
                    id=sid,
                    name=str(sm.get("name") or sid),
                    unit=str(sm.get("unit") or series_unit),
                )
            )
        if not series_metrics and series_col:
            series_metrics.append(
                LibrarySeriesMetric(id=series_col, name=series_col, unit=series_unit)
            )
        title_tmpl = str(item.get("hud_title_template") or "{entity} · {display_name}").strip()
        gmeta = group_title_by_lib.get(lid, (None, None))
        libraries.append(
            LibraryDef(
                id=lid,
                display_name=display,
                table=table,
                device_type=str(item.get("device_type") or lid).strip().lower(),
                hud_supported=bool(item.get("hud_supported", True)),
                core_metric=str(item.get("core_metric") or "").strip(),
                series_column=series_col,
                series_unit=series_unit,
                synonyms=tuple(syn),
                columns=tuple(cols),
                core_metrics=tuple(metrics),
                group_id=gmeta[0],
                group_title=gmeta[1],
                annual_key=annual_key,
                annual_source=annual_source,
                series_metrics=tuple(series_metrics),
                hud_title_template=title_tmpl or "{entity} · {display_name}",
            )
        )

    default_id = str(raw.get("default_library_id") or "fcb").strip().lower()
    if default_id not in seen:
        raise CatalogError(f"default_library_id={default_id} not in libraries")

    # 长短语优先，避免「分层标」被「分层」抢先命中。
    phrases.sort(key=lambda x: len(x[0]), reverse=True)
    catalog = LibraryCatalog(
        version=str(raw.get("version") or ""),
        default_library_id=default_id,
        hitl_prompt=str(raw.get("hitl_prompt") or "未能确定监测库，请选择要查询的数据类型。"),
        generic_settle_words=tuple(
            str(w) for w in (raw.get("generic_settle_words") or ["沉降", "下沉", "监测点"]) if str(w)
        ),
        groups=tuple(groups),
        libraries=tuple(libraries),
        by_id={lib.id: lib for lib in libraries},
        phrases=tuple(phrases),
    )
    logger.info(
        "data_query_agent catalog loaded version=%s libraries=%d",
        catalog.version,
        len(catalog.libraries),
    )
    return catalog


@lru_cache(maxsize=1)
def get_library_catalog() -> LibraryCatalog:
    return load_library_catalog()


def clear_library_catalog_cache() -> None:
    get_library_catalog.cache_clear()
