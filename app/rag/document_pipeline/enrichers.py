from __future__ import annotations

import hashlib
import uuid
from typing import Any, Dict

from app.rag.text_quality import strip_utf16_surrogates


def make_chunk_meta(
    doc_name: str,
    chunk_index: int,
    namespace: str | None,
    source_uri: str | None,
    *,
    section_path: str | None = None,
    section_level: int | None = None,
) -> Dict[str, Any]:
    """
    构造 chunk metadata。

    ``section_path`` / ``section_level`` 非空时写入，供检索与 ``rag_citations`` 透出。
    """
    meta: Dict[str, Any] = {
        "chunk_id": str(uuid.uuid4()),
        "chunk_index": chunk_index,
        "doc_name": doc_name,
        "namespace": namespace,
        "source_uri": source_uri,
    }
    sp = (section_path or "").strip() if isinstance(section_path, str) else None
    if sp:
        meta["section_path"] = sp
    if section_level is not None:
        try:
            lvl = int(section_level)
        except (TypeError, ValueError):
            lvl = None
        if lvl is not None and 1 <= lvl <= 6:
            meta["section_level"] = lvl
    return meta


def chunk_hash(text: str) -> str:
    payload = strip_utf16_surrogates(text)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
