"""看图诊断 台账 scope 候选排序：LLM 从候选中选 Top-K。（HITL两轮后，数据库中拉取所有选项，然后给到当前类进行 基于LLM的相关性候选匹配，然后再给到前端用户选择）"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
from typing import Any

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.llm.client import VLLMHttpClient
from app.llm.prompt_registry import PromptTemplateRegistry
from app.models.analysis_nl2sql_llm import extract_json_object_from_llm_text

logger = get_logger(__name__)

_SCENE = "img_diag_scope_candidate_rank"

_DEFAULT_PROMPT = """\
你是看图诊断台账纠错助手。用户自然语言描述的台账在业务库中未精确命中。
请仅从【候选列表】中选出与用户意图最匹配的若干项（最多 {{TOP_K}} 个），按匹配度从高到低排序。
禁止编造候选之外的名称；若都不像，可返回空 suggestions。

仅输出一个 JSON 对象，不要 markdown：
{"suggestions":[{"value":"候选原文","reason":"简短理由"}]}

【失败字段】{{FAILED_FIELD_LABEL}}（用户原值：{{USER_VALUE}}）
【已确认上级】{{MATCHED_PREFIX}}
【用户自然语言】
{{USER_TEXT}}
【候选列表】
{{CANDIDATES}}
"""


def candidate_rank_top_k() -> int:
    cfg = get_app_config().analysis
    return max(1, int(getattr(cfg, "img_diag_scope_candidate_top_k", 5) or 5))


def build_scope_candidate_rank_prompt(
    *,
    user_text: str,
    failed_field: str,
    failed_field_label: str,
    user_value: Any,
    matched_prefix: dict[str, Any],
    candidates: list[dict[str, str]],
    top_k: int | None = None,
    prompt_registry: PromptTemplateRegistry | None = None,
) -> str:
    registry = prompt_registry or PromptTemplateRegistry()
    version = (
        getattr(get_app_config().analysis, "img_diag_scope_candidate_rank_prompt_version", None)
        or "v1"
    )
    tpl = registry.get_template(_SCENE, version=str(version))
    content = (tpl.content if tpl and tpl.content else _DEFAULT_PROMPT).strip()
    k = max(1, int(top_k if top_k is not None else candidate_rank_top_k()))
    cand_lines = "\n".join(
        f"- {c.get('value')}" for c in candidates if isinstance(c, dict) and c.get("value")
    )
    return (
        content.replace("{{TOP_K}}", str(k))
        .replace("{{FAILED_FIELD_LABEL}}", str(failed_field_label or failed_field or ""))
        .replace("{{USER_VALUE}}", str(user_value if user_value is not None else ""))
        .replace("{{MATCHED_PREFIX}}", json.dumps(matched_prefix or {}, ensure_ascii=False))
        .replace("{{USER_TEXT}}", (user_text or "").strip() or "（空）")
        .replace("{{CANDIDATES}}", cand_lines or "（无候选）")
    )


def parse_candidate_rank_suggestions(
    raw_text: str,
    *,
    candidates: list[dict[str, str]],
    top_k: int,
) -> list[dict[str, Any]]:
    allowed = {
        str(c.get("value")).strip()
        for c in candidates
        if isinstance(c, dict) and str(c.get("value") or "").strip()
    }
    id_by_value = {
        str(c.get("value")).strip(): str(c.get("id") or "")
        for c in candidates
        if isinstance(c, dict) and str(c.get("value") or "").strip()
    }
    obj = extract_json_object_from_llm_text(raw_text) or {}
    raw_list = obj.get("suggestions") if isinstance(obj, dict) else None
    if not isinstance(raw_list, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or "").strip()
        if not value or value not in allowed or value in seen:
            continue
        seen.add(value)
        out.append(
            {
                "id": id_by_value.get(value) or str(len(out) + 1),
                "value": value,
                "label": value,
                "rank": len(out) + 1,
                "reason": str(item.get("reason") or "").strip(),
            }
        )
        if len(out) >= top_k:
            break
    return out


async def rank_scope_candidates_async(
    *,
    user_text: str,
    failed_field: str,
    failed_field_label: str,
    user_value: Any,
    matched_prefix: dict[str, Any],
    candidates: list[dict[str, str]],
    top_k: int | None = None,
    llm_client: VLLMHttpClient | None = None,
    prompt_registry: PromptTemplateRegistry | None = None,
) -> list[dict[str, Any]]:
    if not candidates:
        return []
    k = max(1, int(top_k if top_k is not None else candidate_rank_top_k()))
    prompt = build_scope_candidate_rank_prompt(
        user_text=user_text,
        failed_field=failed_field,
        failed_field_label=failed_field_label,
        user_value=user_value,
        matched_prefix=matched_prefix,
        candidates=candidates,
        top_k=k,
        prompt_registry=prompt_registry,
    )
    client = llm_client or VLLMHttpClient()
    model = get_app_config().llm.default_model
    timeout = float(
        getattr(get_app_config().analysis, "img_diag_scope_candidate_rank_timeout_s", 20.0) or 20.0
    )
    try:
        raw = await client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=512,
            temperature=0.0,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("img_diag scope candidate rank LLM failed: %s", exc)
        # 降级：按列表顺序取前 K
        return [
            {
                "id": str(c.get("id") or i + 1),
                "value": str(c.get("value")),
                "label": str(c.get("label") or c.get("value")),
                "rank": i + 1,
                "reason": "LLM 不可用，按候选列表顺序推荐",
            }
            for i, c in enumerate(candidates[:k])
        ]
    return parse_candidate_rank_suggestions(raw or "", candidates=candidates, top_k=k)


def rank_scope_candidates_sync(**kwargs: Any) -> list[dict[str, Any]]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(rank_scope_candidates_async(**kwargs))
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(asyncio.run, rank_scope_candidates_async(**kwargs))
        return fut.result()
