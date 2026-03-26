from __future__ import annotations

from typing import List

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
        针对 NL2SQL 查询，从多命名空间联合检索上下文片段。
        """
        top = top_k or 5
        results: List[str] = []

        for ns in (self.NS_SCHEMA, self.NS_BIZ, self.NS_QA):
            ctx = self._rag.retrieve_context(
                question,
                top_k=top,
                namespace=ns,
                scene="nl2sql",
            )
            results.extend(ctx)

        # 简单去重
        seen = set()
        unique_results: List[str] = []
        for t in results:
            if t not in seen:
                seen.add(t)
                unique_results.append(t)
        return unique_results

