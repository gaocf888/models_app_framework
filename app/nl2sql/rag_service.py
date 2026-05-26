from __future__ import annotations

import os
from typing import TYPE_CHECKING, List

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.graph.query_service import GraphQueryService
from app.rag.models import RetrievedChunk
from app.rag.retrieval_policy import RetrievalPolicy
from app.rag.rag_service import RAGService

if TYPE_CHECKING:
    from app.nl2sql.qa_feedback import NL2SQLQARetrievalContext

logger = get_logger(__name__)


class NL2SQLRAGService:
    """
    NL2SQL 专用 RAG 服务。

    - 使用专用命名空间区分 Schema/业务知识/问答样例；
    - 对外提供摄入与多命名空间联合检索能力。
    """

    NS_SCHEMA = "nl2sql_schema"
    NS_BIZ = "nl2sql_biz_knowledge"
    NS_QA = "nl2sql_qa_examples"

    def __init__(self, rag_service: RAGService | None = None) -> None:
        self._rag = rag_service or RAGService()
        rag_cfg = get_app_config().rag
        self._policy = RetrievalPolicy(rag_cfg.graph)
        if rag_cfg.graph.enabled:
            try:
                self._graph_query = GraphQueryService(rag_cfg.graph)
            except Exception:
                self._graph_query = None
        else:
            self._graph_query = None

    def index_schema_snippets(self, snippets: List[str]) -> None:
        """
        摄入与 Schema 相关的片段到 nl2sql_schema 命名空间。
        """
        self._rag.index_texts(snippets, namespace=self.NS_SCHEMA)

    def index_biz_knowledge(self, snippets: List[str]) -> None:
        """
        摄入业务知识说明到 nl2sql_biz_knowledge 命名空间。
        """
        self._rag.index_texts(snippets, namespace=self.NS_BIZ)

    def index_qa_examples(self, snippets: List[str]) -> None:
        """
        摄入 NL2SQL 问答样例到 nl2sql_qa_examples 命名空间。
        """
        self._rag.index_texts(snippets, namespace=self.NS_QA)

    def upsert_auto_feedback_qa_pair(
        self,
        *,
        question: str,
        sql: str,
        data_source_fp: str,
        schema_fp: str,
        policy_fp: str,
        analysis_type: str | None,
        plan_item_id: str | None,
        plan_template_version: str | None,
        prompt_prefix_snapshot: str | None,
    ) -> str | None:
        """系统自动写入闭环：校验通过后调用；五元组已存在则跳过，返回 None。"""
        from app.nl2sql.qa_feedback import upsert_nl2sql_auto_qa_pair

        return upsert_nl2sql_auto_qa_pair(
            self._rag,
            question=question,
            sql=sql,
            data_source_fp=data_source_fp,
            schema_fp=schema_fp,
            policy_fp=policy_fp,
            analysis_type=analysis_type,
            plan_item_id=plan_item_id,
            plan_template_version=plan_template_version,
            prompt_prefix_snapshot=prompt_prefix_snapshot,
        )

    def retrieve(
        self,
        question: str,
        top_k: int | None = None,
        *,
        nl2sql_qa_context: NL2SQLQARetrievalContext | None = None,
    ) -> List[str]:
        """
        兼容接口：针对 NL2SQL 查询，从多命名空间联合检索上下文片段（字符串）。
        新链路请优先使用 `retrieve_chunks` 获取标准结构。
        """
        chunks = self.retrieve_chunks(question, top_k=top_k, nl2sql_qa_context=nl2sql_qa_context)
        rendered = [self._render_chunk(c) for c in chunks]
        # 去重（保留顺序）
        seen = set()
        unique_results: List[str] = []
        for t in rendered:
            if t not in seen:
                seen.add(t)
                unique_results.append(t)
        logger.info(
            "NL2SQLRAG.retrieve string_snippets=%d (from %d chunks)",
            len(unique_results),
            len(chunks),
        )
        return unique_results

    def retrieve_chunks(
        self,
        question: str,
        top_k: int | None = None,
        *,
        nl2sql_qa_context: NL2SQLQARetrievalContext | None = None,
    ) -> List[RetrievedChunk]:
        """
        标准检索接口：返回 RetrievedChunk（含 doc/namespace/section 等元信息）。
        nl2sql_qa_context：启用时按数据源/schema 指纹过滤 nl2sql_qa_examples 中的系统自动写入片段；
        综合分析 acquire_data（analysis_type + plan_item_id + plan_template_version 齐全）时
        对 QA 命名空间优先按五元组 doc_name 精确取 1 条，未命中再回退向量检索。
        """
        from app.nl2sql.qa_feedback import (
            fetch_nl2sql_qa_chunks_by_slot,
            nl2sql_qa_slot_lookup_eligible,
            qa_chunk_passes_retrieval_filter,
        )

        profile = get_app_config().rag.scene_profiles.nl2sql
        top = top_k if top_k is not None else profile.top_k
        schema_ns_top = int(os.getenv("NL2SQL_SCHEMA_NAMESPACE_TOP_K", str(max(top + 6, 12))))
        # 各命名空间 chunk 上限：减少无关片段、稳定排序后的上下文（可调大恢复旧行为）
        schema_chunk_cap = max(1, int(os.getenv("NL2SQL_RAG_MAX_SCHEMA_CHUNKS", "10")))
        biz_chunk_cap = max(1, int(os.getenv("NL2SQL_RAG_MAX_BIZ_CHUNKS", "6")))
        qa_chunk_cap = max(1, int(os.getenv("NL2SQL_RAG_MAX_QA_CHUNKS", "6")))
        prefetch_mult = max(1, int(os.getenv("NL2SQL_QA_RAG_PREFETCH_MULT", "4")))
        ns_schema = min(schema_ns_top, schema_chunk_cap)
        ns_biz = min(top, biz_chunk_cap)
        ns_qa = min(top, qa_chunk_cap)
        results: List[RetrievedChunk] = []
        decision = self._policy.decide(question)
        per_ns_vector: dict[str, int] = {}
        per_ns_graph: dict[str, int] = {}
        qa_slot_lookup: str = "n/a"

        for ns in (self.NS_SCHEMA, self.NS_BIZ, self.NS_QA):
            qa_used_slot = False
            if ns == self.NS_QA and nl2sql_qa_slot_lookup_eligible(nl2sql_qa_context):
                slot_chunks = fetch_nl2sql_qa_chunks_by_slot(
                    self._rag,
                    nl2sql_qa_context,  # type: ignore[arg-type]
                    max_chunks=ns_qa,
                )
                if slot_chunks:
                    qa_used_slot = True
                    qa_slot_lookup = "hit"
                    per_ns_vector[ns] = len(slot_chunks)
                    results.extend(slot_chunks)
                elif qa_slot_lookup != "hit":
                    qa_slot_lookup = "miss_fallback_vector"

            # 向量侧标准结构优先保留（含 doc/section 元信息）。
            if decision.mode != "graph" and not qa_used_slot:
                if ns == self.NS_SCHEMA:
                    ns_top = ns_schema
                elif ns == self.NS_BIZ:
                    ns_top = ns_biz
                else:
                    ns_top = min(ns_qa * prefetch_mult, 48) if nl2sql_qa_context is not None else ns_qa
                chunks = self._rag.retrieve_chunks(
                    query=question,
                    top_k=ns_top,
                    namespace=ns,
                    scene="nl2sql",
                )
                if ns == self.NS_QA and nl2sql_qa_context is not None:
                    chunks = [c for c in chunks if qa_chunk_passes_retrieval_filter(c, nl2sql_qa_context)]
                    chunks = chunks[:ns_qa]
                per_ns_vector[ns] = per_ns_vector.get(ns, 0) + len(chunks)
                results.extend(chunks)
            # 图侧事实按统一策略层决策补充。
            if decision.mode != "vector" and self._graph_query is not None and not qa_used_slot:
                graph_facts = self._graph_query.query_relevant_facts(
                    question=question,
                    namespace=ns,
                    max_hops=decision.graph_hops,
                    max_items=decision.max_graph_items,
                )
                if ns == self.NS_SCHEMA:
                    g_top = ns_schema
                elif ns == self.NS_BIZ:
                    g_top = ns_biz
                else:
                    g_top = min(ns_qa * prefetch_mult, 48) if nl2sql_qa_context is not None else ns_qa
                gf = graph_facts[:g_top]
                per_ns_graph[ns] = per_ns_graph.get(ns, 0) + len(gf)
                for idx, fact in enumerate(gf):
                    results.append(
                        RetrievedChunk(
                            text=fact,
                            doc_name="__graph_fact__",
                            namespace=ns,
                            chunk_id=f"graph:{ns}:{idx}:{abs(hash(fact))}",
                            score=decision.graph_weight,
                            metadata={"source": "graph"},
                        )
                    )

        # 基于 chunk_id / text 去重
        seen = set()
        unique_results: List[RetrievedChunk] = []
        for c in results:
            key = c.chunk_id or c.text
            if key in seen:
                continue
            seen.add(key)
            unique_results.append(c)
        logger.info(
            "NL2SQLRAG.retrieve_chunks mode=%s top_k=%s schema_raw_top=%s caps(schema,biz,qa)=(%s,%s,%s) "
            "effective(schema,biz,qa)=(%s,%s,%s) graph_enabled=%s qa_slot_lookup=%s raw_total=%d unique=%d "
            "per_ns_vector=%s per_ns_graph=%s query_len=%d",
            decision.mode,
            top,
            schema_ns_top,
            schema_chunk_cap,
            biz_chunk_cap,
            qa_chunk_cap,
            ns_schema,
            ns_biz,
            ns_qa,
            self._graph_query is not None,
            qa_slot_lookup,
            len(results),
            len(unique_results),
            per_ns_vector,
            per_ns_graph,
            len(question or ""),
        )
        return unique_results

    @staticmethod
    def _render_chunk(chunk: RetrievedChunk) -> str:
        # NL2SQL prompt 中保留来源线索，提升可解释性与后续追踪能力
        prefix_parts: list[str] = []
        if chunk.namespace:
            prefix_parts.append(f"ns={chunk.namespace}")
        if chunk.doc_name:
            prefix_parts.append(f"doc={chunk.doc_name}")
        if chunk.section_path:
            prefix_parts.append(f"section={chunk.section_path}")
        prefix = f"[{' | '.join(prefix_parts)}] " if prefix_parts else ""
        return f"{prefix}{chunk.text}".strip()

