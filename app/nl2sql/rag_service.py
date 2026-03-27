from __future__ import annotations

from typing import List

from app.core.config import get_app_config
from app.graph.query_service import GraphQueryService
from app.rag.models import RetrievedChunk
from app.rag.retrieval_policy import RetrievalPolicy
from app.rag.rag_service import RAGService


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

    def retrieve(self, question: str, top_k: int | None = None) -> List[str]:
        """
        兼容接口：针对 NL2SQL 查询，从多命名空间联合检索上下文片段（字符串）。
        新链路请优先使用 `retrieve_chunks` 获取标准结构。
        """
        chunks = self.retrieve_chunks(question, top_k=top_k)
        rendered = [self._render_chunk(c) for c in chunks]
        # 去重（保留顺序）
        seen = set()
        unique_results: List[str] = []
        for t in rendered:
            if t not in seen:
                seen.add(t)
                unique_results.append(t)
        return unique_results

    def retrieve_chunks(self, question: str, top_k: int | None = None) -> List[RetrievedChunk]:
        """
        标准检索接口：返回 RetrievedChunk（含 doc/namespace/section 等元信息）。
        """
        top = top_k or 5
        results: List[RetrievedChunk] = []
        decision = self._policy.decide(question)

        for ns in (self.NS_SCHEMA, self.NS_BIZ, self.NS_QA):
            # 向量侧标准结构优先保留（含 doc/section 元信息）。
            if decision.mode != "graph":
                chunks = self._rag.retrieve_chunks(
                    query=question,
                    top_k=top,
                    namespace=ns,
                    scene="nl2sql",
                )
                results.extend(chunks)
            # 图侧事实按统一策略层决策补充。
            if decision.mode != "vector" and self._graph_query is not None:
                graph_facts = self._graph_query.query_relevant_facts(
                    question=question,
                    namespace=ns,
                    max_hops=decision.graph_hops,
                    max_items=decision.max_graph_items,
                )
                for idx, fact in enumerate(graph_facts[:top]):
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

