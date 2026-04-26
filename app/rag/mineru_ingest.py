from __future__ import annotations

import os
import time

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.rag.document_pipeline.parsers import DocumentParser
from app.rag.mineru_redis_gate import get_mineru_gate
from app.rag.models import DocumentSource
from app.rag.pdf_text_analysis import is_likely_scanned_pdf

logger = get_logger(__name__)


def prepare_pdf_document_for_pipeline(doc: DocumentSource) -> tuple[DocumentSource, float | None]:
    """
    PDF 路由：
    - 非本地文件路径：不处理，交给 DocumentParser（兼容内联文本模式）。
    - 文字层充足：保持 source_type=pdf，走 pypdf。
    - 扫描/图片为主：MINERU_ENABLED 时必须走 MinerU；失败抛 MinerUParseError（带详细日志）。
    - 扫描且未启用 MinerU：抛 ValueError(E_MINERU_REQUIRED)。

    返回 (document, mineru_wall_s)；未走 MinerU 时第二项为 None。
    """
    st = (doc.source_type or "text").lower()
    if st != "pdf":
        return doc, None

    path = DocumentParser.resolve_local_path(doc.content)
    if path is None:
        return doc, None

    cfg = get_app_config().mineru
    scanned, stats = is_likely_scanned_pdf(path, max_avg_chars_for_text_pdf=cfg.pdf_scanned_max_avg_chars)

    logger.info(
        "PDF route doc_name=%s path=%s scanned=%s pages=%s sampled=%s avg_chars/sample=%.2f threshold=%.2f",
        doc.doc_name,
        path,
        scanned,
        stats.page_count,
        stats.sampled_pages,
        stats.avg_chars_per_sampled_page,
        cfg.pdf_scanned_max_avg_chars,
    )

    if not scanned:
        return doc, None

    if not cfg.enabled:
        raise ValueError(
            "E_MINERU_REQUIRED: PDF appears image-based or scanned (low extractable text); "
            "set MINERU_ENABLED=true and deploy mineru-api, or provide a text-layer PDF."
        )

    from app.rag.mineru_client import MinerUClient

    redis_url = os.getenv("REDIS_URL") or None
    gate = get_mineru_gate(
        redis_url=redis_url,
        max_concurrent=cfg.max_concurrent,
        key_prefix=cfg.redis_semaphore_key_prefix,
    )
    client = MinerUClient(cfg)
    blocking_timeout = max(cfg.timeout_s + 120.0, 300.0)

    wait_t0 = time.perf_counter()
    with gate.acquire(blocking_timeout_s=blocking_timeout):
        wait_ms = int((time.perf_counter() - wait_t0) * 1000)
        if wait_ms > 1000:
            logger.info(
                "MinerU gate acquired after wait_ms=%s doc_name=%s max_concurrent=%s",
                wait_ms,
                doc.doc_name,
                cfg.max_concurrent,
            )
        md, mineru_meta = client.parse_pdf_to_markdown(path, doc_name=doc.doc_name)

    meta = {**doc.metadata, "pdf_parse_route": "mineru", **mineru_meta}
    wall_s = float(mineru_meta.get("mineru_parse_wall_s") or 0.0)
    new_doc = DocumentSource(
        dataset_id=doc.dataset_id,
        doc_name=doc.doc_name,
        namespace=doc.namespace,
        content=md,
        doc_version=doc.doc_version,
        tenant_id=doc.tenant_id,
        source_type="markdown",
        source_uri=doc.source_uri,
        description=doc.description,
        replace_if_exists=doc.replace_if_exists,
        metadata=meta,
    )
    return new_doc, wall_s


def reset_mineru_gate_for_tests() -> None:
    """仅测试：重置 gate 缓存。"""
    import app.rag.mineru_redis_gate as m

    with m._gate_lock:  # noqa: SLF001
        m._gate_singleton = None  # noqa: SLF001
        m._gate_params = None  # noqa: SLF001
