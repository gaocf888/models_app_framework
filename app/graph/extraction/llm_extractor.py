from __future__ import annotations

import json
import re
from app.graph.paths import resolve_project_path

import httpx
import yaml

from app.core.config import GraphRAGConfig, GraphSchemaConfig, get_app_config
from app.core.logging import get_logger
from app.graph.extraction.types import ExtractedEntity, ExtractedGraphPayload, ExtractedRelation
from app.llm.client import openai_chat_completions_url

logger = get_logger(__name__)

_PROMPT_CACHE: dict[str, Any] | None = None


def _load_prompts() -> dict[str, Any]:
    global _PROMPT_CACHE
    if _PROMPT_CACHE is not None:
        return _PROMPT_CACHE
    path = resolve_project_path("configs/graph_extraction.yaml")
    if not path.is_file():
        _PROMPT_CACHE = {}
        return _PROMPT_CACHE
    _PROMPT_CACHE = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _PROMPT_CACHE


def _extract_json_block(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, flags=re.IGNORECASE)
    if fence:
        raw = fence.group(1).strip()
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(raw[start : end + 1])
                return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                return {}
    return {}


def _normalize_entity_id(name: str | None, ent_id: str | None) -> str:
    base = (ent_id or name or "").strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^a-z0-9_\u4e00-\u9fff-]", "", base)
    return base or "unknown"


class LLMGraphExtractor:
    """基于 LLM 的实体关系抽取。"""

    def __init__(self, cfg: GraphRAGConfig) -> None:
        self._cfg = cfg

    def extract(self, text: str, schema: GraphSchemaConfig | None = None) -> ExtractedGraphPayload:
        prompts = _load_prompts().get("entity_relation_extraction") or {}
        system = str(prompts.get("system") or "").strip()
        user_tpl = str(
            prompts.get("user_template")
            or "文本片段：\n---\n{text}\n---\n{schema_hint}\n请输出 JSON。"
        )
        schema_hint = self._build_schema_hint(schema)
        user_content = (
            user_tpl.replace("{text}", text or "").replace("{schema_hint}", schema_hint)
        )

        last_err: Exception | None = None
        retries = max(0, self._cfg.llm_max_retries)
        for attempt in range(retries + 1):
            try:
                content = self._call_llm(system=system, user_content=user_content)
                return self._parse_payload(content, schema)
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                logger.warning(
                    "LLM graph extraction attempt %s failed: %s",
                    attempt + 1,
                    exc,
                )
        if last_err:
            raise last_err
        return ExtractedGraphPayload()

    def extract_batch(
        self,
        texts: list[str],
        schema: GraphSchemaConfig | None = None,
    ) -> list[ExtractedGraphPayload]:
        out: list[ExtractedGraphPayload] = []
        batch_size = max(1, self._cfg.llm_batch_size)
        for i in range(0, len(texts), batch_size):
            for text in texts[i : i + batch_size]:
                try:
                    out.append(self.extract(text, schema=schema))
                except Exception as exc:  # noqa: BLE001
                    if self._cfg.extraction_fallback_rule:
                        from app.graph.extraction.rule_extractor import RuleGraphExtractor

                        logger.warning("LLM chunk extraction failed, rule fallback: %s", exc)
                        out.append(RuleGraphExtractor(self._cfg).extract(text, schema=schema))
                    else:
                        logger.warning("LLM chunk extraction failed (no fallback): %s", exc)
                        out.append(ExtractedGraphPayload())
        return out

    def _build_schema_hint(self, schema: GraphSchemaConfig | None) -> str:
        if schema is None or not schema.enabled:
            return "Schema 提示：无强制本体，type 可使用 Concept / Equipment / Fault 等通用类型。"
        node_types = ", ".join(schema.nodes.keys()) or "Concept"
        rel_types = ", ".join(schema.relations.keys()) or "RELATED"
        return f"Schema 提示：节点类型优先使用 [{node_types}]；关系类型优先使用 [{rel_types}]。"

    def _resolve_llm_target(self) -> tuple[str, str, str | None]:
        app_cfg = get_app_config()
        llm_cfg = app_cfg.llm
        model_key = self._cfg.llm_model or llm_cfg.default_model
        model_cfg = llm_cfg.models.get(model_key)
        if model_cfg is None and llm_cfg.default_model in llm_cfg.models:
            model_key = llm_cfg.default_model
            model_cfg = llm_cfg.models[model_key]
        if model_cfg is None:
            raise ValueError("no LLM model configured for graph extraction")
        endpoint = self._cfg.llm_endpoint or model_cfg.endpoint
        return model_key, endpoint, model_cfg.api_key

    def _call_llm(self, *, system: str, user_content: str) -> str:
        model_key, endpoint, api_key = self._resolve_llm_target()
        app_cfg = get_app_config()
        model_cfg = app_cfg.llm.models[model_key]
        url = openai_chat_completions_url(endpoint)
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user_content})
        payload: dict[str, Any] = {
            "model": model_cfg.model_id,
            "messages": messages,
            "max_tokens": self._cfg.llm_max_tokens,
            "temperature": 0.1,
        }
        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        timeout = httpx.Timeout(
            connect=min(60.0, self._cfg.llm_timeout_s),
            read=max(1.0, self._cfg.llm_timeout_s),
            write=min(600.0, self._cfg.llm_timeout_s),
            pool=max(1.0, self._cfg.llm_timeout_s),
        )
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return content if isinstance(content, str) else str(content)

    def _parse_payload(self, content: str, schema: GraphSchemaConfig | None) -> ExtractedGraphPayload:
        data = _extract_json_block(content)
        entities_raw = data.get("entities") or []
        relations_raw = data.get("relations") or []
        allowed_node_types = set(schema.nodes.keys()) if schema and schema.enabled else None
        allowed_rel_types = set(schema.relations.keys()) if schema and schema.enabled else None

        entities: list[ExtractedEntity] = []
        seen_ids: set[str] = set()
        if isinstance(entities_raw, list):
            for item in entities_raw:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                ent_id = _normalize_entity_id(name, str(item.get("id") or ""))
                ent_type = str(item.get("type") or "Concept").strip() or "Concept"
                if allowed_node_types and ent_type not in allowed_node_types:
                    # schema 开启时映射到最接近的 Concept 或跳过
                    if "Concept" in allowed_node_types:
                        ent_type = "Concept"
                    else:
                        continue
                if ent_id in seen_ids:
                    continue
                seen_ids.add(ent_id)
                entities.append(
                    ExtractedEntity(
                        type=ent_type,
                        id=ent_id,
                        name=name or ent_id,
                        properties={"name": name or ent_id, "norm_name": ent_id},
                    )
                )
                if len(entities) >= max(1, self._cfg.max_entities_per_chunk):
                    break

        id_set = {e.id for e in entities if e.id}
        relations: list[ExtractedRelation] = []
        if isinstance(relations_raw, list):
            for item in relations_raw:
                if not isinstance(item, dict):
                    continue
                rel_type = str(item.get("type") or "RELATED").strip().upper() or "RELATED"
                if allowed_rel_types and rel_type not in allowed_rel_types:
                    mapped = schema.relations.get(rel_type) if schema else None
                    if mapped:
                        rel_type = mapped.type
                    elif "RELATED" in (allowed_rel_types or set()):
                        rel_type = "RELATED"
                    else:
                        continue
                source_id = _normalize_entity_id(None, str(item.get("source_id") or ""))
                target_id = _normalize_entity_id(None, str(item.get("target_id") or ""))
                if source_id not in id_set or target_id not in id_set or source_id == target_id:
                    continue
                relations.append(
                    ExtractedRelation(
                        type=rel_type,
                        source_id=source_id,
                        target_id=target_id,
                        properties={"source": "llm"},
                    )
                )
        return ExtractedGraphPayload(entities=entities, relations=relations)
