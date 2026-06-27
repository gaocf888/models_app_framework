from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.small_models.strategy.face.vector_config import get_face_vector_config

if TYPE_CHECKING:
    from pymilvus import Collection

logger = get_logger(__name__)

_collection_lock = threading.Lock()
_collection: Collection | None = None


def _require_pymilvus():
    try:
        import pymilvus  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "FACE_VECTOR_BACKEND=milvus requires pymilvus. "
            "Install with: pip install -r requirements-人脸识别-Milvus.txt"
        ) from exc


def _milvus_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def milvus_filter_eq(field: str, value: str) -> str:
    return f'{field} == "{_milvus_escape(value)}"'


def milvus_filter_and(*parts: str) -> str:
    return " and ".join(f"({p})" for p in parts if p)


def get_face_milvus_collection():
    """获取（并首次创建）Milvus collection；进程内单例。"""
    global _collection
    _require_pymilvus()
    from pymilvus import (
        Collection,
        CollectionSchema,
        DataType,
        FieldSchema,
        connections,
        utility,
    )

    cfg = get_face_vector_config()
    with _collection_lock:
        if _collection is not None:
            return _collection

        alias = "face_gallery_default"
        if not connections.has_connection(alias):
            connections.connect(alias=alias, uri=cfg.milvus_uri)

        name = cfg.milvus_collection
        if not utility.has_collection(name, using=alias):
            fields = [
                FieldSchema(
                    name="sample_id",
                    dtype=DataType.VARCHAR,
                    is_primary=True,
                    max_length=64,
                ),
                FieldSchema(name="gallery_id", dtype=DataType.VARCHAR, max_length=128),
                FieldSchema(name="person_id", dtype=DataType.VARCHAR, max_length=128),
                FieldSchema(name="person_name", dtype=DataType.VARCHAR, max_length=256),
                FieldSchema(
                    name="embedding",
                    dtype=DataType.FLOAT_VECTOR,
                    dim=cfg.embedding_dim,
                ),
            ]
            schema = CollectionSchema(fields=fields, description="Face gallery embeddings")
            col = Collection(name=name, schema=schema, using=alias)
            index_params = {
                "metric_type": cfg.milvus_metric,
                "index_type": "AUTOINDEX",
                "params": {},
            }
            col.create_index(field_name="embedding", index_params=index_params)
            logger.info(
                "milvus face collection created: name=%s dim=%d metric=%s",
                name,
                cfg.embedding_dim,
                cfg.milvus_metric,
            )
        else:
            col = Collection(name=name, using=alias)

        col.load()
        _collection = col
        return _collection


def reset_milvus_collection_cache() -> None:
    """测试或重连时清空单例。"""
    global _collection
    with _collection_lock:
        _collection = None
