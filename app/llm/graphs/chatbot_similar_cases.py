"""
智能客服：锅炉/管材故障域判定 + 限定 namespace 的相似案例检索辅助逻辑。

与 `enterprise-level_transformation_docs/企业级智能客服 LangGraph 框架实现方案.md` 第 14 节一致。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, List, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

# 规则路径关键词（可按业务扩展）
FAULT_KEYWORDS = (
    "锅炉",
    "管材",
    "管道",
    "管子",
    "爆管",
    "断口",
    "腐蚀",
    "泄漏",
    "渗漏",
    "裂纹",
    "蠕变",
    "过热",
    "结垢",
    "省煤器",
    "水冷壁",
    "过热器",
    "再热器",
    "联箱",
    "承压",
    "焊口",
)


def fault_keyword_match(query: str) -> bool:
    q = (query or "").strip()
    if not q:
        return False
    return any(k in q for k in FAULT_KEYWORDS)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            return None
    return None


FAULT_CLASSIFY_SYSTEM = """你是工业设备故障分析助手。只输出一个 JSON 对象，不要 Markdown、不要其它文字。
字段说明：
- fault_related (boolean): 用户文字和/或图片是否涉及「锅炉」或「管材/管道」类设备的故障或异常（如爆管、断口、腐蚀、泄漏、裂纹、变形、烧损等）。
- confidence (number, 0~1): 对 fault_related 的把握。
- case_rag_query (string): 若 fault_related 为 true，给出一行中文检索查询（含关键现象与部位），否则写空字符串。"""


def resolve_use_fault_vision(
    *,
    global_vision_enabled: bool,
    enable_fault_vision_param: Optional[bool],
    image_urls: List[str],
) -> bool:
    """
    请求级 enable_fault_vision:
    - None: 跟随全局 CHATBOT_FAULT_VISION_ENABLED
    - False: 本轮禁用图片判定
    - True: 有图则用图
    """
    has_img = bool(image_urls)
    if not has_img:
        return False
    if enable_fault_vision_param is False:
        return False
    if enable_fault_vision_param is True:
        return True
    return global_vision_enabled


async def classify_fault_with_llm(
    llm_client: Any,
    *,
    query: str,
    image_urls: List[str],
    use_vision: bool,
    max_tokens: int = 256,
) -> tuple[bool, float, str, List[str]]:
    """返回 (fault_related, confidence, case_rag_query, sources)."""
    sources: List[str] = []
    if use_vision and image_urls:
        blocks: list[dict[str, Any]] = [
            {"type": "text", "text": f"用户问题：{query}\n请结合图片判断是否涉及锅炉或管材类故障。"}
        ]
        for u in image_urls:
            blocks.append({"type": "image_url", "image_url": {"url": u}})
        user_content: Any = blocks
        sources.append("vision")
    else:
        user_content = f"用户问题：{query}\n请仅根据文字判断是否涉及锅炉或管材类故障。"
        sources.append("text")

    messages = [
        {"role": "system", "content": FAULT_CLASSIFY_SYSTEM},
        {"role": "user", "content": user_content},
    ]
    try:
        raw = await llm_client.chat(
            model=None,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("classify_fault_with_llm failed: %s", exc)
        return False, 0.0, (query or "").strip(), []

    data = _extract_json_object(raw) or {}
    fr = bool(data.get("fault_related"))
    try:
        conf = float(data.get("confidence", 0))
    except (TypeError, ValueError):
        conf = 0.0
    crq = str(data.get("case_rag_query") or "").strip() or (query or "").strip()
    return fr, max(0.0, min(1.0, conf)), crq, sources


@dataclass
class FaultCaseGateInput:
    similar_case_enabled: bool
    fault_detect_enabled: bool
    fault_vision_enabled: bool
    fault_detect_mode: str
    fault_min_confidence: float
    intent_label: str
    query: str
    image_urls: List[str]
    enable_fault_vision: Optional[bool]


@dataclass
class FaultCaseGateResult:
    need_similar_cases: bool
    case_rag_query: str
    fault_detect_sources: List[str]
    fault_detect_confidence: float


async def run_fault_case_gate_decision(llm_client: Any, inp: FaultCaseGateInput) -> FaultCaseGateResult:
    """供 LangGraph 节点与 Legacy 流式路径复用。"""
    empty = FaultCaseGateResult(
        need_similar_cases=False,
        case_rag_query="",
        fault_detect_sources=[],
        fault_detect_confidence=0.0,
    )
    if not inp.similar_case_enabled or not inp.fault_detect_enabled:
        return empty
    if inp.intent_label == "clarify":
        return empty

    q = (inp.query or "").strip()
    mode = (inp.fault_detect_mode or "hybrid").lower()
    if mode not in {"rules", "llm", "hybrid"}:
        mode = "hybrid"
    use_vision = resolve_use_fault_vision(
        global_vision_enabled=inp.fault_vision_enabled,
        enable_fault_vision_param=inp.enable_fault_vision,
        image_urls=inp.image_urls,
    )

    fault_related = False
    confidence = 0.0
    case_rag_query = q
    sources: List[str] = []

    if mode == "rules":
        fault_related = fault_keyword_match(q)
        confidence = 1.0 if fault_related else 0.0
        sources = ["text"] if fault_related else []
    elif mode == "llm":
        fault_related, confidence, case_rag_query, sources = await classify_fault_with_llm(
            llm_client,
            query=q,
            image_urls=inp.image_urls,
            use_vision=use_vision,
        )
    else:  # hybrid
        if fault_keyword_match(q):
            fault_related = True
            confidence = 1.0
            sources = ["text"]
        else:
            fault_related, confidence, case_rag_query, sources = await classify_fault_with_llm(
                llm_client,
                query=q,
                image_urls=inp.image_urls,
                use_vision=use_vision,
            )

    need = fault_related and confidence >= inp.fault_min_confidence
    if not need:
        return FaultCaseGateResult(
            need_similar_cases=False,
            case_rag_query="",
            fault_detect_sources=sources,
            fault_detect_confidence=confidence,
        )
    return FaultCaseGateResult(
        need_similar_cases=True,
        case_rag_query=case_rag_query or q,
        fault_detect_sources=sources,
        fault_detect_confidence=confidence,
    )


def format_similar_cases_block(snippets: List[str]) -> str:
    if not snippets:
        return ""
    lines = "\n".join(f"- {s}" for s in snippets)
    return f"\n\n---\n**相似案例**\n{lines}"


def retrieve_similar_case_snippets(
    hybrid_rag: Any,
    *,
    query: str,
    namespace: str,
    top_k: int,
) -> List[str]:
    try:
        return hybrid_rag.retrieve(query, top_k=top_k, namespace=namespace)
    except Exception as exc:  # noqa: BLE001
        logger.warning("retrieve_similar_case_snippets failed ns=%s err=%s", namespace, exc)
        return []
