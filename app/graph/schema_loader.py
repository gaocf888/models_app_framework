from __future__ import annotations

"""
Graph Schema YAML 加载器。

将 ``GRAPH_SCHEMA_CONFIG_PATH`` 指向的 YAML 解析为 ``GraphSchemaConfig``。
"""

from pathlib import Path
from typing import Any

import yaml

from app.graph.paths import resolve_project_path

from app.core.config import (
    GraphRAGConfig,
    GraphSchemaConfig,
    GraphSchemaNodeConfig,
    GraphSchemaRelationConfig,
)
from app.core.logging import get_logger

logger = get_logger(__name__)


def _default_schema_path(cfg: GraphRAGConfig) -> str:
    return (cfg.schema_config_path or "configs/graph_schema.yaml").strip()


def load_graph_schema(path: str | Path, *, fail_fast: bool = False) -> GraphSchemaConfig:
    """
    从 YAML 文件加载 Graph Schema。

    - 文件不存在：返回 ``GraphSchemaConfig(enabled=False)`` 并记 info 日志；
    - 解析失败：fail_fast=True 时抛出 ValueError；否则返回 disabled schema。
    """
    p = resolve_project_path(path)
    if not p.is_file():
        logger.info("graph schema file not found: %s (using schema-less mode)", p)
        return GraphSchemaConfig(enabled=False)

    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        msg = f"failed to parse graph schema YAML: {p}: {exc}"
        if fail_fast:
            raise ValueError(msg) from exc
        logger.warning(msg)
        return GraphSchemaConfig(enabled=False)

    if not isinstance(raw, dict):
        msg = f"graph schema root must be a mapping: {p}"
        if fail_fast:
            raise ValueError(msg)
        logger.warning(msg)
        return GraphSchemaConfig(enabled=False)

    enabled = bool(raw.get("enabled", False))
    nodes_raw = raw.get("nodes") or {}
    relations_raw = raw.get("relations") or {}

    nodes: dict[str, GraphSchemaNodeConfig] = {}
    if isinstance(nodes_raw, dict):
        for key, val in nodes_raw.items():
            if not isinstance(val, dict):
                continue
            nodes[str(key)] = GraphSchemaNodeConfig(
                name=str(val.get("name") or key),
                labels=[str(x) for x in (val.get("labels") or [])],
                key_fields=[str(x) for x in (val.get("key_fields") or [])],
                properties=[str(x) for x in (val.get("properties") or [])],
            )

    relations: dict[str, GraphSchemaRelationConfig] = {}
    if isinstance(relations_raw, dict):
        for key, val in relations_raw.items():
            if not isinstance(val, dict):
                continue
            relations[str(key)] = GraphSchemaRelationConfig(
                name=str(val.get("name") or key),
                type=str(val.get("type") or key),
                from_node=str(val.get("from_node") or val.get("from") or ""),
                to_node=str(val.get("to_node") or val.get("to") or ""),
                properties=[str(x) for x in (val.get("properties") or [])],
            )

    schema = GraphSchemaConfig(enabled=enabled, nodes=nodes, relations=relations)
    logger.info(
        "loaded graph schema from %s enabled=%s nodes=%s relations=%s",
        p,
        schema.enabled,
        len(schema.nodes),
        len(schema.relations),
    )
    return schema


def apply_schema_to_graph_config(cfg: GraphRAGConfig, *, fail_fast: bool = True) -> GraphRAGConfig:
    """在 GraphRAG 启用时将 YAML schema 合并进配置对象。"""
    path = _default_schema_path(cfg)
    cfg.schema = load_graph_schema(path, fail_fast=fail_fast)
    return cfg


def reload_graph_schema(cfg: GraphRAGConfig) -> GraphSchemaConfig:
    """Admin 热加载入口。"""
    path = _default_schema_path(cfg)
    schema = load_graph_schema(path, fail_fast=True)
    cfg.schema = schema
    return schema


def schema_summary(schema: GraphSchemaConfig) -> dict[str, Any]:
    return {
        "enabled": schema.enabled,
        "node_types": list(schema.nodes.keys()),
        "relation_types": list(schema.relations.keys()),
        "nodes_count": len(schema.nodes),
        "relations_count": len(schema.relations),
    }
