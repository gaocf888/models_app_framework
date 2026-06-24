"""看图诊断视觉臂：RAG 缺陷/形貌提示、两阶段 prompt 拼装辅助。"""

from __future__ import annotations

from typing import Any

from app.models.analysis import AnalysisImgDiagRequest

# 知识库无召回时的兜底对照清单（与 prompts 中 defect_type 枚举对齐）
_DEFECT_FALLBACK_TOP10: list[str] = [
    "1. 飞灰冲刷磨损沟槽 — 可见特征：沿烟气流向的平行沟槽/犁沟，多位于冲刷面，槽深较浅、走向一致",
    "2. 点蚀/均匀腐蚀坑 — 可见特征：表面密集小坑或片状麻点，颜色可偏暗红/黑，边界较模糊",
    "3. 管壁胀粗 — 可见特征：局部直径明显增大、鼓包，表面可光滑或伴裂纹，常见于高温区",
    "4. 轴向表面裂纹 — 可见特征：沿管子轴线方向延伸的线状开裂，可细如发丝或较宽",
    "5. 周向表面裂纹 — 可见特征：环绕管壁横向或近似横向的裂纹，常与应力/疲劳相关",
    "6. 焊口夹渣/未熔合 — 可见特征：焊缝处不规则凸起、凹陷或线性缺欠，颜色/纹理与母材差异",
    "7. 防磨瓦脱落 — 可见特征：应覆盖防磨瓦区域裸露，可见瓦座/卡件或脱落痕迹",
    "8. 防磨瓦歪斜 — 可见特征：防磨瓦明显偏位、翘曲，间隙异常",
    "9. 管间氧化皮堆积 — 可见特征：管排间隙或表面片状/层状氧化皮堆积，可呈剥落状",
    "10. 其他可见异常 — 可见特征：上述均不明显时选用，须在 defect_signals 中描述具体形貌",
]

_BURST_FALLBACK_TOP10: list[str] = [
    "1. 环向开口爆口 — 可见特征：沿管周向撕裂的开口，边缘常呈撕裂状或减薄",
    "2. 纵向/轴向裂口 — 可见特征：沿轴线方向延伸的长裂口，可伴开口",
    "3. 穿孔泄漏 — 可见特征：管壁穿透性孔洞，孔缘可翻卷或减薄",
    "4. 窗口形破口 — 可见特征：矩形/不规则窗口状缺失，边沿多毛刺",
    "5. 邻管牵连损伤 — 可见特征：相邻管子可见飞溅、压痕或连带减薄",
    "6. 爆口边缘减薄 — 可见特征：破口边缘明显变薄、呈 knife-edge",
    "7. 冲刷沟槽伴开口 — 可见特征：沟槽末端或沟底伴开裂/穿孔",
    "8. 腐蚀产物伴泄漏 — 可见特征：破口周围堆积腐蚀产物、变色区域",
    "9. 胀粗区伴开裂 — 可见特征：鼓包区域出现表面裂纹或开口",
    "10. 不确定形貌 — 可见特征：无法归入以上类型时选用，须在 burst_signals 中客观描述",
]


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
        fallback = _DEFECT_FALLBACK_TOP10 if subtype == "defect_ident" else _BURST_FALLBACK_TOP10
        items = list(fallback[:top_n])
        source_note = "内置对照清单（知识库未命中）"
    else:
        numbered = [f"{i + 1}. {txt}" for i, txt in enumerate(items)]
        items = numbered
        source_note = "知识库召回"

    title = (
        "【常见缺陷 TOP10 及可见特征对照（"
        f"{source_note}；须与照片可见证据逐条比对，无证据则 defect_type/burst_type 填「不确定」）】"
        if subtype == "defect_ident"
        else "【常见爆口/泄漏形貌 TOP10 及可见特征对照（"
        f"{source_note}；须与照片可见证据逐条比对，无证据则 burst_type 填「不确定」）】"
    )
    block = title + "\n" + "\n".join(items)
    return block, items


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
