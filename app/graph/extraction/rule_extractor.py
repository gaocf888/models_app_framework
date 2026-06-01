from __future__ import annotations

import re

from app.core.config import GraphRAGConfig, GraphSchemaConfig
from app.graph.extraction.types import ExtractedEntity, ExtractedGraphPayload


class RuleGraphExtractor:
    """规则实体抽取（调试/回退用）。"""

    def __init__(self, cfg: GraphRAGConfig) -> None:
        self._cfg = cfg

    def extract(self, text: str, schema: GraphSchemaConfig | None = None) -> ExtractedGraphPayload:
        entities = self._extract_entities(text)
        return ExtractedGraphPayload(entities=entities, relations=[])

    def _extract_entities(self, text: str) -> list[ExtractedEntity]:
        min_len = max(1, self._cfg.entity_min_len)
        max_len = max(min_len, self._cfg.entity_max_len)
        zh_max = max(min_len, min(max_len, self._cfg.zh_entity_max_len))
        en_min = max(1, self._cfg.en_entity_min_len)
        en_max = max(en_min, min(max_len, self._cfg.en_entity_max_len))
        zh_terms = re.findall(rf"[\u4e00-\u9fff]{{{min_len},{zh_max}}}", text or "")
        en_terms = re.findall(rf"\b[A-Z][a-zA-Z0-9]{{{en_min - 1},{en_max}}}\b", text or "")
        seen: set[str] = set()
        out: list[ExtractedEntity] = []
        for term in zh_terms + en_terms:
            name = term.strip()
            if len(name) < min_len or len(name) > max_len:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(
                ExtractedEntity(
                    type="Concept",
                    id=key,
                    name=name,
                    properties={"name": name, "norm_name": key},
                )
            )
            if len(out) >= max(1, self._cfg.max_entities_per_chunk):
                break
        return out
