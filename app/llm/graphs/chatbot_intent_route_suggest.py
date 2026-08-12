"""智能客服首轮意图 HITL：LLM 筛选可用路线按钮（子集或全量）。"""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.llm.graphs.chatbot_hitl_display import (
    ACTION_ROUTE_CLARIFY,
    ACTION_ROUTE_DATA_QUERY,
    ACTION_ROUTE_HYBRID,
    ACTION_ROUTE_KB_QA,
    INTENT_ROUTE_BUTTONS,
)
from app.llm.prompt_registry import PromptTemplateRegistry

logger = get_logger(__name__)

_ROUTE_KEYS = frozenset({"data_query", "kb_qa", "hybrid_qa"})
_ROUTE_TO_ACTION = {
    "data_query": ACTION_ROUTE_DATA_QUERY,
    "kb_qa": ACTION_ROUTE_KB_QA,
    "hybrid_qa": ACTION_ROUTE_HYBRID,
}
_ACTION_ORDER = [
    ACTION_ROUTE_DATA_QUERY,
    ACTION_ROUTE_KB_QA,
    ACTION_ROUTE_HYBRID,
    ACTION_ROUTE_CLARIFY,
]

_FALLBACK_SYSTEM = """你是电厂锅炉领域的智能客服助手。用户意图边界不清，系统将弹出路线确认按钮。
请只输出一个 JSON 对象，不要 Markdown。
格式：
{"routes":["data_query","kb_qa","hybrid_qa"]}
硬性要求：
1) routes 只能从 data_query（查电厂内部实时/台账数据）、kb_qa（基于专业知识分析）、hybrid_qa（综合实时数据+专业知识）中多选；
2) 根据用户当前问题与历史，只保留相关路线：纯知识/处置类勿带 data_query；纯查数类勿硬塞无关知识路线；边界不清可返回多项或全部；
3) routes 至少 1 条、最多 3 条；不要输出 clarify（补充问题由系统固定追加）；
4) 不要编造其它键名，不要输出按钮文案。"""


def intent_route_suggest_enabled() -> bool:
    cfg = get_app_config().chatbot
    return bool(cfg.hitl_enabled) and bool(cfg.intent_hitl_enabled) and bool(
        getattr(cfg, "intent_route_suggest_enabled", True)
    )


def _load_system_prompt(*, user_id: str | None) -> str:
    try:
        reg = PromptTemplateRegistry()
        tpl = reg.get_template(
            scene="chatbot_intent_route_suggest",
            user_id=user_id,
            version=None,
            default_version="v1",
        )
        content = (tpl.content if tpl else "") or ""
        if content.strip():
            return content.strip()
    except Exception:  # noqa: BLE001
        logger.warning("intent route suggest prompt load failed", exc_info=True)
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


def _button_by_id(action_id: str) -> dict[str, str] | None:
    for btn in INTENT_ROUTE_BUTTONS:
        if btn["id"] == action_id:
            return {"id": btn["id"], "label": btn["label"]}
    return None


def build_fallback_route_buttons() -> list[dict[str, str]]:
    return [{"id": b["id"], "label": b["label"]} for b in INTENT_ROUTE_BUTTONS]


def coerce_routes_to_ui_buttons(routes: Any) -> list[dict[str, str]] | None:
    """将 LLM routes 转为 ui_buttons；非法或空则返回 None（调用方回退全量）。"""
    if not isinstance(routes, list):
        return None
    selected_actions: list[str] = []
    seen: set[str] = set()
    for item in routes:
        key = str(item or "").strip().lower()
        if key not in _ROUTE_KEYS:
            continue
        action = _ROUTE_TO_ACTION[key]
        if action in seen:
            continue
        seen.add(action)
        selected_actions.append(action)
    if not selected_actions:
        return None
    # 固定顺序 + 始终追加「我先补充问题」
    ordered: list[dict[str, str]] = []
    for action in _ACTION_ORDER:
        if action == ACTION_ROUTE_CLARIFY:
            btn = _button_by_id(ACTION_ROUTE_CLARIFY)
            if btn:
                ordered.append(btn)
            continue
        if action in seen:
            btn = _button_by_id(action)
            if btn:
                ordered.append(btn)
    if len(ordered) < 2:  # 至少一个业务路线 + clarify
        return None
    return ordered


def _coerce_suggest_result(parsed: dict[str, Any] | None) -> dict[str, Any]:
    buttons = coerce_routes_to_ui_buttons((parsed or {}).get("routes"))
    if not buttons:
        return {
            "ui_buttons": build_fallback_route_buttons(),
            "routes": ["data_query", "kb_qa", "hybrid_qa"],
            "source": "fallback_full",
        }
    routes_out: list[str] = []
    for b in buttons:
        if b["id"] == ACTION_ROUTE_DATA_QUERY:
            routes_out.append("data_query")
        elif b["id"] == ACTION_ROUTE_KB_QA:
            routes_out.append("kb_qa")
        elif b["id"] == ACTION_ROUTE_HYBRID:
            routes_out.append("hybrid_qa")
    return {"ui_buttons": buttons, "routes": routes_out, "source": "llm"}


async def generate_intent_route_buttons(
    *,
    query: str,
    intent_label: str | None = None,
    intent_confidence: float | None = None,
    intent_reason: str | None = None,
    history_summary: str | None = None,
    llm_client: Any,
    user_id: str | None = None,
) -> dict[str, Any]:
    """调用 LLM 筛选首轮 HITL 路线按钮；失败时回退全部四钮。"""
    q = (query or "").strip()
    if not q:
        return {
            "ui_buttons": build_fallback_route_buttons(),
            "routes": ["data_query", "kb_qa", "hybrid_qa"],
            "source": "fallback_full",
        }

    cfg = get_app_config().chatbot
    timeout_sec = max(3.0, float(getattr(cfg, "intent_route_suggest_timeout_sec", 12.0)))
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
            max_tokens=256,
            temperature=0.1,
            timeout=timeout_sec,
        )
        parsed = _parse_llm_payload(raw if isinstance(raw, str) else str(raw))
        result = _coerce_suggest_result(parsed)
        if result.get("source") == "llm":
            result = dict(result)
            result["source"] = "llm"
        logger.info(
            "intent route suggest done source=%s routes=%s buttons=%s",
            result.get("source"),
            result.get("routes"),
            len(result.get("ui_buttons") or []),
        )
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "intent route suggest LLM failed type=%s err=%r timeout_sec=%.1f",
            type(exc).__name__,
            exc,
            timeout_sec,
            exc_info=True,
        )
        return {
            "ui_buttons": build_fallback_route_buttons(),
            "routes": ["data_query", "kb_qa", "hybrid_qa"],
            "source": "fallback_full",
        }
