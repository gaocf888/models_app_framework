"""智能客服意图二次失败：LLM 消歧候选问句（analysis + 3 options）。"""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.llm.prompt_registry import PromptTemplateRegistry

logger = get_logger(__name__)

_FALLBACK_SYSTEM = """你是电厂锅炉领域的智能客服助手。用户问题一次问了多件事、边界不清。请只输出一个 JSON 对象，不要 Markdown。
格式：
{"analysis":"一两句对用户说的话","options":[{"title":"短标题","query":"完整可执行问句","route_hint":"data_query|kb_qa"}]}
硬性要求：
1) analysis 必须像客服当面回复：用「您」，1～2 句；说明「问题里不止一块、怕答偏了、想先对齐」；
   禁止「用户询问」「明确意图」「系统」「结构化查数」等旁白/诊断腔；
2) options 必须恰好 3 条；
3) 至少 1 条 route_hint=data_query（偏查实时/台账数据），至少 1 条 route_hint=kb_qa（偏知识库说明）；
4) query 具体可执行，一条 ideally 只问一件事，不要简单复述用户原话；title 尽量短（不超过 16 字）；
5) route_hint 只能是 data_query 或 kb_qa。"""

_ROUTE_HINTS = frozenset({"data_query", "kb_qa"})


def intent_disambiguation_enabled() -> bool:
    cfg = get_app_config().chatbot
    return bool(cfg.hitl_enabled) and bool(cfg.intent_hitl_enabled) and bool(
        getattr(cfg, "intent_disambiguation_enabled", True)
    )


def intent_hitl_max_rounds() -> int:
    return max(1, int(getattr(get_app_config().chatbot, "intent_hitl_max_rounds", 2)))


def _load_system_prompt(*, user_id: str | None) -> str:
    try:
        reg = PromptTemplateRegistry()
        tpl = reg.get_template(
            scene="chatbot_intent_disambiguation",
            user_id=user_id,
            version=None,
            default_version="v1",
        )
        content = (tpl.content if tpl else "") or ""
        if content.strip():
            return content.strip()
    except Exception:  # noqa: BLE001
        logger.warning("intent disambiguation prompt load failed", exc_info=True)
    return _FALLBACK_SYSTEM


def _parse_llm_payload(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    try:
        data = json.loads(m.group())
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _normalize_option(item: Any) -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None
    title = str(item.get("title") or "").strip()
    query = str(item.get("query") or "").strip()
    route = str(item.get("route_hint") or "").strip().lower()
    if route not in _ROUTE_HINTS:
        return None
    if not query:
        return None
    if not title:
        title = query[:16]
    return {"title": title[:32], "query": query, "route_hint": route}


def _options_cover_both_routes(options: list[dict[str, str]]) -> bool:
    routes = {o["route_hint"] for o in options}
    return "data_query" in routes and "kb_qa" in routes


def build_fallback_disambiguation(*, query: str) -> dict[str, Any]:
    """规则兜底：查数版 / 知识版 / 混合偏知识。"""
    q = (query or "").strip() or "用户问题"
    base = q.rstrip("？?。.!！")
    return {
        "analysis": (
            "您这个问题里，既像在查相关数据，也像在问原因或说明。"
            "我怕一次答偏了，想先跟您对齐一下想先解决哪一块。"
        ),
        "options": [
            {
                "title": "查相关台账/实时数据",
                "query": f"{base}（仅查询相关台账或实时数据，不要解释原因）",
                "route_hint": "data_query",
            },
            {
                "title": "查原因/机理说明",
                "query": f"{base}的常见原因与机理是什么？",
                "route_hint": "kb_qa",
            },
            {
                "title": "查规范与处理建议",
                "query": f"关于「{base}」，有哪些相关标准、规程或处理建议？",
                "route_hint": "kb_qa",
            },
        ],
        "source": "fallback_rules",
    }


def _coerce_disambiguation_result(
    data: dict[str, Any] | None,
    *,
    query: str,
) -> dict[str, Any]:
    if not data:
        return build_fallback_disambiguation(query=query)
    analysis = str(data.get("analysis") or "").strip()
    raw_opts = data.get("options")
    options: list[dict[str, str]] = []
    if isinstance(raw_opts, list):
        for item in raw_opts:
            norm = _normalize_option(item)
            if norm and norm["query"] not in {o["query"] for o in options}:
                options.append(norm)
            if len(options) >= 3:
                break
    if len(options) < 3 or not _options_cover_both_routes(options):
        fb = build_fallback_disambiguation(query=query)
        # 尽量保留已解析的合法选项，不足用兜底补齐
        for o in fb["options"]:
            if len(options) >= 3 and _options_cover_both_routes(options):
                break
            if o["query"] not in {x["query"] for x in options}:
                options.append(o)
        options = options[:3]
        if not _options_cover_both_routes(options):
            return fb
        if not analysis:
            analysis = fb["analysis"]
        return {"analysis": analysis, "options": options, "source": "llm_partial_fallback"}
    if not analysis:
        analysis = (
            "您这个问题里，既像在查相关数据，也像在问原因或说明。"
            "我怕一次答偏了，想先跟您对齐一下想先解决哪一块。"
        )
    return {"analysis": analysis, "options": options[:3], "source": "llm"}


async def generate_intent_disambiguation(
    *,
    query: str,
    intent_label: str | None = None,
    intent_confidence: float | None = None,
    intent_reason: str | None = None,
    history_summary: str | None = None,
    llm_client: Any,
    user_id: str | None = None,
) -> dict[str, Any]:
    """调用 LLM 生成消歧分析与 3 个选项；失败时返回规则兜底。"""
    q = (query or "").strip()
    if not q:
        return build_fallback_disambiguation(query=q)

    cfg = get_app_config().chatbot
    timeout_sec = max(3.0, float(getattr(cfg, "intent_disambiguation_timeout_sec", 15.0)))
    system = _load_system_prompt(user_id=user_id)
    user_msg = (
        f"用户问题：{q}\n"
        f"当前意图标签：{intent_label or '-'}\n"
        f"置信度：{intent_confidence if intent_confidence is not None else '-'}\n"
        f"意图原因：{intent_reason or '-'}\n"
        f"历史摘要：{(history_summary or '')[:600] or '-'}\n"
        "请输出 JSON。"
    )
    try:
        raw = await llm_client.chat(
            model=None,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=512,
            temperature=0.2,
            timeout=timeout_sec,
        )
        parsed = _parse_llm_payload(raw if isinstance(raw, str) else str(raw))
        result = _coerce_disambiguation_result(parsed, query=q)
        logger.info(
            "intent disambiguation done source=%s options=%s",
            result.get("source"),
            len(result.get("options") or []),
        )
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "intent disambiguation LLM failed type=%s err=%r timeout_sec=%.1f",
            type(exc).__name__,
            exc,
            timeout_sec,
            exc_info=True,
        )
        fb = build_fallback_disambiguation(query=q)
        fb["source"] = "fallback_rules"
        return fb
