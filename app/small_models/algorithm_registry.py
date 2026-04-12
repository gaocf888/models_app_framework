from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class AlgorithmConfig:
    """
    单条 algor_type 配置（YAML + API 覆盖）。

    三类策略在 YAML 中用 strategy 区分：
    - ObjectDetectionStrategy：常规目标
    - RegularBehaviorDetectionStrategy：常规行为
    - ComplexBehaviorDetectionStrategy：复杂行为（可选 complex_mode）
    """

    algor_type: str
    name: Optional[str] = None
    description: Optional[str] = None
    strategy: Optional[str] = None
    model_name: Optional[str] = None
    weights_path: Optional[str] = None
    device: Optional[str] = None
    imgsz: Optional[int] = None
    conf: Optional[float] = None
    iou: Optional[float] = None
    cooldown_seconds: Optional[int] = None
    evidence_dir: Optional[str] = None
    clip_seconds: Optional[int] = None
    callback_url: Optional[str] = None
    roi: Optional[dict] = None
    class_filter: Optional[dict] = None
    # 复杂行为（仅 ComplexBehaviorDetectionStrategy 使用）
    complex_mode: Optional[str] = None
    dwell_seconds: Optional[float] = None
    dwell_polygon: Optional[List[List[float]]] = None
    line_cross_line: Optional[List[List[float]]] = None
    zone_intrusion_polygon: Optional[List[List[float]]] = None


def _algorithm_config_from_mapping(algor_type: str, cfg: dict) -> AlgorithmConfig:
    cfg = cfg or {}
    return AlgorithmConfig(
        algor_type=str(algor_type),
        name=cfg.get("name"),
        description=cfg.get("description"),
        strategy=cfg.get("strategy"),
        model_name=cfg.get("model_name"),
        weights_path=cfg.get("weights_path"),
        device=cfg.get("device"),
        imgsz=cfg.get("imgsz"),
        conf=cfg.get("conf"),
        iou=cfg.get("iou"),
        cooldown_seconds=cfg.get("cooldown_seconds"),
        evidence_dir=cfg.get("evidence_dir"),
        clip_seconds=cfg.get("clip_seconds"),
        callback_url=cfg.get("callback_url"),
        roi=cfg.get("roi"),
        class_filter=cfg.get("class_filter"),
        complex_mode=cfg.get("complex_mode"),
        dwell_seconds=cfg.get("dwell_seconds"),
        dwell_polygon=cfg.get("dwell_polygon"),
        line_cross_line=cfg.get("line_cross_line"),
        zone_intrusion_polygon=cfg.get("zone_intrusion_polygon"),
    )


class SmallModelAlgorithmRegistry:
    """algor_type -> AlgorithmConfig，来源：configs/small_model_algorithms.yaml"""

    def __init__(self, config_path: str | None = None) -> None:
        self._algorithms: Dict[str, AlgorithmConfig] = {}
        self._config_path = config_path or "configs/small_model_algorithms.yaml"
        self._load_from_yaml()

    def _load_from_yaml(self) -> None:
        path = Path(self._config_path)
        if not path.exists():
            logger.warning("small model algorithms config file not found: %s", path)
            return
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        algos = data.get("algorithms") or {}
        for algor_type, cfg in algos.items():
            self._algorithms[str(algor_type)] = _algorithm_config_from_mapping(str(algor_type), cfg or {})
        logger.info("loaded %d algorithm configs from %s", len(self._algorithms), path)

    def get(self, algor_type: str | None) -> AlgorithmConfig | None:
        if not algor_type:
            return None
        return self._algorithms.get(str(algor_type))


def resolve_path(path_str: str | None) -> str | None:
    if not path_str:
        return None
    p = Path(path_str)
    if p.is_absolute():
        return str(p)
    return str((Path.cwd() / p).resolve())


def merge_algorithm_config(base: AlgorithmConfig | None, overrides: Dict[str, Any]) -> AlgorithmConfig:
    algor_type = str(overrides.get("algor_type") or (base.algor_type if base else ""))
    if not algor_type:
        raise ValueError("algor_type is required to resolve algorithm config")

    def pick(key: str) -> Any:
        if key in overrides and overrides.get(key) is not None:
            return overrides.get(key)
        return getattr(base, key) if base is not None else None

    kwargs: Dict[str, Any] = {"algor_type": algor_type}
    for f in fields(AlgorithmConfig):
        if f.name == "algor_type":
            continue
        kwargs[f.name] = pick(f.name)
    return AlgorithmConfig(**kwargs)
