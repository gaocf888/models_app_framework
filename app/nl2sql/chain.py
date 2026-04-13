from __future__ import annotations

import os
import re
from typing import Iterable

from app.core.logging import get_logger
from app.llm.client import VLLMHttpClient
from app.llm.prompt_registry import PromptTemplateRegistry
from app.llm.langsmith_tracker import LangSmithTracker
from app.nl2sql.prompt_builder import PromptBuilder
from app.nl2sql.rag_service import NL2SQLRAGService
from app.nl2sql.schema_service import SchemaMetadataService, TableSchema
from app.nl2sql.schema_snippet_parser import (
    TableRAGHints,
    format_enriched_catalog_line,
    parse_nl2sql_schema_snippets,
)
from app.nl2sql.validator import SQLValidator

logger = get_logger(__name__)

NL2SQL_SCHEMA_CATALOG_PLACEHOLDER = "{{NL2SQL_SCHEMA_CATALOG}}"


def _text_preview(text: str | None, max_len: int = 200) -> str:
    s = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    s = " ".join(s.split())
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


class NL2SQLChain:
    """
    NL2SQL 链路（支持 LangChain 的企业级骨架）。

    - 用 RAG 检索相关 Schema 片段；
    - 使用 PromptBuilder 与 PromptTemplateRegistry 构建提示词；
    - 优先通过 LangChain ChatOpenAI 调用 vLLM 生成 SQL；
    - 如未安装 LangChain，则回退到内部 VLLMHttpClient；
    - 用 SQLValidator 做基础安全校验，未通过时返回空字符串。
    """

    def __init__(
        self,
        schema_service: SchemaMetadataService | None = None,
        rag_service: NL2SQLRAGService | None = None,
        prompt_builder: PromptBuilder | None = None,
        llm_client: VLLMHttpClient | None = None,
        validator: SQLValidator | None = None,
        prompt_registry: PromptTemplateRegistry | None = None,
    ) -> None:
        self._schema = schema_service or SchemaMetadataService()
        self._rag = rag_service or NL2SQLRAGService()
        self._prompt_builder = prompt_builder or PromptBuilder()
        self._llm = llm_client or VLLMHttpClient()
        self._validator = validator or SQLValidator()
        self._prompts = prompt_registry or PromptTemplateRegistry()
        self._ls_tracker = LangSmithTracker()
        self._schema_refreshed = False

        # 可选的 LangChain LLM
        self._lc_chat_model = None
        try:
            from langchain_openai import ChatOpenAI  # type: ignore[import-not-found]
            from app.core.config import get_app_config

            cfg = get_app_config()
            llm_cfg = cfg.llm
            default_model = llm_cfg.default_model
            model_cfg = llm_cfg.models[default_model]

            self._lc_chat_model = ChatOpenAI(
                model=model_cfg.model_id,
                base_url=model_cfg.endpoint.rstrip("/"),
                api_key=model_cfg.api_key or "EMPTY",
                temperature=model_cfg.temperature,
            )
            logger.info("NL2SQLChain: LangChain ChatOpenAI enabled.")
        except Exception:
            logger.warning("NL2SQLChain: LangChain not available, fallback to VLLMHttpClient.")

    async def generate_sql(self, question: str, user_id: str | None = None) -> str:
        logger.info(
            "NL2SQLChain.generate_sql start user_id=%s question_len=%d preview=%r",
            user_id,
            len(question or ""),
            _text_preview(question, 160),
        )
        await self._ensure_schema_refreshed_once()
        schema_from_db = self._db_schema_available()
        table_names = [t.name for t in self._schema.list_tables() if t.name]
        logger.info(
            "NL2SQLChain schema_after_refresh table_count=%d schema_from_db=%s sample=%s",
            len(table_names),
            schema_from_db,
            sorted({n.lower() for n in table_names})[:8],
        )

        # Step 1: 规划（DB 反射成功时默认跳过，避免虚构表名污染 RAG 查询）
        plan_summary: str | None = None
        planner_skipped = True
        if self._lc_chat_model is not None:
            disable_plan = os.getenv("NL2SQL_DISABLE_PLANNER_WHEN_DB_SCHEMA", "true").lower() == "true"
            if not (disable_plan and schema_from_db):
                planner_skipped = False
                try:
                    plan_summary = await self._plan(question=question)
                except Exception:
                    logger.exception("NL2SQLChain: planning step failed, fallback to simple flow.")
                    plan_summary = None
        logger.info(
            "NL2SQLChain planner planner_skipped=%s plan_summary_len=%s",
            planner_skipped,
            len(plan_summary or ""),
        )

        # Step 2: 基于规划结果从 NL2SQL 专用 RAG 检索 Schema/业务知识/样例 Q&A 片段
        rag_query = question
        if plan_summary:
            rag_query = f"【NL2SQL 规划】{plan_summary}\n【用户问题】{question}"
        schema_snippets = self._rag.retrieve(rag_query)
        rag_hints = parse_nl2sql_schema_snippets(schema_snippets)
        allowed_tables, allowed_columns, schema_ok = self._whitelist_from_schema_and_snippets(schema_snippets)
        if schema_ok != schema_from_db:
            logger.warning(
                "NL2SQLChain: schema whitelist flag mismatch schema_from_db=%s schema_ok=%s",
                schema_from_db,
                schema_ok,
            )
        logger.info(
            "NL2SQLChain after RAG snippets=%d rag_hint_tables=%d whitelist_tables=%d whitelist_columns=%d schema_ok=%s",
            len(schema_snippets),
            len(rag_hints),
            len(allowed_tables),
            len(allowed_columns),
            schema_ok,
        )

        full_catalog = self._format_enriched_schema_catalog(self._schema.list_tables(), rag_hints)

        # NL2SQL 专用 Prompt 前缀（scene=nl2sql），支持 {{NL2SQL_SCHEMA_CATALOG}} 注入全库表结构
        prompt_default_version = os.getenv("NL2SQL_PROMPT_DEFAULT_VERSION", "v2")
        tpl = self._prompts.get_template(
            scene="nl2sql",
            user_id=user_id,
            version=None,
            default_version=prompt_default_version,
        )
        raw_prefix = (tpl.content if tpl else None) or ""
        catalog_in_template = NL2SQL_SCHEMA_CATALOG_PLACEHOLDER in raw_prefix
        replacement_len = 0
        if catalog_in_template:
            if schema_from_db:
                replacement = full_catalog
                catalog_source = "db_full_catalog"
            elif rag_hints:
                replacement = self._format_rag_hints_catalog(rag_hints)
                catalog_source = "rag_hints_only"
            else:
                replacement = (
                    "（当前未能从数据库加载完整表结构，且未从 RAG 解析到表结构片段；"
                    "请严格依据下方【Database schema】中的真实表名与字段名生成 SQL。）"
                )
                catalog_source = "placeholder_warning"
            replacement_len = len(replacement.strip())
            system_prefix = raw_prefix.replace(NL2SQL_SCHEMA_CATALOG_PLACEHOLDER, replacement.strip())
        else:
            system_prefix = raw_prefix or None
            catalog_source = "no_placeholder_in_template"

        prompt_catalog: str | None
        if catalog_in_template:
            prompt_catalog = None
        elif schema_from_db:
            prompt_catalog = full_catalog
        else:
            prompt_catalog = self._build_schema_catalog_hint(
                schema_snippets, allowed_tables=allowed_tables, rag_hints=rag_hints
            )

        prompt = self._prompt_builder.build(
            question,
            schema_snippets,
            system_prefix=system_prefix,
            schema_catalog=prompt_catalog,
        )
        logger.info(
            "NL2SQLChain prompt built version=%s catalog_in_template=%s catalog_source=%s "
            "replacement_chars=%d prompt_catalog_chars=%s prompt_total_chars=%d",
            prompt_default_version,
            catalog_in_template,
            catalog_source,
            replacement_len,
            len(prompt_catalog or "") if prompt_catalog is not None else None,
            len(prompt),
        )

        if self._lc_chat_model is not None:
            sql = await self._generate_via_langchain(prompt)
        else:
            sql = await self._llm.generate(model=None, prompt=prompt)  # type: ignore[arg-type]
        raw_out_len = len(sql or "")
        sql = self._validator.normalize_sql(sql)
        logger.info(
            "NL2SQLChain LLM sql raw_len=%d normalized_len=%d preview=%r llm_backend=%s",
            raw_out_len,
            len(sql or ""),
            _text_preview(sql, 240),
            "langchain" if self._lc_chat_model is not None else "vllm_http",
        )

        valid, validation_error = self._validate_sql(
            sql,
            allowed_tables=allowed_tables,
            allowed_columns=allowed_columns,
            enforce_column_whitelist=schema_ok,
        )
        if not valid:
            logger.warning(
                "NL2SQLChain validation failed preview_question=%r sql_preview=%r reason=%s",
                _text_preview(question, 80),
                _text_preview(sql, 200),
                validation_error,
            )
            # 可选 Step 4: 在 LangChain 可用时尝试自检与修正
            if self._lc_chat_model is not None:
                try:
                    logger.info("NL2SQLChain refine_sql start reason=%s", validation_error)
                    sql = await self._refine_sql(
                        question=question,
                        original_sql=sql,
                        validation_error=validation_error,
                    )
                    sql = self._validator.normalize_sql(sql)
                    valid, validation_error = self._validate_sql(
                        sql,
                        allowed_tables=allowed_tables,
                        allowed_columns=allowed_columns,
                        enforce_column_whitelist=schema_ok,
                    )
                    if not valid:
                        logger.warning(
                            "NL2SQLChain refine_sql still invalid sql_preview=%r reason=%s",
                            _text_preview(sql, 200),
                            validation_error,
                        )
                        return ""
                    logger.info(
                        "NL2SQLChain refine_sql ok sql_len=%d preview=%r",
                        len(sql or ""),
                        _text_preview(sql, 200),
                    )
                except Exception:
                    logger.exception("NL2SQLChain: refine_sql failed, return empty SQL.")
                    return ""
            else:
                logger.warning("NL2SQLChain validation failed and no LangChain; return empty SQL")
                return ""

        # LangSmith trace（若启用）
        self._ls_tracker.log_run(
            name="nl2sql",
            run_type="llm",
            inputs={
                "user_id": user_id,
                "question": question,
            },
            outputs={"sql": sql},
            metadata={"scene": "nl2sql"},
        )

        logger.info(
            "NL2SQLChain.generate_sql success sql_len=%d preview=%r",
            len(sql or ""),
            _text_preview(sql, 200),
        )
        return sql

    async def _plan(self, question: str) -> str:
        """
        NL2SQL 问题理解与规划步骤。

        当前版本：
        - 使用 LangChain LLM 输出简要文本，概括可能涉及的业务实体/表、关键字段与复杂度（是否需要多表 join/聚合等）。
        """
        from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore[import-not-found]

        system = (
            "你是一个 NL2SQL 规划助手。请用简短中文总结："
            "1) 可能涉及的业务实体；表名仅作检索提示，后续会提供真实库表清单，请勿编造英文表名；"
            "2) 需要关注的关键字段（时间/状态/主键等）；"
            "3) 是否需要多表 join 或聚合。"
        )
        messages: list[object] = [
            SystemMessage(content=system),
            HumanMessage(content=f"用户的查询需求是：{question}"),
        ]
        resp = await self._lc_chat_model.ainvoke(messages)  # type: ignore[union-attr]
        summary = resp.content if hasattr(resp, "content") else str(resp)
        logger.info("NL2SQLChain planner summary: %s", summary)
        return summary

    async def _refine_sql(self, question: str, original_sql: str, validation_error: str | None = None) -> str:
        """
        当初始 SQL 未通过 SQLValidator 校验时的自检与修正步骤。

        当前版本：
        - 将原始 SQL 与问题一起交给 LLM，请其生成“更安全、仅含 SELECT 的 SQL”；
        - 不强依赖特定错误信息，仅作为结构性骨架。
        """
        from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore[import-not-found]

        system = (
            "你是一个 NL2SQL SQL 修正助手。"
            "给定用户问题与一条可能存在安全风险或不符合只读要求的 SQL，"
            "请输出一条仅包含安全 SELECT 查询的 SQL，不要包含 DROP/DELETE/UPDATE/INSERT 等写操作。"
            " 输出为单行可执行 SQL：除字符串字面量内部外不要换行或多余缩进。"
            " 若问题涉及锅炉/设备名称与明细记录等多实体，应通过 JOIN 关联台账表与事实表，禁止用 boiler_id='1' 等臆造数字代替「一号锅炉」类名称条件。"
        )
        messages: list[object] = [
            SystemMessage(content=system),
            HumanMessage(
                content=(
                    f"用户问题: {question}\n"
                    f"初稿 SQL: {original_sql}\n"
                    f"校验失败原因: {validation_error or 'unknown'}\n"
                    "请在保证语义合理的前提下，输出一条安全的仅 SELECT 语句（单行，无 markdown）。"
                )
            ),
        ]
        resp = await self._lc_chat_model.ainvoke(messages)  # type: ignore[union-attr]
        content = resp.content if hasattr(resp, "content") else str(resp)
        out = content.strip()
        logger.debug("NL2SQLChain._refine_sql output_len=%d preview=%r", len(out), _text_preview(out, 160))
        return out

    async def _ensure_schema_refreshed_once(self) -> None:
        if self._schema_refreshed:
            return
        try:
            await self._schema.refresh_from_db()
        except Exception:
            logger.warning(
                "NL2SQLChain: refresh schema from DB failed, fallback to snippet-based whitelist.",
                exc_info=True,
            )
        self._schema_refreshed = True

    def _db_schema_available(self) -> bool:
        names = {t.name.lower() for t in self._schema.list_tables() if t.name}
        return bool(names) and names != {"orders"}

    def _whitelist_from_schema_and_snippets(
        self, schema_snippets: Iterable[str]
    ) -> tuple[set[str], set[str], bool]:
        """
        优先从真实 DB Schema 构建白名单；失败时回退到 RAG 片段抽取。
        第三项 True 表示可对限定列名做强校验。
        """
        db_tables: set[str] = set()
        db_columns: set[str] = set()
        for t in self._schema.list_tables():
            if t.name:
                db_tables.add(t.name.lower())
            for c in t.columns:
                if c.name:
                    db_columns.add(c.name.lower())

        if db_tables and db_tables != {"orders"}:
            return db_tables, db_columns, True

        st, sc = self._validator.extract_identifiers_from_snippets(schema_snippets)
        return st, sc, False

    def _format_enriched_schema_catalog(
        self, tables: list[TableSchema], rag_hints: dict[str, TableRAGHints]
    ) -> str:
        max_tables = max(1, int(os.getenv("NL2SQL_SCHEMA_CATALOG_MAX_TABLES", "400")))
        max_cols = max(1, int(os.getenv("NL2SQL_SCHEMA_CATALOG_MAX_COLS", "48")))
        sorted_tables = sorted((t for t in tables if t.name), key=lambda x: x.name.lower())
        lines: list[str] = []
        for t in sorted_tables[:max_tables]:
            cols = [c.name for c in t.columns if c.name][:max_cols]
            h = rag_hints.get(t.name.lower()) if t.name else None
            lines.append(
                format_enriched_catalog_line(
                    t.name, cols, h, max_cols=max_cols, foreign_keys=t.foreign_keys or None
                )
            )
        if len(sorted_tables) > max_tables:
            lines.append(
                f"... 其余 {len(sorted_tables) - max_tables} 张表已省略（可调 NL2SQL_SCHEMA_CATALOG_MAX_TABLES）"
            )
        return "\n".join(lines)

    def _format_rag_hints_catalog(self, rag_hints: dict[str, TableRAGHints]) -> str:
        """无 DB 反射时，仅用 RAG 解析结果填充占位符（表名以文档为准，执行前需库一致）。"""
        max_tables = max(1, int(os.getenv("NL2SQL_SCHEMA_CATALOG_MAX_TABLES", "400")))
        max_cols = max(1, int(os.getenv("NL2SQL_SCHEMA_CATALOG_MAX_COLS", "48")))
        lines: list[str] = []
        for name in sorted(rag_hints.keys())[:max_tables]:
            h = rag_hints[name]
            cols = sorted(h.column_comments.keys())[:max_cols]
            lines.append(format_enriched_catalog_line(name, cols, h, max_cols=max_cols))
        if len(rag_hints) > max_tables:
            lines.append(f"... 其余 {len(rag_hints) - max_tables} 张表已省略")
        return "\n".join(lines)

    def _validate_sql(
        self,
        sql: str,
        *,
        allowed_tables: set[str],
        allowed_columns: set[str],
        enforce_column_whitelist: bool,
    ) -> tuple[bool, str | None]:
        if not self._validator.validate(sql):
            return False, "sql safety validation failed"
        cols = allowed_columns if enforce_column_whitelist else None
        ok, reason = self._validator.validate_identifiers(
            sql,
            allowed_tables=allowed_tables or None,
            allowed_columns=cols,
        )
        return ok, reason

    def _build_schema_catalog_hint(
        self,
        schema_snippets: Iterable[str],
        *,
        allowed_tables: set[str],
        rag_hints: dict[str, TableRAGHints],
    ) -> str:
        """
        构建结构化 schema catalog，显式告诉模型可用表和字段。
        """
        tables = self._schema.list_tables()
        if not tables:
            return ""

        snippet_text = "\n".join(schema_snippets).lower()
        candidate_names: set[str] = set()
        for m in re.finditer(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", snippet_text):
            candidate_names.add(m.group(0).lower())

        selected = [t for t in tables if t.name and t.name.lower() in allowed_tables and t.name.lower() in candidate_names]
        if not selected:
            selected = [t for t in tables if t.name and t.name.lower() in allowed_tables]
        if not selected:
            selected = tables

        lines: list[str] = []
        for t in selected[:12]:
            cols = [c.name for c in t.columns if c.name][:16]
            if not cols:
                continue
            h = rag_hints.get(t.name.lower()) if t.name else None
            lines.append(
                format_enriched_catalog_line(
                    t.name, cols, h, max_cols=16, foreign_keys=t.foreign_keys or None
                )
            )
        return "\n".join(lines)

    async def _generate_via_langchain(self, prompt: str) -> str:
        from langchain_core.messages import HumanMessage  # type: ignore[import-not-found]

        resp = await self._lc_chat_model.ainvoke([HumanMessage(content=prompt)])  # type: ignore[union-attr]
        content = resp.content if hasattr(resp, "content") else str(resp)
        return content.strip()

