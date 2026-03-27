from __future__ import annotations

"""
企业级文档处理管线（模块化）：
- parser：按 source_type 解析原始内容；
- cleaner：执行规范化清洗；
- splitter：结构切分 + 语义切分 + 滑窗兜底；
- enricher：生成 chunk 元数据和 hash。
"""

from typing import List, Tuple

from app.core.config import get_app_config
from app.rag.models import ChunkRecord, DocumentSource

from .cleaners import TextCleaner
from .enrichers import chunk_hash, make_chunk_meta
from .parsers import DocumentParser
from .splitters import ChunkingConfig, SemanticSplitter, StructureSplitter, WindowSplitter


class DocumentPipeline:
    def __init__(self, cfg: ChunkingConfig | None = None, cleaning_profile: str | None = None, strategy: str | None = None) -> None:
        rag_ingest_cfg = get_app_config().rag.ingestion
        self._cfg = cfg or ChunkingConfig(
            chunk_size=rag_ingest_cfg.chunk_size,
            chunk_overlap=rag_ingest_cfg.chunk_overlap,
            min_chunk_size=rag_ingest_cfg.min_chunk_size,
        )
        self._strategy = (strategy or rag_ingest_cfg.default_chunk_strategy or "structure").lower()
        self._parser = DocumentParser()
        self._cleaner = TextCleaner(
            profile=cleaning_profile or rag_ingest_cfg.cleaning_profile,
            remove_header_footer=rag_ingest_cfg.clean_remove_header_footer,
            merge_duplicate_paragraphs=rag_ingest_cfg.clean_merge_duplicate_paragraphs,
            fix_encoding_noise=rag_ingest_cfg.clean_fix_encoding_noise,
            min_repeated_line_pages=rag_ingest_cfg.clean_min_repeated_line_pages,
        )
        self._structure = StructureSplitter()
        self._semantic = SemanticSplitter()
        self._window = WindowSplitter(self._cfg)

    def process(self, content: str) -> List[str]:
        source = DocumentSource(
            dataset_id="adhoc",
            doc_name="adhoc",
            namespace=None,
            content=content,
            source_type="text",
        )
        chunks, _ = self.process_document(source)
        return [c.text for c in chunks]

    def process_document(self, source: DocumentSource) -> Tuple[List[ChunkRecord], dict]:
        staged = self.process_document_staged(source)
        return staged["chunks"], staged["stats"]

    def process_document_staged(self, source: DocumentSource) -> dict:
        """
        分阶段处理文档，返回阶段产物与阶段耗时（ms），便于 orchestrator 做企业级 step 治理。
        """
        import time

        stage_durations_ms: dict[str, int] = {}
        t0 = time.perf_counter()
        parsed = self._parser.parse(source.content, source.source_type)
        stage_durations_ms["parse"] = int((time.perf_counter() - t0) * 1000)

        t1 = time.perf_counter()
        cleaned = self._cleaner.clean(parsed)
        stage_durations_ms["clean"] = int((time.perf_counter() - t1) * 1000)
        if not cleaned:
            return {
                "parsed": parsed,
                "cleaned": cleaned,
                "chunk_texts": [],
                "chunks": [],
                "stats": {"normalized_length": 0},
                "stage_durations_ms": stage_durations_ms,
            }

        t2 = time.perf_counter()
        if self._strategy == "window":
            sections = [cleaned]
        else:
            sections = self._structure.split(cleaned)
            if not sections:
                sections = [cleaned]

        chunk_texts: list[str] = []
        for sec in sections:
            if self._strategy == "semantic":
                semantic_chunks = self._semantic.split(sec, target_size=self._cfg.chunk_size) or [sec]
            elif self._strategy == "window":
                semantic_chunks = [sec]
            else:
                semantic_chunks = self._semantic.split(sec, target_size=self._cfg.chunk_size) or [sec]
            for sem in semantic_chunks:
                if len(sem) > self._cfg.chunk_size:
                    chunk_texts.extend(self._window.split(sem))
                else:
                    chunk_texts.append(sem.strip())
        stage_durations_ms["chunk"] = int((time.perf_counter() - t2) * 1000)

        t3 = time.perf_counter()
        chunks: list[ChunkRecord] = []
        for idx, text in enumerate([t for t in chunk_texts if t.strip()]):
            meta = make_chunk_meta(
                doc_name=source.doc_name,
                chunk_index=idx,
                namespace=source.namespace,
                source_uri=source.source_uri,
            )
            meta["chunk_hash"] = chunk_hash(text)
            chunks.append(
                ChunkRecord(
                    chunk_id=meta["chunk_id"],
                    chunk_index=idx,
                    text=text,
                    metadata=meta,
                )
            )
        stage_durations_ms["enrich"] = int((time.perf_counter() - t3) * 1000)

        stats = {
            "normalized_length": len(cleaned),
            "section_count": len(sections),
            "chunk_count": len(chunks),
            "avg_chunk_length": (sum(len(c.text) for c in chunks) / len(chunks)) if chunks else 0,
            "stage_durations_ms": stage_durations_ms,
        }
        return {
            "parsed": parsed,
            "cleaned": cleaned,
            "chunk_texts": chunk_texts,
            "chunks": chunks,
            "stats": stats,
            "stage_durations_ms": stage_durations_ms,
        }

