"""NL2SQL 业务语义层：问句对齐到指标、监测类型与维度码。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.core.logging import get_logger
from app.nl2sql.nl2sql_business_profile import get_nl2sql_business_profile
from app.nl2sql.question_scope_models import QuestionIntent

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class MetricBinding:
    id: str
    name: str
    unit: str
    grain: str
    definition_ref: str
    confidence: float
    preferred_tables: tuple[str, ...]
    preferred_columns: tuple[str, ...]
    time_column: str


@dataclass
class SemanticBinding:
    semantic_version: str
    metrics: list[MetricBinding] = field(default_factory=list)
    device_types: list[str] = field(default_factory=list)
    device_type_tables: list[str] = field(default_factory=list)
    district_codes: list[str] = field(default_factory=list)
    station_ids: list[str] = field(default_factory=list)
    station_names: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    default_table: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.semantic_version,
            "metrics": [
                {
                    "id": m.id,
                    "name": m.name,
                    "unit": m.unit,
                    "grain": m.grain,
                    "definition_ref": m.definition_ref,
                    "confidence": m.confidence,
                    "preferred_tables": list(m.preferred_tables),
                    "preferred_columns": list(m.preferred_columns),
                    "time_column": m.time_column,
                }
                for m in self.metrics
            ],
            "dimensions": {
                "device_types": list(self.device_types),
                "device_type_tables": list(self.device_type_tables),
                "district_codes": list(self.district_codes),
                "station_ids": list(self.station_ids),
                "station_names": list(self.station_names),
            },
            "default_table": self.default_table,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class SemanticAssets:
    version: str
    metrics: tuple[dict[str, Any], ...]
    metric_synonyms: dict[str, str]
    forbidden_pairs: frozenset[tuple[str, str]]
    device_type_aliases: dict[str, str]
    device_type_tables: dict[str, str]
    default_subsidence_table: str
    districts: tuple[str, ...]
    stations: tuple[dict[str, Any], ...]


def _resolve_semantic_root() -> Path | None:
    profile = get_nl2sql_business_profile()
    if profile is None:
        return None
    override = (Path(__file__).resolve().parents[2] / profile.semantic_dict_path).resolve()
    if override.is_dir():
        return override
    return None


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=2)
def load_semantic_assets(root: str) -> SemanticAssets | None:
    base = Path(root)
    if not base.is_dir():
        return None

    manifest = _load_yaml(base / "manifest.yaml")
    metrics_raw = _load_yaml(base / "metrics.yaml")
    synonyms_raw = _load_yaml(base / "synonyms.yaml")

    version = str(manifest.get("version") or metrics_raw.get("version") or "unknown")
    metrics = tuple(metrics_raw.get("metrics") or [])

    metric_synonyms: dict[str, str] = {}
    for m in metrics:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or "")
        if not mid:
            continue
        metric_synonyms[mid] = mid
        metric_synonyms[mid.lower()] = mid
        for syn in m.get("synonyms") or []:
            if syn:
                metric_synonyms[str(syn).strip().lower()] = mid

    forbidden: set[tuple[str, str]] = set()
    for pair in synonyms_raw.get("forbidden_pairs") or []:
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            forbidden.add((str(pair[0]), str(pair[1])))
            forbidden.add((str(pair[1]), str(pair[0])))

    device_aliases = dict(synonyms_raw.get("device_type_aliases") or {})
    device_tables = dict(synonyms_raw.get("device_type_tables") or {})
    default_table = str(synonyms_raw.get("default_subsidence_table") or "t_data_wash_fcb")

    districts: list[str] = []
    district_yaml = _load_yaml(base / "dimensions" / "district.yaml")
    for ent in district_yaml.get("entries") or []:
        if isinstance(ent, dict) and ent.get("name"):
            districts.append(str(ent["name"]))
        elif isinstance(ent, str):
            districts.append(ent)

    stations: list[dict[str, Any]] = []
    station_yaml = _load_yaml(base / "dimensions" / "station.yaml")
    for ent in station_yaml.get("entries") or []:
        if isinstance(ent, dict):
            stations.append(ent)

    return SemanticAssets(
        version=version,
        metrics=metrics,
        metric_synonyms=metric_synonyms,
        forbidden_pairs=frozenset(forbidden),
        device_type_aliases={str(k): str(v) for k, v in device_aliases.items()},
        device_type_tables={str(k): str(v) for k, v in device_tables.items()},
        default_subsidence_table=default_table,
        districts=tuple(districts),
        stations=tuple(stations),
    )


def _longest_match(text: str, candidates: dict[str, str]) -> tuple[str, str] | None:
    hits: list[tuple[int, str, str]] = []
    for phrase, canonical in candidates.items():
        if not phrase or phrase not in text:
            continue
        hits.append((len(phrase), phrase, canonical))
    if not hits:
        return None
    hits.sort(key=lambda x: (-x[0], x[1]))
    return hits[0][1], hits[0][2]


def _match_metrics(question: str, assets: SemanticAssets) -> list[tuple[str, float]]:
    q = (question or "").lower()
    found: dict[str, float] = {}
    for phrase, mid in assets.metric_synonyms.items():
        if phrase and phrase in q:
            found[mid] = max(found.get(mid, 0.0), min(1.0, len(phrase) / max(len(q), 1)))
    return sorted(found.items(), key=lambda x: (-x[1], x[0]))


def _metric_def(assets: SemanticAssets, metric_id: str) -> dict[str, Any] | None:
    for m in assets.metrics:
        if isinstance(m, dict) and str(m.get("id")) == metric_id:
            return m
    return None


def align_semantics(
    question: str,
    intent: QuestionIntent,
    *,
    assets: SemanticAssets | None = None,
) -> SemanticBinding | None:
    root = _resolve_semantic_root()
    if root is None:
        return None
    if assets is None:
        assets = load_semantic_assets(str(root))
    if assets is None:
        return None

    q = (question or "").strip()
    q_lower = q.lower()
    binding = SemanticBinding(semantic_version=assets.version)
    binding.default_table = assets.default_subsidence_table

    metric_hits = _match_metrics(q, assets)
    metric_ids = [mid for mid, _ in metric_hits]

    for mid, conf in metric_hits:
        mdef = _metric_def(assets, mid)
        if not mdef:
            continue
        binding.metrics.append(
            MetricBinding(
                id=mid,
                name=str(mdef.get("name") or mid),
                unit=str(mdef.get("unit") or ""),
                grain=str(mdef.get("grain") or ""),
                definition_ref=str(mdef.get("formula_note") or mdef.get("name") or mid),
                confidence=conf,
                preferred_tables=tuple(str(t) for t in (mdef.get("preferred_tables") or [])),
                preferred_columns=tuple(str(c) for c in (mdef.get("preferred_columns") or [])),
                time_column=str(mdef.get("time_column") or "data_time"),
            )
        )

    if len(metric_ids) >= 2:
        for i, a in enumerate(metric_ids):
            for b in metric_ids[i + 1:]:
                if (a, b) in assets.forbidden_pairs:
                    binding.warnings.append(f"metric_forbidden_mix:{a}+{b}")

    # device type
    device_alias_map = {str(k).lower(): str(v) for k, v in assets.device_type_aliases.items()}
    for phrase, dtype in sorted(device_alias_map.items(), key=lambda x: len(x[0]), reverse=True):
        if phrase in q_lower:
            binding.device_types.append(dtype)
            tbl = assets.device_type_tables.get(dtype)
            if tbl:
                binding.device_type_tables.append(tbl)
            break

    if not binding.device_types and any(
        w in q_lower for w in ("沉降", "下沉", "回弹", "监测点")
    ):
        binding.device_types.append("fcb")
        binding.device_type_tables.append(assets.default_subsidence_table)

    # districts
    for dist in sorted(assets.districts, key=len, reverse=True):
        if dist in q:
            binding.district_codes.append(dist)

    # stations from semantic asset + scope intent
    scope = intent.scope
    if scope.station_id:
        binding.station_ids.append(scope.station_id)
    if scope.station_name:
        binding.station_names.append(scope.station_name)
    for ent in assets.stations:
        name = str(ent.get("name") or ent.get("station_name") or "")
        if name and name in q:
            binding.station_names.append(name)
            sid = ent.get("station_id") or ent.get("id")
            if sid:
                binding.station_ids.append(str(sid))

    if re.search(r"第\s*\d+\s*层", q):
        binding.warnings.append("layered_fcb_placeholder:分层各层数据尚未入库")

    return binding


def semantic_version_fingerprint() -> str:
    """语义资产版本号，供 NL2SQL 缓存 policy_fp 防跨版本脏命中。"""
    profile = get_nl2sql_business_profile()
    if profile is None or not profile.semantic_link_enabled:
        return ""
    root = _resolve_semantic_root()
    if root is None:
        return ""
    assets = load_semantic_assets(str(root))
    return (assets.version if assets else "") or ""


def clear_semantic_assets_cache() -> None:
    load_semantic_assets.cache_clear()

