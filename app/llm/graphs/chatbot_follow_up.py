"""
智能客服：回答后的关联问题推荐（规则 + RAG 片段延伸 + LLM 补全）。

合并策略：去重、截断长度、总量受配置上限约束。
"""

from __future__ import annotations

import json
import re
from typing import Any, List

from app.core.logging import get_logger

logger = get_logger(__name__)

_TOPIC_FOLLOW_UPS: dict[str, list[str]] = {
    "爆管": [
        "如何控制过热爆管风险？",
        "类似爆管案例的处理经验有哪些？",
        "爆管后检修与试验应注意什么？",
    ],
    "过热": [
        "过热器超温的常见原因有哪些？",
        "运行中如何监视与调整汽温？",
        "相关标准对汽温偏差有何要求？",
    ],
    "腐蚀": [
        "烟气侧腐蚀与应力腐蚀如何区分？",
        "防腐蚀检修要点有哪些？",
        "哪些部位需要重点测厚？",
    ],
    "泄漏": [
        "承压部件泄漏的应急处理原则是什么？",
        "如何定位泄漏点并安排隔离？",
        "泄漏后恢复运行的前置条件有哪些？",
    ],
    "检修": [
        "该型号锅炉检修周期如何确定？",
        "检修项目与验收要点有哪些？",
        "检修记录应包含哪些关键字段？",
    ],
    "台账": [
        "如何在台账中快速定位某台炉的关键参数？",
        "台账与检修记录如何关联查询？",
        "设备变更后台账如何同步？",
    ],
    "标准": [
        "该问题涉及哪些国标/行标条款？",
        "标准条款与现场规程不一致时如何处理？",
        "合规性审查通常检查哪些材料？",
    ],
}


def _rule_based_suggestions(query: str, max_n: int) -> list[str]:
    q = (query or "").strip()
    if not q or max_n <= 0:
        return []
    out: list[str] = []
    for key, cands in _TOPIC_FOLLOW_UPS.items():
        if key in q:
            for c in cands:
                if c not in out:
                    out.append(c)
                if len(out) >= max_n:
                    return out
    return out


def _snippet_seeds(snippets: List[str], max_n: int) -> list[str]:
    if max_n <= 0 or not snippets:
        return []
    seeds: list[str] = []
    for s in snippets[:3]:
        line = (s or "").strip().split("\n", 1)[0].strip()
        line = re.sub(r"\s+", " ", line)
        if len(line) > 80:
            line = line[:77] + "…"
        if len(line) < 12:
            continue
        seeds.append(f"结合知识库：{line} 还可以了解什么？")
        if len(seeds) >= max_n:
            break
    return seeds


def _parse_llm_questions(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
        if isinstance(data, dict) and isinstance(data.get("questions"), list):
            return [str(x).strip() for x in data["questions"] if str(x).strip()]
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            data = json.loads(m.group())
            if isinstance(data, dict) and isinstance(data.get("questions"), list):
                return [str(x).strip() for x in data["questions"] if str(x).strip()]
        except json.JSONDecodeError:
            return []
    return []


async def build_suggested_questions(
    *,
    query: str,
    answer: str,
    context_snippets: List[str],
    intent_label: str,
    llm_client: Any,
    max_total: int = 5,
) -> list[str]:
    """
    组合生成关联问题；`intent_label==clarify` 时返回较少或不调用 LLM。
    """
    max_total = max(1, min(10, max_total))
    collected: list[str] = []

    rule_n = 3 if intent_label != "clarify" else 1
    collected.extend(_rule_based_suggestions(query, rule_n))

    snip_n = 2 if intent_label == "kb_qa" else 1
    collected.extend(_snippet_seeds(context_snippets, snip_n))

    # 去重（保序）
    seen: set[str] = set()
    uniq: list[str] = []
    for x in collected:
        if x not in seen:
            seen.add(x)
            uniq.append(x)

    need_llm = intent_label in {"kb_qa", "data_query"} and len(uniq) < max_total
    if need_llm:
        ans_excerpt = (answer or "").strip()[:1200]
        sys_msg = (
            "你是电厂锅炉领域助手。只输出一个 JSON 对象，不要 Markdown。"
            '格式：{"questions":["问题1","问题2","问题3"]}。'
            "要求：3 条中文短问句，与用户主题相关，可引导查规范/案例/检修/运行；"
            "不要重复用户原话；不要包含敏感违规内容。"
        )
        user_msg = f"用户问：{query}\n助手答摘要：{ans_excerpt}\n请生成 questions。"
        try:
            raw = await llm_client.chat(
                model=None,
                messages=[
                    {"role": "system", "content": sys_msg},
                    {"role": "user", "content": user_msg},
                ],
            )
            llm_qs = _parse_llm_questions(raw if isinstance(raw, str) else str(raw))
            for q in llm_qs:
                if q not in seen and len(uniq) < max_total:
                    seen.add(q)
                    uniq.append(q)
        except Exception as exc:  # noqa: BLE001
            logger.warning("follow_up LLM failed: %s", exc)

    return uniq[:max_total]
