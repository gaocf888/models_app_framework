from __future__ import annotations

from functools import lru_cache

from app.core.config import FaceVectorConfig, get_app_config


@lru_cache(maxsize=1)
def get_face_vector_config() -> FaceVectorConfig:
    return get_app_config().face_vector


def use_milvus_backend() -> bool:
    return get_face_vector_config().backend == "milvus"
