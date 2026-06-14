from __future__ import annotations

from pathlib import Path

from app.small_models.algorithm_registry import AlgorithmConfig, merge_algorithm_config
from app.small_models.face.gallery_store import FaceGalleryStore
from app.small_models.face.matcher import match_embedding_1n
from app.small_models.face.models import FaceSample
from app.small_models.inference_engine import SmallModelInferenceEngine, _STRATEGY_CLASSES


def test_strategy_table_includes_face_recognition() -> None:
    assert "FaceRecognitionStrategy" in _STRATEGY_CLASSES


def test_merge_face_algorithm_config() -> None:
    base = AlgorithmConfig(
        algor_type="43101",
        strategy="FaceRecognitionStrategy",
        gallery_id="default",
        match_threshold=0.45,
    )
    m = merge_algorithm_config(base, {"algor_type": "43101", "gallery_id": "gate_a", "match_threshold": 0.5})
    assert m.gallery_id == "gate_a"
    assert m.match_threshold == 0.5
    assert m.strategy == "FaceRecognitionStrategy"


def test_gallery_store_crud(tmp_path: Path) -> None:
    store = FaceGalleryStore(base_dir=str(tmp_path / "galleries"))
    store.create_gallery("g1", name="测试库")
    store.enroll("g1", person_id="p1", name="张三", embedding=[1.0, 0.0, 0.0])
    persons = store.list_persons("g1")
    assert len(persons) == 1 and persons[0]["person_id"] == "p1"
    samples, names = store.all_samples("g1")
    assert len(samples) == 1
    assert names["p1"] == "张三"
    assert store.delete_person("g1", "p1")
    assert store.list_persons("g1") == []


def test_match_embedding_1n() -> None:
    samples = [
        FaceSample(sample_id="s1", person_id="alice", embedding=[1.0, 0.0, 0.0]),
        FaceSample(sample_id="s2", person_id="bob", embedding=[0.0, 1.0, 0.0]),
    ]
    hit = match_embedding_1n(
        [0.99, 0.01, 0.0],
        samples,
        person_names={"alice": "Alice", "bob": "Bob"},
        threshold=0.5,
    )
    assert hit is not None
    assert hit.person_id == "alice"
    assert hit.person_name == "Alice"

    miss = match_embedding_1n([0.0, 0.0, 1.0], samples, person_names={"alice": "Alice"}, threshold=0.9)
    assert miss is None


def test_gallery_index_cache(tmp_path: Path) -> None:
    store = FaceGalleryStore(base_dir=str(tmp_path / "galleries"))
    store.create_gallery("g1")
    store.enroll("g1", person_id="p1", name="A", embedding=[1.0, 0.0])
    idx1 = store.get_gallery_index("g1")
    idx2 = store.get_gallery_index("g1")
    assert idx1 is idx2
    assert idx1.backend == "numpy"
    stats = store.gallery_index_stats("g1")
    assert stats["sample_count"] == 1

    eng = SmallModelInferenceEngine()
    a = eng._get_strategy("FaceRecognitionStrategy")
    b = eng._get_strategy("FaceRecognitionStrategy")
    assert a is b
