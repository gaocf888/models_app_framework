from __future__ import annotations

"""
企业级文档处理管线（模块化）：
- parser：按 source_type 解析原始内容；
- cleaner：执行规范化清洗；
- splitter：结构切分 + 语义切分 + 滑窗兜底（携带 section_path）；
- enricher：生成 chunk 元数据和 hash（含章节字段）。
"""

from typing import List, Tuple

from app.core.config import get_app_config
from app.rag.document_pipeline.section_utils import SectionBlock
from app.rag.models import ChunkRecord, DocumentSource
from app.rag.text_quality import looks_like_binary_text

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
            strip_html=rag_ingest_cfg.clean_strip_html,
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
        if looks_like_binary_text(parsed):
            raise ValueError(
                f"E_BINARY_CONTENT: parsed content looks like binary for doc={source.doc_name}; "
                "check source_type (pdf/docx) or re-ingest with correct format"
            )

        t1 = time.perf_counter()
        cleaned = self._cleaner.clean(parsed)
        stage_durations_ms["clean"] = int((time.perf_counter() - t1) * 1000)
        if not cleaned:
            return {
                "parsed": parsed,
                "cleaned": cleaned,
                "chunk_texts": [],
                "chunks": [],
                "stats": {"normalized_length": 0, "sections_with_path": 0},
                "stage_durations_ms": stage_durations_ms,
            }

        t2 = time.perf_counter()
        annotated_chunks = self._chunk_with_sections(cleaned)
        stage_durations_ms["chunk"] = int((time.perf_counter() - t2) * 1000)

        t3 = time.perf_counter()
        chunks: list[ChunkRecord] = []
        chunk_texts: list[str] = []
        sections_with_path = 0
        for idx, block in enumerate(annotated_chunks):
            text = (block.text or "").strip()
            if not text:
                continue
            chunk_texts.append(text)
            meta = make_chunk_meta(
                doc_name=source.doc_name,
                chunk_index=idx,
                namespace=source.namespace,
                source_uri=source.source_uri,
                section_path=block.section_path,
                section_level=block.section_level,
            )
            meta["chunk_hash"] = chunk_hash(text)
            if block.section_path:
                sections_with_path += 1
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
            "section_count": len({b.section_path for b in annotated_chunks if b.section_path})
            if annotated_chunks
            else 0,
            "chunk_count": len(chunks),
            "chunks_with_section_path": sections_with_path,
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

    def _chunk_with_sections(self, cleaned: str) -> list[SectionBlock]:
        """
        按策略切块并保留章节归属：
        - window：整篇滑窗 + 按偏移标注最近标题；
        - structure / semantic：先结构切分，子块继承父节 section_path。
        """
        if self._strategy == "window":
            return self._window.split_with_sections(cleaned)

        section_blocks = self._structure.split_sections(cleaned)
        if not section_blocks:
            section_blocks = [SectionBlock(text=cleaned, section_path=None, section_level=None)]

        out: list[SectionBlock] = []
        for sec in section_blocks:
            if self._strategy == "semantic":
                pieces = self._semantic.split(sec.text, target_size=self._cfg.chunk_size) or [sec.text]
            else:
                pieces = self._semantic.split(sec.text, target_size=self._cfg.chunk_size) or [sec.text]
            for piece in pieces:
                piece = (piece or "").strip()
                if not piece:
                    continue
                if len(piece) > self._cfg.chunk_size:
                    for win in self._window.split(piece):
                        w = (win or "").strip()
                        if w:
                            out.append(
                                SectionBlock(
                                    text=w,
                                    section_path=sec.section_path,
                                    section_level=sec.section_level,
                                )
                            )
                else:
                    out.append(
                        SectionBlock(
                            text=piece,
                            section_path=sec.section_path,
                            section_level=sec.section_level,
                        )
                    )
        return out
