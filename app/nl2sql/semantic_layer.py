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

# 标编号形态：F8-10 / J8-1 等（用于区分站点名 vs 标编号）
_MARK_NAME_RE = re.compile(r"^[A-Za-z]+\d+(?:-\d+)?$")
# 压缩层 / 层间意图（grain C）
_LAYER_COMPRESS_RE = re.compile(r"压缩层|层间压缩|层间沉降|层间差|层压缩")
# 「第N压缩层」或「第N、M压缩层」（N 为压缩层序号，对应层位 N-1→N）
_COMPRESS_ORDINAL_RE = re.compile(
    r"第\s*(\d+)\s*(?:[、,，/和与]\s*(\d+)\s*)*(?:压缩层|层压缩)"
)
_COMPRESS_MULTI_NUM_RE = re.compile(r"第\s*((?:\d+\s*[、,，/和与]\s*)*\d+)\s*(?:压缩层|层压缩)")
_LAYER_PAIR_RE = re.compile(
    r"层位\s*(\d+)\s*(?:与|和|到|至|-|—|→)\s*(?:层位\s*)?(\d+)"
)
_SHALLOW_FIRST_COMPRESS_RE = re.compile(r"浅部.*?(?:第一|第\s*1)\s*压缩|第一压缩层")
# 「第N层」占位（无「压缩」时易与标序号混淆，不走 C）
_LAYER_PLACEHOLDER_RE = re.compile(r"第\s*\d+\s*层(?!压缩)")



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
    # 监测站点展示名（= DB project_name，如 F8(周村)）；勿直接当标编号过滤
    station_names: list[str] = field(default_factory=list)
    # 站点场地过滤：DB project_name
    project_names: list[str] = field(default_factory=list)
    # 分层标代表标 / 显式标编号 / 压缩层边界标：DB station_name
    preferred_station_names: list[str] = field(default_factory=list)
    # 压缩层区间：[{project_name, layer_from, layer_to, mark_from, mark_to}]
    compress_pairs: list[dict[str, Any]] = field(default_factory=list)
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
                "project_names": list(self.project_names),
                "preferred_station_names": list(self.preferred_station_names),
                "compress_pairs": list(self.compress_pairs),
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
    # project_name → 层位0 标编号
    fcb_layer0_by_project: dict[str, str] = field(default_factory=dict)
    # 标编号 → project_name
    fcb_project_by_mark: dict[str, str] = field(default_factory=dict)
    # 全部已知标编号
    fcb_mark_names: frozenset[str] = field(default_factory=frozenset)
    # 全部层位0 标编号（稳定顺序）
    fcb_layer0_marks: tuple[str, ...] = ()
    # district_raw（xls 简称，可无「区」）→ 层位0 标列表
    fcb_layer0_by_district_raw: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # project_name → {monitor_layer: station_name}（仅非空层位）
    fcb_layers_by_project: dict[str, dict[int, str]] = field(default_factory=dict)


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


def _resolve_fcb_layer_map_path(base: Path) -> Path:
    """优先 profile.fcb_layer_map_file，否则 semantic/dimensions/fcb_layer_map.yaml。"""
    profile = get_nl2sql_business_profile()
    if profile is not None:
        raw = getattr(profile, "fcb_layer_map_file", None) or ""
        if str(raw).strip():
            p = Path(str(raw).strip())
            if not p.is_absolute():
                p = (_REPO_ROOT / p).resolve()
            if p.is_file():
                return p
    return base / "dimensions" / "fcb_layer_map.yaml"


def _parse_fcb_layer_map(
    data: dict[str, Any],
) -> tuple[
    dict[str, str],
    dict[str, str],
    frozenset[str],
    tuple[str, ...],
    dict[str, tuple[str, ...]],
    dict[str, dict[int, str]],
]:
    layer0_by_project: dict[str, str] = {}
    project_by_mark: dict[str, str] = {}
    marks: set[str] = set()
    layer0_marks: list[str] = []
    layer0_by_district: dict[str, list[str]] = {}
    layers_by_project: dict[str, dict[int, str]] = {}

    for ent in data.get("entries") or []:
        if not isinstance(ent, dict):
            continue
        project = str(ent.get("project_name") or "").strip()
        mark = str(ent.get("station_name") or "").strip()
        if not project or not mark:
            continue
        marks.add(mark)
        project_by_mark[mark] = project
        layer_raw = ent.get("monitor_layer")
        layer: int | None
        try:
            layer = None if layer_raw is None or layer_raw == "" else int(layer_raw)
        except (TypeError, ValueError):
            layer = None
        if layer is not None:
            layers_by_project.setdefault(project, {})[layer] = mark
        if layer == 0:
            layer0_by_project[project] = mark
            if mark not in layer0_marks:
                layer0_marks.append(mark)
            district_raw = str(ent.get("district_raw") or "").strip()
            if district_raw:
                layer0_by_district.setdefault(district_raw, [])
                if mark not in layer0_by_district[district_raw]:
                    layer0_by_district[district_raw].append(mark)

    return (
        layer0_by_project,
        project_by_mark,
        frozenset(marks),
        tuple(layer0_marks),
        {k: tuple(v) for k, v in layer0_by_district.items()},
        layers_by_project,
    )


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

    map_path = _resolve_fcb_layer_map_path(base)
    map_raw = _load_yaml(map_path)
    (
        layer0_by_project,
        project_by_mark,
        mark_names,
        layer0_marks,
        layer0_by_district,
        layers_by_project,
    ) = _parse_fcb_layer_map(map_raw)
    if layer0_by_project:
        map_ver = str(map_raw.get("version") or "").strip()
        if map_ver and map_ver not in version:
            version = f"{version}+fcb{map_ver}"
        logger.info(
            "fcb_layer_map loaded path=%s projects=%d layer0=%d marks=%d layered_projects=%d",
            map_path,
            len(layer0_by_project),
            len(layer0_marks),
            len(mark_names),
            len(layers_by_project),
        )

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
        fcb_layer0_by_project=layer0_by_project,
        fcb_project_by_mark=project_by_mark,
        fcb_mark_names=mark_names,
        fcb_layer0_marks=layer0_marks,
        fcb_layer0_by_district_raw=layer0_by_district,
        fcb_layers_by_project=layers_by_project,
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


def _normalize_forced_tables(forced_tables: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in forced_tables or []:
        t = str(raw or "").strip().lower()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _uniq_append(target: list[str], value: str) -> None:
    v = (value or "").strip()
    if v and v not in target:
        target.append(v)


def _looks_like_mark(name: str, assets: SemanticAssets) -> bool:
    n = (name or "").strip()
    if not n:
        return False
    if n in assets.fcb_mark_names:
        return True
    return bool(_MARK_NAME_RE.match(n)) and "-" in n



_STATION_TABLE = "t_station"

_STATION_CATALOG_RE = re.compile(
    r"(?:"
    r"(?:监测)?(?:站点|测站|监测点|监测站点).{0,12}(?:有哪些|有什么|哪些|列表|名录|清单|都有啥|都有哪些)"
    r"|(?:有哪些|有什么|哪些|列出|查询|查一下).{0,12}(?:监测)?(?:站点|测站|监测点|监测站点)"
    r"|(?:站点|测站|监测点).{0,6}(?:分布|布置).{0,6}(?:情况|如何|怎样|怎么)"
    r")",
    re.IGNORECASE,
)


def is_station_catalog_question(question: str) -> bool:
    """判断是否为「监测站点有哪些/列表」类维表清单问句。"""
    q = (question or "").strip()
    if not q:
        return False
    if re.search(r"(沉降量|累计沉降|位移量|压缩量)", q) and not re.search(
        r"(有哪些|哪些|列表|名录|清单)", q
    ):
        return False
    return bool(_STATION_CATALOG_RE.search(q))


def _is_fcb_station_grain(binding: SemanticBinding) -> bool:
    """站点/地面沉降类问句（分层标主表），需要层位0 代表标。"""
    if any(dt in {"gnss", "dxswj", "kxsylj", "qxz", "gq"} for dt in binding.device_types):
        # 非分层标主路径（显式其它监测类型）不注入
        if "fcb" not in binding.device_types and "jyb" not in binding.device_types:
            return False
    tables = {t.lower() for t in binding.device_type_tables}
    if binding.default_table:
        tables.add(binding.default_table.lower())
    for m in binding.metrics:
        tables.update(t.lower() for t in m.preferred_tables)
    if tables & {"t_data_wash_fcb", "t_data_wash_jyb"}:
        return True
    if "fcb" in binding.device_types or "jyb" in binding.device_types:
        return True
    return False


def _district_raw_keys(district: str) -> list[str]:
    d = (district or "").strip()
    if not d:
        return []
    keys = [d]
    if d.endswith("区") and len(d) > 1:
        keys.append(d[:-1])
    elif not d.endswith("区"):
        keys.append(d + "区")
    return keys


def _resolve_layer0_marks_for_district(assets: SemanticAssets, district: str) -> list[str]:
    out: list[str] = []
    for key in _district_raw_keys(district):
        for mark in assets.fcb_layer0_by_district_raw.get(key) or ():
            _uniq_append(out, mark)
    return out


def _collect_projects_from_binding(binding: SemanticBinding, assets: SemanticAssets) -> list[str]:
    projects: list[str] = []
    for name in list(binding.station_names) + list(binding.project_names):
        if not name:
            continue
        if name in assets.fcb_layers_by_project or name in assets.fcb_layer0_by_project:
            _uniq_append(projects, name)
        elif name in assets.fcb_project_by_mark:
            _uniq_append(projects, assets.fcb_project_by_mark[name])
        elif not _looks_like_mark(name, assets):
            _uniq_append(projects, name)
    for sid in binding.station_ids:
        sid_u = (sid or "").strip().upper()
        if not sid_u:
            continue
        for project in assets.fcb_layers_by_project or assets.fcb_layer0_by_project:
            if project.upper().startswith(sid_u + "(") or project.upper() == sid_u:
                _uniq_append(projects, project)
                break
    return projects


def _consecutive_layer_pairs(layers: dict[int, str]) -> list[tuple[int, int]]:
    keys = sorted(layers)
    return [(keys[i], keys[i + 1]) for i in range(len(keys) - 1) if keys[i + 1] == keys[i] + 1]


def _parse_compress_layer_intervals(question: str) -> list[tuple[int, int]]:
    """解析压缩层区间。『第N压缩层』→ (N-1, N)；『层位i与i+1』→ (i, i+1)。"""
    q = question or ""
    intervals: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()

    def add(a: int, b: int) -> None:
        lo, hi = (a, b) if a <= b else (b, a)
        if hi != lo + 1:
            return
        pair = (lo, hi)
        if pair not in seen:
            seen.add(pair)
            intervals.append(pair)

    if _SHALLOW_FIRST_COMPRESS_RE.search(q):
        add(0, 1)

    for m in _LAYER_PAIR_RE.finditer(q):
        add(int(m.group(1)), int(m.group(2)))

    for m in _COMPRESS_MULTI_NUM_RE.finditer(q):
        nums = [int(x) for x in re.findall(r"\d+", m.group(1) or "")]
        for n in nums:
            if n >= 1:
                add(n - 1, n)

    # 兼容「第1压缩层」被 MULTI 漏掉的情况
    if not intervals:
        for m in re.finditer(r"第\s*(\d+)\s*压缩层", q):
            n = int(m.group(1))
            if n >= 1:
                add(n - 1, n)

    return intervals


def _is_compress_intent(question: str, binding: SemanticBinding) -> bool:
    q = question or ""
    if _LAYER_COMPRESS_RE.search(q) or _COMPRESS_MULTI_NUM_RE.search(q) or _LAYER_PAIR_RE.search(q):
        return True
    if _SHALLOW_FIRST_COMPRESS_RE.search(q):
        return True
    if any(m.id == "layer_compress_mm" for m in binding.metrics):
        return True
    return False


def _inject_fcb_compress_pairs(
    question: str,
    binding: SemanticBinding,
    assets: SemanticAssets,
) -> bool:
    """
    压缩层 / 层间：注入相邻监测层位边界标。
    compress(i→i+1)=Δ(i)−Δ(i+1)；『第N压缩层』默认对应层位 (N-1, N)。
    返回是否已按压缩层路径处理（含仅 warning 的情况）。
    """
    if not assets.fcb_layers_by_project:
        return False
    if not _is_fcb_station_grain(binding):
        return False
    if not _is_compress_intent(question, binding):
        return False

    projects = _collect_projects_from_binding(binding, assets)
    intervals = _parse_compress_layer_intervals(question)

    if not projects:
        binding.warnings.append("fcb_compress_need_station:压缩层需指定站点或可解析的 project_name")
        return True

    if not intervals:
        # 未指明第几压缩层：对该站注入全部相邻层位对的边界标
        binding.warnings.append("fcb_compress_all_adjacent:未指定压缩层序号，注入该站全部相邻层位边界标")

    any_pair = False
    missing: list[str] = []
    for project in projects:
        layers = assets.fcb_layers_by_project.get(project) or {}
        if not layers:
            missing.append(f"{project}:无层位字典")
            _uniq_append(binding.project_names, project)
            continue
        _uniq_append(binding.project_names, project)
        use_intervals = intervals or _consecutive_layer_pairs(layers)
        for lo, hi in use_intervals:
            mark_lo = layers.get(lo)
            mark_hi = layers.get(hi)
            if not mark_lo or not mark_hi:
                missing.append(f"{project}:层位{lo}-{hi}不连续或缺失")
                continue
            # 仅当字典中确实相邻（或问句明确要求该对）
            if hi != lo + 1:
                continue
            if lo not in layers or hi not in layers:
                continue
            pair = {
                "project_name": project,
                "layer_from": lo,
                "layer_to": hi,
                "mark_from": mark_lo,
                "mark_to": mark_hi,
                "formula": f"compress({lo}→{hi})=Δ({mark_lo})−Δ({mark_hi})",
            }
            binding.compress_pairs.append(pair)
            _uniq_append(binding.preferred_station_names, mark_lo)
            _uniq_append(binding.preferred_station_names, mark_hi)
            any_pair = True

    if any_pair:
        binding.warnings.append("fcb_compress_boundary_marks")
    if missing:
        binding.warnings.append("fcb_compress_missing:" + ";".join(missing[:5]))
    if not any_pair and not missing:
        binding.warnings.append("fcb_compress_no_pair:未能解析可用相邻层位对")
    return True


def _inject_fcb_preferred_station_names(
    question: str,
    binding: SemanticBinding,
    assets: SemanticAssets,
) -> None:
    """站点沉降层位0 / 压缩层边界标注入。"""
    if not assets.fcb_layer0_by_project and not assets.fcb_layers_by_project:
        return
    if not _is_fcb_station_grain(binding):
        return

    # C：压缩层优先（不再注入层位0 单标）
    if _inject_fcb_compress_pairs(question, binding, assets):
        return

    if _LAYER_PLACEHOLDER_RE.search(question or ""):
        # 「第N层」无压缩语义：保留占位告警，不强制层位0
        return

    # 问句中显式点名的标编号优先保留（grain A）
    explicit_marks: list[str] = []
    for mark in assets.fcb_mark_names:
        if mark and mark in (question or ""):
            _uniq_append(explicit_marks, mark)
    if explicit_marks:
        for mark in explicit_marks:
            _uniq_append(binding.preferred_station_names, mark)
            project = assets.fcb_project_by_mark.get(mark)
            if project:
                _uniq_append(binding.project_names, project)
                _uniq_append(binding.station_names, project)
        binding.warnings.append("fcb_explicit_mark")
        return

    # B：已解析站点 → 层位0
    projects = _collect_projects_from_binding(binding, assets)
    for name in list(binding.station_names) + list(binding.project_names):
        if name and _looks_like_mark(name, assets) and name not in assets.fcb_layer0_by_project:
            if name not in assets.fcb_project_by_mark:
                _uniq_append(binding.preferred_station_names, name)

    injected = False
    for project in projects:
        mark = assets.fcb_layer0_by_project.get(project)
        if mark:
            _uniq_append(binding.project_names, project)
            _uniq_append(binding.preferred_station_names, mark)
            injected = True
        else:
            _uniq_append(binding.project_names, project)

    if injected:
        binding.warnings.append("fcb_layer0_preferred_station")
        return

    if binding.district_codes:
        marks: list[str] = []
        for dist in binding.district_codes:
            for m in _resolve_layer0_marks_for_district(assets, dist):
                _uniq_append(marks, m)
        if marks:
            for m in marks:
                _uniq_append(binding.preferred_station_names, m)
            binding.warnings.append("fcb_layer0_district_filter")
            return

    if assets.fcb_layer0_marks:
        for m in assets.fcb_layer0_marks:
            _uniq_append(binding.preferred_station_names, m)
        binding.warnings.append("fcb_layer0_city_filter")


def align_semantics(
    question: str,
    intent: QuestionIntent,
    *,
    assets: SemanticAssets | None = None,
    forced_tables: list[str] | None = None,
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
            for b in metric_ids[i + 1 :]:
                if (a, b) in assets.forbidden_pairs:
                    binding.warnings.append(f"metric_forbidden_mix:{a}+{b}")

    # device type：仅 forced_tables 非空时用 intent.device_type / 锁表覆盖问句别名与「沉降→fcb」
    forced = _normalize_forced_tables(forced_tables)
    if forced:
        dtype = str(intent.scope.device_type or "").strip()
        table_by_type = {str(v).lower(): str(k) for k, v in assets.device_type_tables.items()}
        if dtype:
            binding.device_types.append(dtype)
            tbl = assets.device_type_tables.get(dtype)
            if tbl:
                binding.device_type_tables.append(tbl)
        for tbl in forced:
            if tbl == "t_station":
                continue
            if tbl not in {str(x).lower() for x in binding.device_type_tables}:
                binding.device_type_tables.append(tbl)
            mapped = table_by_type.get(tbl)
            if mapped and mapped not in binding.device_types:
                binding.device_types.append(mapped)
        pinned = next((t for t in forced if t != "t_station"), None)
        if pinned:
            binding.default_table = pinned
        binding.warnings.append("forced_tables_pin")
    else:
        device_alias_map = {str(k).lower(): str(v) for k, v in assets.device_type_aliases.items()}
        for phrase, dtype in sorted(device_alias_map.items(), key=lambda x: len(x[0]), reverse=True):
            if phrase in q_lower:
                binding.device_types.append(dtype)
                tbl = assets.device_type_tables.get(dtype)
                if tbl:
                    binding.device_type_tables.append(tbl)
                break

        if is_station_catalog_question(q):
            binding.default_table = _STATION_TABLE
            binding.warnings.append("station_catalog_query")
        elif (
            not binding.device_types
            and assets.device_type_tables
            and "fcb" in assets.device_type_tables
        ):
            # 未指明监测类型时默认分层标(fcb)，仅地降语义包（含 fcb 表映射）生效
            binding.device_types.append("fcb")
            binding.device_type_tables.append(
                assets.device_type_tables.get("fcb") or assets.default_subsidence_table
            )
            binding.warnings.append("default_device_type_fcb")

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
        # 站点展示名默认按 project_name 理解
        if not _looks_like_mark(scope.station_name, assets):
            _uniq_append(binding.project_names, scope.station_name)
    for ent in assets.stations:
        name = str(ent.get("name") or ent.get("station_name") or "")
        if name and name in q:
            binding.station_names.append(name)
            if not _looks_like_mark(name, assets):
                _uniq_append(binding.project_names, name)
            sid = ent.get("station_id") or ent.get("id")
            if sid:
                binding.station_ids.append(str(sid))

    if _LAYER_PLACEHOLDER_RE.search(q):
        binding.warnings.append("layered_fcb_placeholder:分层各层数据尚未入库")

    if is_station_catalog_question(q) or "station_catalog_query" in binding.warnings:
        binding.default_table = _STATION_TABLE
        if "station_catalog_query" not in binding.warnings:
            binding.warnings.append("station_catalog_query")
    else:
        _inject_fcb_preferred_station_names(q, binding, assets)

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
