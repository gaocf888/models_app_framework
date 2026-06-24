"""看图诊断视觉臂：RAG 缺陷/形貌提示、两阶段 prompt 拼装辅助。"""

from __future__ import annotations

from typing import Any

from app.models.analysis import AnalysisImgDiagRequest


def build_vision_rag_hint_query(req: AnalysisImgDiagRequest, *, rag_scene_label: str, hint_intent: str) -> str:
    """视觉臂前置 RAG：召回常见缺陷/爆口类型及可见形貌特征（TOP N）。"""
    q = (req.query or "").strip()
    return (
        f"{q}\n"
        f"场景:{rag_scene_label}·视觉识别辅助\n"
        f"检索意图:{hint_intent}\n"
        "输出要求:常见类型名称 + 可见形貌/表面特征 + 识别要点，便于与现场照片逐条对照"
    )


def _normalize_snippet_line(text: str, *, max_chars: int = 360) -> str:
    line = " ".join((text or "").split())
    if len(line) > max_chars:
        return line[: max_chars - 1] + "…"
    return line


def format_vision_rag_hints_block(
    snippets: list[str],
    *,
    top_n: int,
    subtype: str,
) -> tuple[str, list[str]]:
    """
    将 RAG snippets 格式化为视觉 prompt 注入块。
    无有效召回时返回空块，不注入内置对照清单。
    返回 (prompt_block, item_lines)。
    """
    top_n = max(1, min(20, top_n))
    items: list[str] = []
    for raw in snippets:
        line = _normalize_snippet_line(raw)
        if not line:
            continue
        if any(line in x for x in items):
            continue
        items.append(line)
        if len(items) >= top_n:
            break

    if not items:
        return "", []

    numbered = [f"{i + 1}. {txt}" for i, txt in enumerate(items)]
    title = (
        "【常见缺陷 TOP10 及可见特征对照（知识库召回；须与照片可见证据逐条比对，"
        "无证据则 defect_type/burst_type 填「不确定」）】"
        if subtype == "defect_ident"
        else "【常见爆口/泄漏形貌 TOP10 及可见特征对照（知识库召回；须与照片可见证据逐条比对，"
        "无证据则 burst_type 填「不确定」）】"
    )
    block = title + "\n" + "\n".join(numbered)
    return block, numbered


def build_vision_multimodal_content(
    *,
    text_header: str,
    image_urls: list[str],
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "text", "text": text_header}]
    for url in image_urls:
        if isinstance(url, str) and url.strip():
            content.append({"type": "image_url", "image_url": {"url": url.strip()}})
    return content
