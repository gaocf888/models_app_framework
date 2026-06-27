"""接打电话 CallingDetectionStrategy 与空间规则测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.small_models.algorithm_registry import AlgorithmConfig, merge_algorithm_config
from app.small_models.inference_engine import SmallModelInferenceEngine, _STRATEGY_CLASSES, _canonical_strategy_name
from app.small_models.strategy.base import Detection
from app.small_models.strategy.base._spatial_rules import (
    match_calling_by_phone_spatial,
    phone_center_in_person_upper_body,
)
from app.small_models.strategy.specialized.calling_detection import (
    CallingDetectionStrategy,
    resolve_strategy_name_for_algor,
)


def test_phone_center_in_person_upper_body() -> None:
    person = (100, 100, 200, 300)
    phone_ok = (130, 120, 160, 160)
    phone_low = (130, 250, 160, 280)
    assert phone_center_in_person_upper_body(person, phone_ok, upper_body_ratio=0.45)
    assert not phone_center_in_person_upper_body(person, phone_low, upper_body_ratio=0.45)


def test_match_calling_by_phone_spatial() -> None:
    persons = [
        Detection(label="person", score=0.9, bbox_xyxy=(100, 100, 200, 300), class_id=0),
    ]
    phones = [
        Detection(label="phone", score=0.8, bbox_xyxy=(130, 120, 160, 160), class_id=67),
        Detection(label="phone", score=0.3, bbox_xyxy=(130, 120, 160, 160), class_id=67),
    ]
    matches = match_calling_by_phone_spatial(
        persons, phones, upper_body_ratio=0.45, min_phone_conf=0.5
    )
    assert len(matches) == 1
    assert matches[0].match_score == 0.8


def test_resolve_strategy_name_for_algor() -> None:
    assert (
        resolve_strategy_name_for_algor("RegularBehaviorDetectionStrategy", "40417")
        == "RegularBehaviorDetectionStrategy"
    )
    assert resolve_strategy_name_for_algor(None, "40417") == "CallingDetectionStrategy"
    assert resolve_strategy_name_for_algor(None, "40111") is None


def test_calling_strategy_alias() -> None:
    assert _canonical_strategy_name("CallingStrategy") == "CallingDetectionStrategy"
    eng = SmallModelInferenceEngine()
    a = eng._get_strategy("CallingStrategy")
    b = eng._get_strategy("CallingDetectionStrategy")
    assert a is b


def test_strategy_table_includes_calling() -> None:
    assert "CallingDetectionStrategy" in _STRATEGY_CLASSES


def test_merge_calling_config() -> None:
    base = AlgorithmConfig(
        algor_type="40417",
        strategy="CallingDetectionStrategy",
        calling_mode="spatial",
        calling_fallback_end_to_end=True,
    )
    m = merge_algorithm_config(base, {"algor_type": "40417", "calling_mode": "end_to_end"})
    assert m.calling_mode == "end_to_end"
    assert m.calling_fallback_end_to_end is True


def test_default_strategy_for_calling_algor_type() -> None:
    from app.small_models.inference_engine import _default_strategy_for_cfg

    cfg = AlgorithmConfig(algor_type="40417")
    out = _default_strategy_for_cfg(cfg)
    assert out.strategy == "CallingDetectionStrategy"


@patch("app.small_models.strategy.specialized.calling_detection.run_yolo_detection_pipeline")
def test_calling_end_to_end_mode(mock_pipeline: MagicMock) -> None:
    mock_pipeline.return_value = [
        Detection(label="calling", score=0.9, bbox_xyxy=(1, 2, 3, 4), class_id=0),
    ]
    strat = CallingDetectionStrategy()
    result = strat.infer(
        MagicMock(),
        config={"calling_mode": "end_to_end", "weights_path": "call.pt"},
        context={"channel_id": "c1", "algor_type": "41201"},
    )
    assert result.triggered
    assert result.extra.get("calling_mode") == "end_to_end"
    mock_pipeline.assert_called_once()


@patch("app.small_models.strategy.specialized.calling_detection.run_yolo_detection_pipeline")
@patch("app.small_models.strategy.specialized.calling_detection.predict_detections")
@patch("app.small_models.strategy.specialized.calling_detection.get_yolo_model")
@patch("app.small_models.strategy.specialized.calling_detection.resolve_path")
def test_calling_spatial_with_fallback(
    mock_resolve: MagicMock,
    mock_get_model: MagicMock,
    mock_predict: MagicMock,
    mock_pipeline: MagicMock,
) -> None:
    mock_resolve.return_value = "/fake/yolov8s.pt"
    mock_get_model.return_value = MagicMock()
    mock_predict.return_value = []
    mock_pipeline.return_value = [
        Detection(label="calling", score=0.85, bbox_xyxy=(10, 10, 50, 50), class_id=0),
    ]

    strat = CallingDetectionStrategy()
    result = strat.infer(
        MagicMock(),
        config={
            "calling_mode": "spatial",
            "weights_path": "yolov8s.pt",
            "calling_fallback_end_to_end": True,
            "calling_fallback_weights_path": "call.pt",
        },
        context={"channel_id": "c1", "algor_type": "40417"},
    )
    assert result.triggered
    assert result.extra.get("calling_fallback_used") is True
    mock_pipeline.assert_called_once()
