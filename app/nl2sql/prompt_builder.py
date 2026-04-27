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
                "4) 除字符串字面量内部外，整条 SQL 应为紧凑单行（无换行、无仅用于美观的缩进）。",
                "5) 当问题同时涉及台账/设备/锅炉名称与业务明细（如超温记录）时，必须通过多表 JOIN 关联；"
                "名称类条件应使用 catalog 中的名称字段（如 boiler_name 等），禁止用 boiler_id='1' 等猜测数字对应「一号」「#1」等表述。",
                "6) Schema catalog 中若列出 FK: 本地列->引用表.引用列，应优先据此书写 ON 条件。",
                "7) 当问题含“近一周/最近7天”等时间口径时，必须使用动态时间窗（如 NOW() 与 DATE_SUB），禁止硬编码历史固定日期。",
                "8) 区域/部位匹配（如水冷壁前墙）优先使用 LIKE 模糊匹配，不要过度使用严格等值导致 0 行。",
            ]
        )
        return "\n".join(parts)

