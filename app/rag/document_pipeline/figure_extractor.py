from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.rag.asset_storage import RagAssetStorage
from app.rag.document_pipeline.enrichers import chunk_hash, make_chunk_meta
from app.rag.document_pipeline.figure_text import (
    find_markdown_image_refs,
    format_figure_chunk_text,
    resolve_image_ref_path,
    slice_neighbor_text,
)
from app.rag.models import ChunkRecord, DocumentSource
from app.rag.vision_caption_service import VisionCaptionService

logger = get_logger(__name__)


@dataclass
class ExtractedFigure:
    path: str
    md_ref: str
    alt_text: str
    anchor_start: int
    anchor_end: int
    figure_index: int
    parent_section_path: str | None = None


def _section_path_before_markdown(parsed: str, anchor_start: int) -> str | None:
    import re

    region = parsed[: max(0, anchor_start)]
    matches = list(re.finditer(r"^(#{1,6})\s+(.+)$", region, re.MULTILINE))
    if matches:
        return matches[-1].group(2).strip()
    return None


def _save_docx_embed_image(document: object, embed: str, tmp_dir: Path, figure_index: int) -> str | None:
    from docx.oxml.ns import qn

    part = getattr(document, "part", None)
    rels = getattr(part, "rels", {}) if part else {}
    rel = rels.get(embed)
    if rel is None or "image" not in getattr(rel, "reltype", ""):
        return None
    try:
        blob = rel.target_part.blob
        ext = Path(rel.target_part.partname).suffix or ".png"
        fp = tmp_dir / f"img_{figure_index}{ext}"
        fp.write_bytes(blob)
        return str(fp)
    except Exception as exc:  # noqa: BLE001
        logger.debug("skip docx embed %s: %s", embed, exc)
        return None


def _walk_docx_paragraph_images(
    para: object,
    *,
    document: object,
    tmp_dir: Path,
    figure_index: int,
    current_section: str | None,
    parsed_parts: list[str],
    figures: list[ExtractedFigure],
) -> int:
    from docx.oxml.ns import qn

    style = (para.style.name or "") if getattr(para, "style", None) else ""
    if style.startswith("Heading") and para.text.strip():
        parsed_parts.append(para.text.strip())
        parsed_parts.append("\n\n")
        return figure_index

    run_buf: list[str] = []
    for run in para.runs:
        if run.text:
            run_buf.append(run.text)
        for blip in run._element.xpath(".//a:blip"):
            embed = blip.get(qn("r:embed"))
            if not embed:
                continue
            if run_buf:
                parsed_parts.append("".join(run_buf))
                run_buf = []
            path = _save_docx_embed_image(document, embed, tmp_dir, figure_index)
            if not path:
                continue
            marker = f"\n[FIG:{figure_index}]\n"
            anchor_start = sum(len(p) for p in parsed_parts)
            parsed_parts.append(marker)
            figures.append(
                ExtractedFigure(
                    path=path,
                    md_ref=embed,
                    alt_text="",
                    anchor_start=anchor_start,
                    anchor_end=anchor_start + len(marker),
                    figure_index=figure_index,
                    parent_section_path=current_section,
                )
            )
            figure_index += 1
    if run_buf:
        parsed_parts.append("".join(run_buf))
    return figure_index


def _extract_docx_figures(doc: DocumentSource) -> tuple[list[ExtractedFigure], str]:
    from app.rag.document_pipeline.parsers import DocumentParser

    p = DocumentParser.resolve_local_path(doc.content)
    if p is None or p.suffix.lower() not in {".docx"}:
        return [], ""
    try:
        import docx  # type: ignore[import-untyped]
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except Exception:
        return [], ""

    document = docx.Document(str(p))
    import tempfile

    tmp_dir = Path(tempfile.mkdtemp(prefix="rag_docx_img_"))
    parsed_parts: list[str] = []
    figures: list[ExtractedFigure] = []
    figure_index = 0
    current_section: str | None = None

    for child in document.element.body:
        if child.tag == qn("w:p"):
            para = Paragraph(child, document)
            style = (para.style.name or "") if para.style else ""
            if style.startswith("Heading") and para.text.strip():
                current_section = para.text.strip()
            figure_index = _walk_docx_paragraph_images(
                para,
                document=document,
                tmp_dir=tmp_dir,
                figure_index=figure_index,
                current_section=current_section,
                parsed_parts=parsed_parts,
                figures=figures,
            )
            parsed_parts.append("\n\n")
        elif child.tag == qn("w:tbl"):
            tbl = Table(child, document)
            rows: list[str] = []
            for row in tbl.rows:
                cells = [c.text.strip().replace("|", "｜") for c in row.cells]
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                parsed_parts.append("\n".join(rows))
                parsed_parts.append("\n\n")

    parsed_linear = "".join(parsed_parts).strip()
    return figures, parsed_linear


def _mineru_image_base(doc: DocumentSource) -> Path | None:
    meta = doc.metadata or {}
    task_id = meta.get("mineru_job_id") or meta.get("mineru_task_id")
    subdir = meta.get("mineru_disk_fallback_subdir") or "mineru-output"
    if not task_id:
        return None
    try:
        from app.core.config import get_app_config as gac

        io_base = Path(gac().mineru.io_path).expanduser()
        root = io_base / subdir / str(task_id)
        if root.exists():
            return root
    except Exception:  # noqa: BLE001
        pass
    extra = meta.get("mineru_image_base")
    if extra:
        p = Path(str(extra))
        if p.exists():
            return p
    return None


def _discover_markdown_figures(parsed: str, doc: DocumentSource) -> list[ExtractedFigure]:
    base = _mineru_image_base(doc)
    base_str = str(base) if base else None
    refs = find_markdown_image_refs(parsed)
    out: list[ExtractedFigure] = []
    for i, (start, end, alt, ref) in enumerate(refs):
        path = resolve_image_ref_path(ref, base_dir=base_str)
        if not path:
            continue
        out.append(
            ExtractedFigure(
                path=path,
                md_ref=ref,
                alt_text=alt,
                anchor_start=start,
                anchor_end=end,
                figure_index=i,
                parent_section_path=_section_path_before_markdown(parsed, start),
            )
        )
    return out


def _extract_docx_images(doc: DocumentSource) -> tuple[list[ExtractedFigure], str]:
    """兼容旧名；返回 (figures, 含图锚点的线性正文流)。"""
    return _extract_docx_figures(doc)


def extract_figures_from_document(
    *,
    parsed: str,
    doc: DocumentSource,
    staged: dict[str, Any] | None = None,
) -> tuple[list[ExtractedFigure], str | None]:
    """
    返回 (figures, neighbor_parsed_override)。
    Docx 内嵌图需用文档顺序重建的正文流截取邻近正文，第二项为非空 override。
    """
    st = (doc.source_type or "text").lower()
    if st in {"markdown", "md", "html"} or doc.metadata.get("pdf_parse_route") == "mineru":
        figs = _discover_markdown_figures(parsed, doc)
        if figs:
            return figs, None
    if st in {"docx", "doc"}:
        figs, linear = _extract_docx_figures(doc)
        return figs, linear or None
    return [], None


def build_figure_chunks_from_extracted(
    *,
    doc: DocumentSource,
    parsed: str,
    figures: list[ExtractedFigure],
) -> tuple[list[ChunkRecord], dict[str, Any]]:
    if not figures:
        return [], {"figure_count": 0, "vlm_caption_ms": 0}

    cfg = get_app_config().rag.ingestion
    storage = RagAssetStorage()
    caption_svc = VisionCaptionService()
    metrics: dict[str, Any] = {"figure_count": 0, "vlm_caption_ms": 0}
    chunks: list[ChunkRecord] = []

    for fig in figures:
        t0 = time.perf_counter()
        try:
            asset = storage.upload_image(
                local_path=fig.path,
                doc_name=doc.doc_name,
                doc_version=doc.doc_version,
                figure_index=fig.figure_index,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("figure upload failed doc=%s ref=%s err=%s", doc.doc_name, fig.md_ref, exc)
            continue

        before, after = slice_neighbor_text(
            parsed,
            anchor_start=fig.anchor_start,
            anchor_end=fig.anchor_end,
            max_chars=cfg.figure_neighbor_text_max_chars,
            before_ratio=cfg.figure_neighbor_text_before_ratio,
        )
        context = before or fig.alt_text or doc.description
        caption_source = "vlm"
        try:
            caption = caption_svc.caption_figure(asset["image_url"], context=context)
        except Exception:
            caption = fig.alt_text or before or f"图块 {fig.figure_index + 1}"
            caption_source = "failed"
            metrics["caption_failed_count"] = metrics.get("caption_failed_count", 0) + 1

        metrics["vlm_caption_ms"] = metrics.get("vlm_caption_ms", 0) + int((time.perf_counter() - t0) * 1000)

        text = format_figure_chunk_text(
            caption=caption,
            neighbor_before=before,
            neighbor_after=after,
            doc_name=doc.doc_name,
        )
        meta = make_chunk_meta(
            doc_name=doc.doc_name,
            chunk_index=100000 + fig.figure_index,
            namespace=doc.namespace,
            source_uri=doc.source_uri,
        )
        meta.update(
            {
                "content_type": "figure",
                "image_url": asset["image_url"],
                "image_object_key": asset.get("image_object_key"),
                "figure_index": fig.figure_index,
                "parent_doc_name": doc.doc_name,
                "parent_section_path": fig.parent_section_path,
                "neighbor_text_before": before,
                "neighbor_text_after": after,
                "caption_source": caption_source,
                "md_image_ref": fig.md_ref,
            }
        )
        meta["chunk_hash"] = chunk_hash(text)
        chunks.append(ChunkRecord(chunk_id=meta["chunk_id"], chunk_index=meta["chunk_index"], text=text, metadata=meta))
        metrics["figure_count"] = metrics.get("figure_count", 0) + 1

    return chunks, metrics
