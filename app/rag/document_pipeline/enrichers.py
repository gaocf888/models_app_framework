from __future__ import annotations

import hashlib
import uuid
from typing import Dict


def make_chunk_meta(doc_name: str, chunk_index: int, namespace: str | None, source_uri: str | None) -> Dict:
    return {
        "chunk_id": str(uuid.uuid4()),
        "chunk_index": chunk_index,
        "doc_name": doc_name,
        "namespace": namespace,
        "source_uri": source_uri,
    }


def chunk_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

