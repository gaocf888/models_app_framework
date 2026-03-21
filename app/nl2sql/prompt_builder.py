from __future__ import annotations

from typing import List, Optional


class PromptBuilder:
    """
    NL2SQL Prompt 构建器（简化骨架）。

    按《NL2SQL系统概要设计》将 Schema/RAG 结果等拼装为大模型提示词。
    """

    DEFAULT_SYSTEM_PREFIX = (
        "你是一个资深 SQL 助手，需要根据给定的数据库 Schema 信息和业务背景，为用户生成只读查询 SQL。"
        " 严格只生成 SELECT 语句，不要生成 UPDATE/DELETE/INSERT/DDL。"
    )

    def build(self, question: str, schema_snippets: List[str], system_prefix: Optional[str] = None) -> str:
        """
        根据自然语言问题和 Schema 片段构建提示词。

        - system_prefix：来自 PromptTemplateRegistry 的 nl2sql 场景前缀，可选；
        - 若未提供 system_prefix，则使用内置默认前缀。
        """
        prefix = (system_prefix or self.DEFAULT_SYSTEM_PREFIX).strip()
        schema_block = "\n".join(f"- {s}" for s in schema_snippets)
        parts = [
            prefix,
            "\n【Database schema】",
            schema_block,
            "\n【User question】",
            question,
            "\n请只输出一条 SQL 语句，不要添加多余解释。",
        ]
        return "\n".join(parts)

