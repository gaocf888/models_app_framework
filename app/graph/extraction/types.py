from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExtractedEntity:
    type: str
    id: str | None
    name: str | None
    properties: dict = field(default_factory=dict)


@dataclass
class ExtractedRelation:
    type: str
    source_id: str
    target_id: str
    properties: dict = field(default_factory=dict)


@dataclass
class ExtractedGraphPayload:
    entities: list[ExtractedEntity] = field(default_factory=list)
    relations: list[ExtractedRelation] = field(default_factory=list)
