"""P1-1：规则未锁库时的轻量 LLM 补召回。结果必须 ∈ 注册表，否则仍 HITL。"""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.data_query_agent.catalog import LibraryCatalog, LibraryDef
from app.data_query_agent.library_intent import LibraryIntentResult

logger = get_logger(__name__)

_JSON_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def _catalog_lines(catalog: LibraryCatalog) -> str:
    rows = []
    for lib in catalog.libraries:
        syn = "、".join(lib.synonyms[:8])
        rows.append(f"- {lib.id} {lib.display_name}（表 {lib.table}；同义词：{syn}）")
    return "\n".join(rows)


def _parse_library_id(text: str, catalog: LibraryCatalog) -> LibraryDef | None:
    raw = (text or "").strip()
    if not raw:
        return None
    blob = raw
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    if fence:
        blob = fence.group(1).strip()
    match = _JSON_RE.search(blob)
    if match:
        blob = match.group(0)
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    lid = str(data.get("library_id") or "").strip().lower()
    if not lid or lid in {"null", "none", "unknown"}:
        return None
    return catalog.get(lid)


async def supplement_library_intent_llm(
    query: str,
    catalog: LibraryCatalog,
    *,
    llm_client: Any | None = None,
) -> LibraryIntentResult | None:
    """仅在规则零命中时调用。幻觉 id / 失败 / 关闭开关 → None（调用方继续 HITL）。"""
    cfg = get_app_config().data_query_agent
    if not bool(getattr(cfg, "library_llm_enabled", True)):
        return None
    prompt = (
        "你是北京市沉降监测「监测库」分类器。只能从下列 library_id 中选一个；"
        "选不出则 library_id 为 null。禁止编造列表外的库名。\n"
        f"{_catalog_lines(catalog)}\n\n"
        f"用户问句：{query}\n"
        '只输出一行 JSON，例如 {"library_id":"dxswj"} 或 {"library_id":null}。'
    )
    try:
        client = llm_client
        if client is None:
            from app.llm.client import VLLMHttpClient

            client = VLLMHttpClient(timeout=float(getattr(cfg, "library_llm_timeout_seconds", 8) or 8))
        model = get_app_config().llm.default_model
        text = await client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=64,
            temperature=0,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("data_query_agent library LLM failed: %s", exc)
        return None
    lib = _parse_library_id(str(text or ""), catalog)
    if lib is None:
        logger.info("data_query_agent library LLM unused or invalid query=%s", query[:80])
        return None
    return LibraryIntentResult(ok=True, library=lib, source="llm")
