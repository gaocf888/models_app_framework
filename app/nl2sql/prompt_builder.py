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
        " 只能使用输入中出现过的真实表名和字段名，不允许臆造表名/字段名。"
    )

    def build(
        self,
        question: str,
        schema_snippets: List[str],
        system_prefix: Optional[str] = None,
        schema_catalog: Optional[str] = None,
    ) -> str:
        """
        根据自然语言问题和 Schema 片段构建提示词。

        - system_prefix：来自 PromptTemplateRegistry 的 nl2sql 场景前缀，可选；
        - 若未提供 system_prefix，则使用内置默认前缀。
        """
        prefix = (system_prefix or self.DEFAULT_SYSTEM_PREFIX).strip()
        schema_block = "\n".join(f"- {s}" for s in schema_snippets)
        parts: list[str] = [
            prefix,
            "\n【Database schema】",
            schema_block,
        ]
        if schema_catalog is not None:
            parts.extend(
                [
                    "\n【Schema catalog (authoritative identifiers)】",
                    schema_catalog.strip() or "(not available)",
                ]
            )
        parts.extend(
            [
                "\n【User question】",
                question,
                "\n【Output rules】",
                "1) 只输出一条可执行 SQL，禁止输出 markdown 代码块（不要包含 ```）。",
                "2) 表名/字段名以 Schema catalog（若有）为准；否则严格依据 Database schema 片段，禁止臆造。",
                "3) 查询语句必须是只读 SELECT（可包含 WITH），禁止任何写操作。",
            ]
        )
        return "\n".join(parts)

