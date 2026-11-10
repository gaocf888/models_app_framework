"""
智能客服：知识引用预览页（解析后正文 + 目标分片高亮定位）。

数据来自向量库 chunk（与 RAG 检索一致），不是原 PDF/Word 排版还原。
``original_content_url`` 仍由前端/预览页提供「打开原文档」入口。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger
from app.llm.graphs.chatbot_rag_citations import _original_content_url_from_chunk_metadata
from app.rag.vector_store import VectorStoreProvider

logger = get_logger(__name__)

_MAX_DOC_CHUNKS = 10000  # 1000 问类长文档需足够大，避免目标落在截断窗外
_CONTEXT_RADIUS = 40  # 目标分片前后各保留的 chunk 数（过大时截窗）


@dataclass(frozen=True)
class PreviewChunk:
    chunk_id: str
    text: str
    section_path: str | None
    chunk_index: int | None
    is_target: bool
    original_content_url: str | None = None
    image_url: str | None = None


@dataclass(frozen=True)
class CitationPreviewModel:
    doc_name: str
    namespace: str | None
    doc_version: str | None
    ref_index: int | None
    target_chunk_id: str | None
    chunks: list[PreviewChunk]
    original_content_url: str | None
    truncated: bool
    message: str | None = None


def _meta_chunk_index(meta: Any) -> int | None:
    if not isinstance(meta, dict):
        return None
    raw = meta.get("chunk_index")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _meta_section(meta: Any, row: dict[str, Any]) -> str | None:
    if isinstance(meta, dict):
        for key in ("section_path", "parent_section_path"):
            v = meta.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def _row_ids(row: dict[str, Any]) -> set[str]:
    """同一分片可能出现在 ext_id / metadata.chunk_id / 顶层 chunk_id。"""
    ids: set[str] = set()
    for key in ("ext_id", "chunk_id"):
        v = row.get(key)
        if v is not None and str(v).strip():
            ids.add(str(v).strip())
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    for key in ("chunk_id", "ext_id"):
        v = meta.get(key)
        if v is not None and str(v).strip():
            ids.add(str(v).strip())
    return ids


def _row_matches_chunk_id(row: dict[str, Any], chunk_id: str) -> bool:
    return bool(chunk_id) and chunk_id in _row_ids(row)


def _normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", "", (text or "")).strip()


def _highlight_needles(highlight: str) -> list[str]:
    """
    生成若干正文匹配线索：完整前缀、去 Markdown #、FAQ「N. 题干」等。
    兼容 text_preview 与入库正文标点/标题符不完全一致的情况。
    """
    raw = (highlight or "").strip()
    if not raw:
        return []
    needles: list[str] = []
    norm = _normalize_for_match(raw)
    if len(norm) >= 8:
        needles.append(norm[:64])
    # 去掉行首 # / ## …
    stripped = re.sub(r"^#{1,6}\s*", "", raw.strip())
    sn = _normalize_for_match(stripped)
    if len(sn) >= 8 and sn[:64] not in needles:
        needles.append(sn[:64])
    # FAQ：取「807. ……？」一类题干（约 12～48 字）
    m = re.search(r"(\d{1,4}\.\s*[^?\n？]{6,48}[?？]?)", stripped)
    if m:
        q = _normalize_for_match(m.group(1))
        if len(q) >= 8 and q not in needles:
            needles.append(q[:64])
    return needles


def _row_matches_highlight(row: dict[str, Any], highlight: str) -> bool:
    body = _normalize_for_match(str(row.get("text") or ""))
    if not body:
        return False
    for key in _highlight_needles(highlight):
        if key and key in body:
            return True
    return False


def _row_to_preview_chunk(row: dict[str, Any], *, is_target: bool) -> PreviewChunk | None:
    text = str(row.get("text") or "").strip()
    if not text and not (
        isinstance(row.get("metadata"), dict) and row["metadata"].get("image_url")
    ):
        return None
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    cid = ""
    ids = _row_ids(row)
    if ids:
        cid = next(iter(ids))
    img = None
    if meta.get("content_type") == "figure" and meta.get("image_url"):
        img = str(meta.get("image_url") or "").strip() or None
    return PreviewChunk(
        chunk_id=cid,
        text=text,
        section_path=_meta_section(meta, row),
        chunk_index=_meta_chunk_index(meta),
        is_target=is_target,
        original_content_url=_original_content_url_from_chunk_metadata(meta),
        image_url=img,
    )


def _fetch_target_row(store, cid: str, ns: str | None) -> dict[str, Any] | None:
    """按 ext_id 回查目标分片；namespace 过滤过严时再放宽一次。"""
    if not cid:
        return None
    for cand_ns in (ns, None):
        try:
            hits = store.get_chunks_by_ext_ids([cid], namespace=cand_ns)
        except Exception as exc:  # noqa: BLE001
            logger.warning("citation preview get_chunks_by_ext_ids failed: %s", exc)
            hits = []
        if hits:
            return hits[0]
    return None


def _find_target_index(
    rows: list[dict[str, Any]],
    *,
    chunk_id: str | None,
    highlight: str | None,
) -> int:
    if chunk_id:
        for i, row in enumerate(rows):
            if _row_matches_chunk_id(row, chunk_id):
                return i
    if highlight:
        for i, row in enumerate(rows):
            if _row_matches_highlight(row, highlight):
                return i
    return -1


def load_citation_preview(
    *,
    chunk_id: str | None = None,
    namespace: str | None = None,
    doc_name: str | None = None,
    doc_version: str | None = None,
    ref_index: int | None = None,
    highlight: str | None = None,
    store=None,
) -> CitationPreviewModel:
    """从向量库加载文档分片窗口，标记目标 chunk 供高亮。"""
    cid = (chunk_id or "").strip() or None
    dname = (doc_name or "").strip() or None
    ns_raw = (namespace or "").strip()
    ns: str | None = ns_raw if ns_raw else None
    dv = (doc_version or "").strip() or None
    hl = (highlight or "").strip() or None

    if store is None:
        store = VectorStoreProvider().get_default_store()

    target_row = _fetch_target_row(store, cid, ns) if cid else None
    if target_row is not None:
        if not dname:
            dname = str(target_row.get("doc_name") or "").strip() or None
        if ns is None and target_row.get("namespace") is not None:
            ns_hit = str(target_row.get("namespace") or "").strip()
            ns = ns_hit if ns_hit else None

    rows: list[dict[str, Any]] = []
    truncated = False
    if dname:
        try:
            rows = list(
                store.list_chunks_for_document(
                    dname,
                    namespace=ns,
                    doc_version=dv,
                    limit=_MAX_DOC_CHUNKS,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("citation preview list_chunks failed: %s", exc)
            rows = []
        if len(rows) >= _MAX_DOC_CHUNKS:
            truncated = True
        # 带 version 未命中时再放宽 version，避免目标被过滤掉
        if not rows and dv is not None:
            try:
                rows = list(
                    store.list_chunks_for_document(
                        dname,
                        namespace=ns,
                        doc_version=None,
                        limit=_MAX_DOC_CHUNKS,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("citation preview list_chunks(no version) failed: %s", exc)

    if not rows and target_row:
        rows = [target_row]

    if not rows:
        return CitationPreviewModel(
            doc_name=dname or "（未知文档）",
            namespace=ns,
            doc_version=dv,
            ref_index=ref_index,
            target_chunk_id=cid,
            chunks=[],
            original_content_url=None,
            truncated=False,
            message="未在向量库中找到对应知识分片，请确认 chunk_id / doc_name / namespace。",
        )

    target_idx = _find_target_index(rows, chunk_id=cid, highlight=hl)
    if target_idx < 0 and target_row is not None:
        # 列表窗口未包含目标（旧逻辑会误高亮第 0 条「前言」）——插入目标分片
        rows = [target_row] + rows
        target_idx = 0
        truncated = True
    if target_idx < 0 and hl:
        # 仍找不到则按正文线索再扫一遍（兼容 id 体系不一致）
        target_idx = _find_target_index(rows, chunk_id=None, highlight=hl)

    locate_failed = target_idx < 0
    if locate_failed:
        # 不再静默回退到第 0 条；若有 target_row 已在上面处理
        message = (
            "未能精确定位到引用分片（chunk_id/正文未匹配），已展示文档分片列表供查阅。"
            if (cid or hl)
            else None
        )
    else:
        message = None

    # 大文档截窗，保证目标附近可读
    if target_idx >= 0 and len(rows) > _CONTEXT_RADIUS * 2 + 1:
        lo = max(0, target_idx - _CONTEXT_RADIUS)
        hi = min(len(rows), target_idx + _CONTEXT_RADIUS + 1)
        if lo > 0 or hi < len(rows):
            truncated = True
        rows = rows[lo:hi]
        target_idx = target_idx - lo

    chunks: list[PreviewChunk] = []
    orig_url: str | None = None
    for i, row in enumerate(rows):
        is_target = (not locate_failed) and i == target_idx
        # 再保险：按 id/正文标记，避免空行 skip 导致下标错位
        if not is_target and not locate_failed:
            if (cid and _row_matches_chunk_id(row, cid)) or (hl and _row_matches_highlight(row, hl)):
                is_target = True
        pc = _row_to_preview_chunk(row, is_target=is_target)
        if not pc:
            continue
        if is_target and pc.original_content_url:
            orig_url = pc.original_content_url
        elif not orig_url and pc.original_content_url:
            orig_url = pc.original_content_url
        chunks.append(pc)

    # 若因空行 skip 导致没有任何 is_target，按 id/正文补标一次
    if not locate_failed and chunks and not any(c.is_target for c in chunks):
        rebuilt: list[PreviewChunk] = []
        marked = False
        for c in chunks:
            hit = False
            if not marked and cid and c.chunk_id == cid:
                hit = True
            if not marked and hl:
                body_n = _normalize_for_match(c.text)
                hit = any(k in body_n for k in _highlight_needles(hl))
            if hit:
                marked = True
                rebuilt.append(
                    PreviewChunk(
                        chunk_id=c.chunk_id,
                        text=c.text,
                        section_path=c.section_path,
                        chunk_index=c.chunk_index,
                        is_target=True,
                        original_content_url=c.original_content_url,
                        image_url=c.image_url,
                    )
                )
            else:
                rebuilt.append(c)
        chunks = rebuilt
        if not marked:
            message = (
                "未能精确定位到引用分片（chunk_id/正文未匹配），已展示文档分片列表供查阅。"
            )

    if not dname and rows:
        dname = str(rows[0].get("doc_name") or "").strip() or "（未知文档）"

    if truncated and not message:
        message = "文档分片较多，仅展示目标附近内容；完整原件请点「打开原文档」。"

    return CitationPreviewModel(
        doc_name=dname or "（未知文档）",
        namespace=ns,
        doc_version=dv,
        ref_index=ref_index,
        target_chunk_id=cid,
        chunks=chunks,
        original_content_url=orig_url,
        truncated=truncated,
        message=message,
    )


def _format_chunk_body_html(text: str) -> str:
    """轻量排版：转义 + 保留换行；识别 Markdown 标题行。"""
    if not text:
        return ""
    lines = text.splitlines() or [text]
    parts: list[str] = []
    for line in lines:
        raw = line.rstrip()
        if not raw.strip():
            parts.append("<br/>")
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", raw.strip())
        if m:
            level = min(6, len(m.group(1)))
            parts.append(f"<h{level}>{html.escape(m.group(2))}</h{level}>")
            continue
        parts.append(f"<p>{html.escape(raw)}</p>")
    return "\n".join(parts)


def render_citation_preview_html(model: CitationPreviewModel) -> str:
    """渲染可读预览页 HTML（片段高亮 + 自动滚到目标）。"""
    title = html.escape(model.doc_name)
    meta_bits = []
    if model.namespace:
        meta_bits.append(f"namespace={html.escape(model.namespace)}")
    if model.doc_version:
        meta_bits.append(f"version={html.escape(model.doc_version)}")
    if model.ref_index is not None:
        meta_bits.append(f"引用 [{int(model.ref_index)}]")
    if model.target_chunk_id:
        meta_bits.append(f"chunk={html.escape(model.target_chunk_id[:48])}")
    meta_line = " · ".join(meta_bits)

    orig_btn = ""
    if model.original_content_url:
        ou = html.escape(model.original_content_url, quote=True)
        orig_btn = (
            f'<a class="btn" href="{ou}" target="_blank" rel="noopener noreferrer">打开原文档</a>'
        )

    notice = ""
    if model.message:
        notice = f'<p class="notice">{html.escape(model.message)}</p>'
    elif model.truncated:
        notice = '<p class="notice">文档分片较多，仅展示目标附近内容；完整原件请点「打开原文档」。</p>'

    body_parts: list[str] = []
    for ch in model.chunks:
        section = (
            f'<div class="section">{html.escape(ch.section_path)}</div>'
            if ch.section_path
            else ""
        )
        img = ""
        if ch.image_url:
            img = (
                f'<p><img class="fig" src="{html.escape(ch.image_url, quote=True)}" '
                f'alt="figure"/></p>'
            )
        cls = "chunk hit" if ch.is_target else "chunk"
        anchor = ' id="cite-target"' if ch.is_target else ""
        badge = '<span class="badge">本轮引用</span>' if ch.is_target else ""
        body_parts.append(
            f'<article class="{cls}"{anchor}>{badge}{section}{img}'
            f'<div class="body">{_format_chunk_body_html(ch.text)}</div></article>'
        )

    if not body_parts and not notice:
        notice = '<p class="notice">无可展示正文。</p>'

    chunks_html = "\n".join(body_parts)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>知识引用预览 · {title}</title>
  <style>
    :root {{
      --bg: #f3efe6;
      --paper: #fffdf8;
      --ink: #1c1917;
      --muted: #78716c;
      --line: #e7e0d4;
      --hit: #fef3c7;
      --hit-border: #d97706;
      --accent: #0f766e;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Source Han Serif SC", "Noto Serif SC", "Songti SC", Georgia, serif;
      background:
        radial-gradient(ellipse at top, #faf6ee 0%, var(--bg) 55%);
      color: var(--ink);
      line-height: 1.7;
    }}
    .top {{
      position: sticky; top: 0; z-index: 5;
      backdrop-filter: blur(8px);
      background: rgba(255,253,248,0.92);
      border-bottom: 1px solid var(--line);
      padding: 0.75rem 1rem;
      display: flex; gap: 0.75rem; flex-wrap: wrap; align-items: center;
      justify-content: space-between;
    }}
    .top h1 {{
      margin: 0; font-size: 1.05rem; font-weight: 650;
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    }}
    .meta {{ color: var(--muted); font-size: 0.8rem;
      font-family: ui-sans-serif, system-ui, sans-serif; }}
    .btn {{
      display: inline-block; text-decoration: none;
      background: var(--accent); color: #fff;
      padding: 0.4rem 0.75rem; border-radius: 6px;
      font-size: 0.85rem;
      font-family: ui-sans-serif, system-ui, sans-serif;
    }}
    .btn.ghost {{ background: transparent; color: var(--accent); border: 1px solid var(--accent); }}
    main {{
      max-width: 46rem; margin: 1.25rem auto 3rem; padding: 0 1rem;
    }}
    .paper {{
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 1.25rem 1.35rem 1.75rem;
      box-shadow: 0 10px 30px rgba(28,25,23,0.06);
    }}
    .notice {{
      font-family: ui-sans-serif, system-ui, sans-serif;
      font-size: 0.85rem; color: #92400e;
      background: #fffbeb; border: 1px solid #fde68a;
      padding: 0.6rem 0.75rem; border-radius: 8px;
    }}
    .chunk {{
      padding: 0.85rem 0.15rem;
      border-bottom: 1px dashed var(--line);
    }}
    .chunk:last-child {{ border-bottom: none; }}
    .chunk.hit {{
      background: var(--hit);
      border: 1px solid var(--hit-border);
      border-radius: 10px;
      padding: 0.9rem 0.85rem;
      margin: 0.75rem 0;
      scroll-margin-top: 5.5rem;
    }}
    .badge {{
      display: inline-block;
      font-family: ui-sans-serif, system-ui, sans-serif;
      font-size: 0.72rem; font-weight: 600;
      color: #92400e; background: #fde68a;
      padding: 0.15rem 0.45rem; border-radius: 999px;
      margin-bottom: 0.35rem;
    }}
    .section {{
      font-family: ui-sans-serif, system-ui, sans-serif;
      font-size: 0.78rem; color: var(--muted); margin-bottom: 0.35rem;
    }}
    .body p {{ margin: 0.35rem 0; }}
    .body h1,.body h2,.body h3,.body h4,.body h5,.body h6 {{
      margin: 0.55rem 0 0.35rem;
      font-family: ui-sans-serif, system-ui, "PingFang SC", "Microsoft YaHei", sans-serif;
      line-height: 1.35;
    }}
    .fig {{ max-width: 100%; border-radius: 8px; border: 1px solid var(--line); }}
    .foot {{
      margin-top: 1rem; color: var(--muted); font-size: 0.78rem;
      font-family: ui-sans-serif, system-ui, sans-serif;
    }}
  </style>
</head>
<body>
  <div class="top">
    <div>
      <h1>{title}</h1>
      <div class="meta">{meta_line}</div>
    </div>
    <div style="display:flex;gap:0.5rem;flex-wrap:wrap;">
      {orig_btn}
      <a class="btn ghost" href="#cite-target">回到引用位置</a>
    </div>
  </div>
  <main>
    <div class="paper">
      {notice}
      {chunks_html}
      <p class="foot">本页展示知识库解析后的文本分片（用于引用定位），版式可能与原 PDF/Word 不同。</p>
    </div>
  </main>
  <script>
    function jump() {{
      var el = document.getElementById("cite-target");
      if (el) el.scrollIntoView({{ behavior: "smooth", block: "center" }});
    }}
    window.addEventListener("load", function() {{ setTimeout(jump, 60); }});
  </script>
</body>
</html>
"""


def preview_model_to_dict(model: CitationPreviewModel) -> dict[str, Any]:
    return {
        "doc_name": model.doc_name,
        "namespace": model.namespace,
        "doc_version": model.doc_version,
        "ref_index": model.ref_index,
        "target_chunk_id": model.target_chunk_id,
        "original_content_url": model.original_content_url,
        "truncated": model.truncated,
        "message": model.message,
        "chunks": [
            {
                "chunk_id": c.chunk_id,
                "text": c.text,
                "section_path": c.section_path,
                "chunk_index": c.chunk_index,
                "is_target": c.is_target,
                "original_content_url": c.original_content_url,
                "image_url": c.image_url,
            }
            for c in model.chunks
        ],
    }
