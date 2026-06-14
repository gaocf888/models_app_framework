
def test_resolve_face_alert_mode() -> None:
    from app.small_models.face.pipeline import resolve_face_alert_mode

    assert resolve_face_alert_mode({}) == "identified"
    assert resolve_face_alert_mode({"unknown_alert": True}) == "both"
    assert resolve_face_alert_mode({"face_alert_mode": "unknown"}) == "unknown"


def test_filter_faces_by_roi() -> None:
    from app.small_models.face.pipeline import filter_faces_by_roi
    from app.small_models.strategy._insightface_utils import FaceInsight

    faces = [
        FaceInsight(bbox_xyxy=(10, 10, 50, 50), det_score=0.9, embedding=[1.0]),
        FaceInsight(bbox_xyxy=(200, 200, 260, 260), det_score=0.9, embedding=[1.0]),
    ]
    roi = {"mode": "rect", "xyxy": [0, 0, 100, 100], "match_mode": "center"}
    out = filter_faces_by_roi(faces, roi, (480, 640, 3))
    assert len(out) == 1
    assert out[0].bbox_xyxy[0] == 10


def test_analyze_faces_alert_modes() -> None:
    from app.small_models.face.gallery_index import GalleryIndex
    from app.small_models.face.models import FaceSample
    from app.small_models.face.pipeline import analyze_faces
    from app.small_models.strategy._insightface_utils import FaceInsight

    samples = [FaceSample(sample_id="s1", person_id="p1", embedding=[1.0, 0.0])]
    index = GalleryIndex.build(samples, {"p1": "P1"}, "t1")
    known = FaceInsight(bbox_xyxy=(0, 0, 10, 10), det_score=0.9, embedding=[1.0, 0.0])
    unknown = FaceInsight(bbox_xyxy=(20, 20, 40, 40), det_score=0.9, embedding=[0.0, 1.0])

    _, _, _, triggered_id, types_id, alerts_id = analyze_faces(
        [known], index, threshold=0.5, face_alert_mode="identified"
    )
    assert triggered_id and types_id == ["identified"]

    _, _, _, triggered_unk, types_unk, _ = analyze_faces(
        [unknown], index, threshold=0.5, face_alert_mode="unknown"
    )
    assert triggered_unk and types_unk == ["unknown"]

    _, _, _, triggered_both, types_both, alerts_both = analyze_faces(
        [known, unknown], index, threshold=0.5, face_alert_mode="both"
    )
    assert triggered_both and set(types_both) == {"identified", "unknown"}
    assert len(alerts_both) == 2


def test_face_cooldown_separate_keys() -> None:
    from app.small_models.algorithm_registry import AlgorithmConfig
    from app.small_models.inference_engine import SmallModelInferenceEngine
    from app.small_models.strategy.base import StrategyResult

    eng = SmallModelInferenceEngine()
    cfg = AlgorithmConfig(
        algor_type="43102",
        cooldown_seconds=60,
        unknown_cooldown_seconds=10,
    )
    result = StrategyResult(
        triggered=True,
        detections=[],
        extra={
            "algorithm": "face_recognition",
            "alert_types": ["identified", "unknown"],
            "face_alerts": [
                {"match_type": "identified", "alert": True},
                {"match_type": "unknown", "alert": True},
            ],
        },
    )
    out = eng._apply_face_cooldown("ch1", "43102", cfg, result)
    assert out.triggered
    assert eng._last_trigger_ts["ch1:43102:identified"] > 0
    assert eng._last_trigger_ts["ch1:43102:unknown"] > 0

    out2 = eng._apply_face_cooldown("ch1", "43102", cfg, result)
    assert not out2.triggered
