"""小模型策略：配置合并、类过滤、策略表与兼容别名。"""

from __future__ import annotations

import pytest

from app.small_models.algorithm_registry import AlgorithmConfig, merge_algorithm_config
from app.small_models.inference_engine import SmallModelInferenceEngine, _STRATEGY_CLASSES, _canonical_strategy_name
from app.small_models.strategy.base import Detection
from app.small_models.strategy.object_detection import apply_class_filter


def test_merge_algorithm_config() -> None:
    base = AlgorithmConfig(
        algor_type="x",
        strategy="ObjectDetectionStrategy",
        weights_path="a.pt",
        complex_mode="dwell",
    )
    m = merge_algorithm_config(base, {"algor_type": "x", "conf": 0.5})
    assert m.conf == 0.5
    assert m.weights_path == "a.pt"
    assert m.complex_mode == "dwell"


def test_apply_class_filter() -> None:
    dets = [
        Detection(label="person", score=0.9, bbox_xyxy=(0, 0, 10, 10), class_id=0),
        Detection(label="car", score=0.8, bbox_xyxy=(0, 0, 10, 10), class_id=2),
    ]
    out = apply_class_filter(dets, {"class_names": ["person"]})
    assert len(out) == 1 and out[0].label == "person"


def test_strategy_table_three_classes() -> None:
    assert set(_STRATEGY_CLASSES) == {
        "ObjectDetectionStrategy",
        "RegularBehaviorDetectionStrategy",
        "ComplexBehaviorDetectionStrategy",
    }


def test_calling_strategy_alias_same_singleton() -> None:
    eng = SmallModelInferenceEngine()
    a = eng._get_strategy("CallingStrategy")
    b = eng._get_strategy("RegularBehaviorDetectionStrategy")
    assert a is b


def test_canonical_name() -> None:
    assert _canonical_strategy_name("CallingStrategy") == "RegularBehaviorDetectionStrategy"
    assert _canonical_strategy_name("ObjectDetectionStrategy") == "ObjectDetectionStrategy"


def test_unknown_strategy_raises() -> None:
    eng = SmallModelInferenceEngine()
    with pytest.raises(ValueError, match="unknown strategy"):
        eng._get_strategy("NoSuchStrategy")
