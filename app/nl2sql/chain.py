from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable

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
from app.nl2sql.entity_rules import EntityRule, check_entity_rules, load_entity_rules_from_env
from app.nl2sql.sql_cache import strip_plan_context_guide_suffix
from app.nl2sql.validator import SQLValidator

logger = get_logger(__name__)


@dataclass(frozen=True)
class NL2SQLValidationContext:
    """供服务层在执行失败 / EXPLAIN 失败时做二次 refine 与再校验。"""

    allowed_tables: frozenset[str]
    allowed_columns: frozenset[str]
    schema_ok: bool
    table_columns: dict[str, frozenset[str]]
    join_whitelist: frozenset[str]
    parsed_intent: dict[str, Any] | None = None
    analysis_type: str | None = None

NL2SQL_SCHEMA_CATALOG_PLACEHOLDER = "{{NL2SQL_SCHEMA_CATALOG}}"


def _text_preview(text: str | None, max_len: int = 200) -> str:
    s = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    s = " ".join(s.split())
    if max_len <= 0:
        return s
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
    _tidb_forbidden_aliases_default = {
        "load",
        "row_number",
        "rank",
        "dense_rank",
        "lead",
        "lag",
        "window",
        "select",
        "from",
        "where",
        "group",
        "order",
        "limit",
        "join",
        "key",
        "index",
        "table",
        "column",
        "primary",
        "default",
        "desc",
        "interval",
        "current_date",
        "current_time",
        "current_timestamp",
    }
    _tidb_postgres_interval_pattern = re.compile(
        r"\binterval\s*'(\d+)\s*(day|days|hour|hours|minute|minutes|month|months|year|years)'",
        re.IGNORECASE,
    )
    _tidb_window_pattern = re.compile(r"\bover\s*\(", re.IGNORECASE)
    _tidb_lag_like_pattern = re.compile(
        r"\b(lag|lead|row_number|rank|dense_rank)\s*\(",
        re.IGNORECASE,
    )
    # DATE_SUB / DATE_ADD 等参数里可含嵌套括号（如 NOW()），用 [^)]* 会截断在第一个 )，把 “, INTERVAL 7 DAY)” 留在 SQL 外导致语法错误。
    _tidb_date_call_arg = r"(?:[^()]|\([^()]*\))*"
    _tidb_date_call_rhs = rf"DATE_[A-Z_]+\({_tidb_date_call_arg}\)"
    # 动态时间窗仅替换 QA/模板中的日期字面量，避免误改 SQL 内已有的 DATE_SUB/CURDATE() 表达式。
    _TIME_LITERAL_RHS = r"'[^']+'"
    _DAY_WINDOW_TAGS = frozenset(
        {"today", "yesterday", "day_before_yesterday", "three_days_ago"}
    )
    _PLAN_OVERRIDE_WINDOW_TAGS = frozenset(
        {
            "recent_1_year",
            "recent_7_days",
            "recent_6_months",
        }
    )
    _HALF_OPEN_WINDOW_TAGS = _DAY_WINDOW_TAGS | frozenset(
        {
            "this_week",
            "last_week",
            "this_month",
            "last_month",
            "this_year",
            "last_year",
            "year_before_last",
            "this_quarter",
            "last_quarter",
            "half_first",
            "half_second",
        }
    )

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
        self._tidb_forbidden_aliases = self._load_tidb_forbidden_aliases_from_env()

        # 可选的 LangChain LLM
        self._lc_chat_model = None
        try:
            from langchain_openai import ChatOpenAI  # type: ignore[import-not-found]
            from app.core.config import get_app_config

            cfg = get_app_config()
            llm_cfg = cfg.llm
            default_model = llm_cfg.default_model
            model_cfg = llm_cfg.models[default_model]

            # NL2SQL 单独使用低随机性参数，避免同题多次生成 SQL 漂移（与客服等场景的 LLM 温度解耦）
            nl2sql_temp = float(os.getenv("NL2SQL_CHAT_TEMPERATURE", "0"))
            nl2sql_top_p = float(os.getenv("NL2SQL_CHAT_TOP_P", "0.95"))
            nl2sql_seed_raw = os.getenv("NL2SQL_CHAT_SEED", "").strip()
            model_kw: dict = {"top_p": nl2sql_top_p}
            if nl2sql_seed_raw:
                try:
                    model_kw["seed"] = int(nl2sql_seed_raw)
                except ValueError:
                    logger.warning("NL2SQL_CHAT_SEED ignored (not an int): %r", nl2sql_seed_raw)
            self._lc_chat_model = ChatOpenAI(
                model=model_cfg.model_id,
                base_url=model_cfg.endpoint.rstrip("/"),
                api_key=model_cfg.api_key or "EMPTY",
                temperature=nl2sql_temp,
                model_kwargs=model_kw,
            )
            logger.info(
                "NL2SQLChain: LangChain ChatOpenAI enabled (nl2sql temperature=%s top_p=%s).",
                nl2sql_temp,
                nl2sql_top_p,
            )
        except Exception:
            logger.warning("NL2SQLChain: LangChain not available, fallback to VLLMHttpClient.")

    async def generate_sql(
        self,
        question: str,
        user_id: str | None = None,
        analysis_type: str | None = None,
    ) -> str:
        sql, _ctx = await self.generate_sql_with_validation_context(
            question,
            user_id=user_id,
            analysis_type=analysis_type,
            plan_item_id=None,
        )
        return sql

    async def generate_sql_with_validation_context(
        self,
        question: str,
        user_id: str | None = None,
        analysis_type: str | None = None,
        plan_item_id: str | None = None,
        plan_template_version: str | None = None,
        time_intent_text: str | None = None,
        confirmed_scope: dict | None = None,
        scope_intent_text: str | None = None,
        original_query: str | None = None,
    ) -> tuple[str, NL2SQLValidationContext]:
        if confirmed_scope:
            time_src = (
                (scope_intent_text or time_intent_text or question) or ""
            ).strip()
        else:
            time_src = (
                time_intent_text.strip()
                if time_intent_text is not None
                else question
            )
        from app.nl2sql.question_intent import resolve_question_intent, scope_literals_from_intent
        from app.nl2sql.question_intent_display import (
            format_parsed_intent_prompt_block,
            inject_parsed_intent_enabled,
            question_intent_to_dict,
        )

        question_intent = resolve_question_intent(
            question,
            time_intent_source=time_src,
            confirmed_scope=confirmed_scope,
            scope_intent_text=scope_intent_text,
            original_query=original_query,
        )
        scope_literals = scope_literals_from_intent(question_intent)
        parsed_intent_dict = question_intent_to_dict(question_intent)
        logger.info(
            "NL2SQLChain parsed_intent parse_mode=%s boiler=%s device=%s time_tag=%s",
            question_intent.parse_mode,
            question_intent.scope.boiler,
            question_intent.scope.device_name,
            question_intent.time_window_tag,
        )
        cache_key_for_store: str | None = None
        sql_cache_backend: Any = None
        l1_cache_key_for_store: str | None = None
        l1_cache_backend: Any = None
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
        from app.core.config import get_app_config
        from app.nl2sql.qa_feedback import NL2SQLQARetrievalContext
        from app.nl2sql.sql_cache import (
            compute_nl2sql_data_source_fp,
            compute_nl2sql_policy_fp,
            compute_schema_fp_from_metadata,
        )

        app_cfg = get_app_config()
        cfg_analysis = app_cfg.analysis
        db_cfg = getattr(app_cfg, "db", None)
        data_source_fp = ""
        schema_fp = ""
        policy_fp = ""
        nl2sql_qa_ctx: NL2SQLQARetrievalContext | None = None
        if db_cfg is not None:
            data_source_fp = compute_nl2sql_data_source_fp(
                host=db_cfg.host,
                port=db_cfg.port,
                database=db_cfg.database,
            )
            schema_fp = compute_schema_fp_from_metadata(table_names)
            policy_fp = compute_nl2sql_policy_fp(analysis_type=analysis_type)
            if os.getenv("NL2SQL_QA_FILTER_ENABLED", "true").lower() == "true":
                nl2sql_qa_ctx = NL2SQLQARetrievalContext(
                    data_source_fp=data_source_fp,
                    schema_fp=schema_fp,
                    policy_fp=policy_fp,
                    analysis_type=analysis_type,
                    plan_item_id=plan_item_id,
                    plan_template_version=plan_template_version,
                )

        rag_query = question
        if plan_summary:
            rag_query = f"【NL2SQL 规划】{plan_summary}\n【用户问题】{question}"
        schema_snippets = self._rag.retrieve(rag_query, nl2sql_qa_context=nl2sql_qa_ctx)
        rag_hints = parse_nl2sql_schema_snippets(schema_snippets)
        allowed_tables, allowed_columns, schema_ok = self._whitelist_from_schema_and_snippets(schema_snippets)
        table_columns_map = self._table_columns_map() if schema_ok else {}
        scoped_tables = self._resolve_table_scope(analysis_type=analysis_type, table_columns=table_columns_map)
        if scoped_tables:
            allowed_tables &= scoped_tables
            table_columns_map = {k: v for k, v in table_columns_map.items() if k in scoped_tables}
            if allowed_columns:
                scope_cols = {c for cols in table_columns_map.values() for c in cols}
                if scope_cols:
                    allowed_columns &= scope_cols
        join_whitelist = self._build_join_whitelist(table_columns_map, analysis_type=analysis_type)
        validation_ctx = NL2SQLValidationContext(
            frozenset(allowed_tables),
            frozenset(allowed_columns),
            schema_ok,
            {k: frozenset(v) for k, v in table_columns_map.items()},
            frozenset(join_whitelist),
            parsed_intent=parsed_intent_dict,
            analysis_type=analysis_type,
        )
        entity_rules = load_entity_rules_from_env()
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

        from app.nl2sql.sql_cache import (
            build_nl2sql_sql_cache_key,
            get_nl2sql_l1_cache,
            get_nl2sql_sql_cache,
        )
        from app.nl2sql.sql_skeleton import (
            build_nl2sql_l1_cache_key,
            render_sql_time_skeleton,
            skeleton_payload_from_json,
        )

        fresh_sql_generation = False

        from app.nl2sql.qa_feedback import (
            nl2sql_qa_slot_lookup_eligible,
            nl2sql_qa_slot_strict_replay_enabled,
        )

        if (
            nl2sql_qa_ctx is not None
            and nl2sql_qa_slot_strict_replay_enabled(analysis_type)
            and nl2sql_qa_slot_lookup_eligible(nl2sql_qa_ctx)
        ):
            replay_sql = await self._try_qa_slot_strict_replay(
                nl2sql_qa_ctx=nl2sql_qa_ctx,
                question=question,
                time_intent_source=time_src,
                validation_ctx=validation_ctx,
                entity_rules=entity_rules,
                user_id=user_id,
                plan_item_id=plan_item_id,
            )
            if replay_sql:
                logger.info(
                    "NL2SQLChain.generate_sql success sql_len=%d preview=%r qa_replay=strict",
                    len(replay_sql or ""),
                    _text_preview(replay_sql, 0),
                )
                return replay_sql, validation_ctx

        if cfg_analysis.nl2sql_cache_enabled and db_cfg is not None:
            cache_key_for_store = build_nl2sql_sql_cache_key(
                data_source_fp=data_source_fp,
                analysis_type=analysis_type,
                plan_item_id=plan_item_id,
                question=question,
                schema_fp=schema_fp,
                policy_fp=policy_fp,
            )
            sql_cache_backend = get_nl2sql_sql_cache(
                ttl_seconds=cfg_analysis.nl2sql_cache_ttl_seconds,
                max_entries=cfg_analysis.nl2sql_cache_max_entries,
            )
            if cfg_analysis.nl2sql_l1_cache_enabled:
                l1_cache_key_for_store = build_nl2sql_l1_cache_key(
                    data_source_fp=data_source_fp,
                    analysis_type=analysis_type,
                    plan_item_id=plan_item_id,
                    question=question,
                    schema_fp=schema_fp,
                    policy_fp=policy_fp,
                )
                l1_cache_backend = get_nl2sql_l1_cache(
                    ttl_seconds=cfg_analysis.nl2sql_cache_ttl_seconds,
                    max_entries=cfg_analysis.nl2sql_cache_max_entries,
                )
            cached_sql = sql_cache_backend.get(cache_key_for_store)
            if cached_sql:
                sql, valid, fail_reason = self._postprocess_and_validate_candidate_sql(
                    cached_sql,
                    question=question,
                    time_intent_source=time_src,
                    validation_ctx=validation_ctx,
                    entity_rules=entity_rules,
                    log_label="sql_cache",
                    plan_item_id=plan_item_id,
                )
                if valid:
                    logger.info(
                        "NL2SQLChain sql_cache hit sql_len=%d data_source_fp=%s plan_item_id=%s",
                        len(sql or ""),
                        data_source_fp,
                        plan_item_id or "-",
                    )
                    self._ls_tracker.log_run(
                        name="nl2sql",
                        run_type="llm",
                        inputs={
                            "user_id": user_id,
                            "question": question,
                        },
                        outputs={"sql": sql},
                        metadata={"scene": "nl2sql", "sql_cache": "hit"},
                    )
                    logger.info(
                        "NL2SQLChain.generate_sql success sql_len=%d preview=%r",
                        len(sql or ""),
                        _text_preview(sql, 0),
                    )
                    return sql, validation_ctx
                logger.warning(
                    "NL2SQLChain sql_cache stale_or_invalid evict preview=%r reason=%s",
                    _text_preview(sql, 0),
                    fail_reason or "-",
                )
                sql_cache_backend.delete(cache_key_for_store)

            if (
                cfg_analysis.nl2sql_l1_cache_enabled
                and l1_cache_backend is not None
                and l1_cache_key_for_store
            ):
                raw_l1 = l1_cache_backend.get(l1_cache_key_for_store)
                if raw_l1:
                    payload = skeleton_payload_from_json(raw_l1)
                    if not isinstance(payload, dict):
                        l1_cache_backend.delete(l1_cache_key_for_store)
                    else:
                        rendered = render_sql_time_skeleton(payload, time_src)
                        if rendered:
                            sql, valid, fail_reason = self._postprocess_and_validate_candidate_sql(
                                rendered,
                                question=question,
                                time_intent_source=time_src,
                                validation_ctx=validation_ctx,
                                entity_rules=entity_rules,
                                log_label="sql_l1_cache",
                                plan_item_id=plan_item_id,
                            )
                            if valid:
                                logger.info(
                                    "NL2SQLChain sql_l1_cache hit sql_len=%d data_source_fp=%s plan_item_id=%s",
                                    len(sql or ""),
                                    data_source_fp,
                                    plan_item_id or "-",
                                )
                                self._ls_tracker.log_run(
                                    name="nl2sql",
                                    run_type="llm",
                                    inputs={
                                        "user_id": user_id,
                                        "question": question,
                                    },
                                    outputs={"sql": sql},
                                    metadata={"scene": "nl2sql", "sql_cache": "l1_hit"},
                                )
                                logger.info(
                                    "NL2SQLChain.generate_sql success sql_len=%d preview=%r",
                                    len(sql or ""),
                                    _text_preview(sql, 0),
                                )
                                return sql, validation_ctx
                            logger.warning(
                                "NL2SQLChain sql_l1_cache stale_or_invalid evict preview=%r reason=%s",
                                _text_preview(sql, 0),
                                fail_reason or "-",
                            )
                            l1_cache_backend.delete(l1_cache_key_for_store)

        catalog_tables = self._schema.list_tables()
        if scoped_tables:
            catalog_tables = [t for t in catalog_tables if t.name and t.name.lower() in scoped_tables]
        full_catalog = self._format_enriched_schema_catalog(catalog_tables, rag_hints)

        # NL2SQL Prompt 前缀：analysis_agent 与独立 NL2SQL 共用 scene=nl2sql（传真实 analysis_type）
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
        if inject_parsed_intent_enabled():
            prompt = f"{prompt}\n\n{format_parsed_intent_prompt_block(question_intent)}"
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

        fresh_sql_generation = True
        if self._lc_chat_model is not None:
            sql = await self._generate_via_langchain(prompt)
        else:
            vllm_kw: dict = {
                "temperature": float(os.getenv("NL2SQL_CHAT_TEMPERATURE", "0")),
                "top_p": float(os.getenv("NL2SQL_CHAT_TOP_P", "0.95")),
            }
            seed_raw = os.getenv("NL2SQL_CHAT_SEED", "").strip()
            if seed_raw:
                try:
                    vllm_kw["seed"] = int(seed_raw)
                except ValueError:
                    pass
            sql = await self._llm.generate(model=None, prompt=prompt, **vllm_kw)  # type: ignore[arg-type]
        raw_out_len = len(sql or "")
        sql = self._validator.normalize_sql(sql)
        sql, rewrite_notes = self._rewrite_tidb_compatible_sql(sql)
        rewrite_meta: dict[str, Any] = {}
        sql, filter_notes = self._rewrite_query_filters(
            sql,
            question=question,
            time_intent_source=time_src,
            scope_literals=scope_literals,
            parsed_intent=parsed_intent_dict,
            plan_item_id=plan_item_id,
            analysis_type=analysis_type,
            rewrite_meta=rewrite_meta,
        )
        self._apply_rewrite_meta_to_parsed_intent(parsed_intent_dict, rewrite_meta)
        rewrite_notes.extend(filter_notes)
        if rewrite_notes:
            logger.info("NL2SQLChain TiDB rewrite applied: %s", "; ".join(rewrite_notes))
        logger.info(
            "NL2SQLChain LLM sql raw_len=%d normalized_len=%d preview=%r llm_backend=%s",
            raw_out_len,
            len(sql or ""),
            _text_preview(sql, 0),
            "langchain" if self._lc_chat_model is not None else "vllm_http",
        )
        dialect_ok, dialect_reason = self._validate_tidb_dialect(sql)
        if not dialect_ok:
            logger.warning(
                "NL2SQLChain TiDB dialect check failed preview_question=%r sql_preview=%r reason=%s",
                _text_preview(question, 80),
                _text_preview(sql, 0),
                dialect_reason,
            )
            if self._lc_chat_model is not None:
                try:
                    logger.info("NL2SQLChain TiDB dialect refine start reason=%s", dialect_reason)
                    sql = await self._refine_sql(
                        question=question,
                        original_sql=sql,
                        validation_error=dialect_reason,
                    )
                    sql = self._validator.normalize_sql(sql)
                    sql, refine_notes = self._rewrite_tidb_compatible_sql(sql)
                    rewrite_meta = {}
                    sql, filter_notes = self._rewrite_query_filters(
                        sql,
                        question=question,
                        time_intent_source=time_src,
                        scope_literals=scope_literals,
                        parsed_intent=parsed_intent_dict,
                        plan_item_id=plan_item_id,
                        analysis_type=analysis_type,
                        rewrite_meta=rewrite_meta,
                    )
                    self._apply_rewrite_meta_to_parsed_intent(parsed_intent_dict, rewrite_meta)
                    refine_notes.extend(filter_notes)
                    if refine_notes:
                        logger.info(
                            "NL2SQLChain TiDB rewrite applied after refine: %s",
                            "; ".join(refine_notes),
                        )
                    dialect_ok, dialect_reason = self._validate_tidb_dialect(sql)
                    if not dialect_ok:
                        logger.warning(
                            "NL2SQLChain TiDB dialect refine still invalid sql_preview=%r reason=%s",
                            _text_preview(sql, 0),
                            dialect_reason,
                        )
                        return "", validation_ctx
                except Exception:
                    logger.exception("NL2SQLChain: TiDB dialect refine failed, return empty SQL.")
                    return "", validation_ctx
            else:
                logger.warning("NL2SQLChain TiDB dialect failed and no LangChain; return empty SQL")
                return "", validation_ctx

        ph_ok, ph_reason = self._validate_unresolved_time_placeholders(sql)
        if not ph_ok:
            logger.warning(
                "NL2SQLChain unresolved time placeholders preview_question=%r reason=%s",
                _text_preview(question, 80),
                ph_reason,
            )
            validation_error = ph_reason
            if self._lc_chat_model is not None:
                try:
                    sql = await self._refine_sql(
                        question=question,
                        original_sql=sql,
                        validation_error=validation_error or "",
                    )
                    sql = self._validator.normalize_sql(sql)
                    sql, refine_notes = self._rewrite_tidb_compatible_sql(sql)
                    rewrite_meta = {}
                    sql, filter_notes = self._rewrite_query_filters(
                        sql,
                        question=question,
                        time_intent_source=time_src,
                        scope_literals=scope_literals,
                        parsed_intent=parsed_intent_dict,
                        plan_item_id=plan_item_id,
                        analysis_type=analysis_type,
                        rewrite_meta=rewrite_meta,
                    )
                    self._apply_rewrite_meta_to_parsed_intent(parsed_intent_dict, rewrite_meta)
                    refine_notes.extend(filter_notes)
                    ph_ok, ph_reason = self._validate_unresolved_time_placeholders(sql)
                    if not ph_ok:
                        return "", validation_ctx
                except Exception:
                    logger.exception("NL2SQLChain: time placeholder refine failed, return empty SQL.")
                    return "", validation_ctx
            else:
                return "", validation_ctx

        valid, validation_error = self._validate_sql(
            sql,
            question=question,
            allowed_tables=allowed_tables,
            allowed_columns=allowed_columns,
            enforce_column_whitelist=schema_ok,
            table_columns=table_columns_map if schema_ok else None,
            join_whitelist=join_whitelist,
            entity_rules=entity_rules,
        )
        if not valid:
            logger.warning(
                "NL2SQLChain validation failed preview_question=%r sql_preview=%r reason=%s",
                _text_preview(question, 80),
                _text_preview(sql, 0),
                validation_error,
            )
            # 可选 Step 4: LangChain 可用时做一次校验修正（单次 LLM：合并原「首轮+严格轮」提示，降低双次 refine 带来的首包延迟）
            if self._lc_chat_model is not None:
                try:
                    logger.info(
                        "NL2SQLChain refine_sql start (single merged strict) reason=%s",
                        validation_error,
                    )
                    sql = await self._refine_sql(
                        question=question,
                        original_sql=sql,
                        validation_error=validation_error,
                        strict_schema_reminder=True,
                    )
                    sql = self._validator.normalize_sql(sql)
                    sql, refine_notes = self._rewrite_tidb_compatible_sql(sql)
                    rewrite_meta = {}
                    sql, filter_notes = self._rewrite_query_filters(
                        sql,
                        question=question,
                        time_intent_source=time_src,
                        scope_literals=scope_literals,
                        parsed_intent=parsed_intent_dict,
                        plan_item_id=plan_item_id,
                        analysis_type=analysis_type,
                        rewrite_meta=rewrite_meta,
                    )
                    self._apply_rewrite_meta_to_parsed_intent(parsed_intent_dict, rewrite_meta)
                    refine_notes.extend(filter_notes)
                    if refine_notes:
                        logger.info(
                            "NL2SQLChain TiDB rewrite applied in refine_sql: %s",
                            "; ".join(refine_notes),
                        )
                    dialect_ok, dialect_reason = self._validate_tidb_dialect(sql)
                    if not dialect_ok:
                        logger.warning(
                            "NL2SQLChain refine_sql TiDB dialect invalid sql_preview=%r reason=%s",
                            _text_preview(sql, 0),
                            dialect_reason,
                        )
                        return "", validation_ctx
                    ph_ok, ph_reason = self._validate_unresolved_time_placeholders(sql)
                    if not ph_ok:
                        logger.warning(
                            "NL2SQLChain refine_sql unresolved time placeholders reason=%s",
                            ph_reason,
                        )
                        return "", validation_ctx
                    valid, validation_error = self._validate_sql(
                        sql,
                        question=question,
                        allowed_tables=allowed_tables,
                        allowed_columns=allowed_columns,
                        enforce_column_whitelist=schema_ok,
                        table_columns=table_columns_map if schema_ok else None,
                        join_whitelist=join_whitelist,
                        entity_rules=entity_rules,
                    )
                    if not valid:
                        logger.warning(
                            "NL2SQLChain refine_sql still invalid sql_preview=%r reason=%s",
                            _text_preview(sql, 0),
                            validation_error,
                        )
                        return "", validation_ctx
                    logger.info(
                        "NL2SQLChain refine_sql ok sql_len=%d preview=%r",
                        len(sql or ""),
                        _text_preview(sql, 0),
                    )
                except Exception:
                    logger.exception("NL2SQLChain: refine_sql failed, return empty SQL.")
                    return "", validation_ctx
            else:
                logger.warning("NL2SQLChain validation failed and no LangChain; return empty SQL")
                return "", validation_ctx

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
            _text_preview(sql, 0),
        )
        self._maybe_store_nl2sql_sql_cache(
            cache_key=cache_key_for_store,
            backend=sql_cache_backend,
            sql=sql or "",
            enabled=cfg_analysis.nl2sql_cache_enabled,
        )
        self._maybe_store_nl2sql_l1_cache(
            cache_key=l1_cache_key_for_store,
            backend=l1_cache_backend,
            sql=sql or "",
            enabled=cfg_analysis.nl2sql_cache_enabled and cfg_analysis.nl2sql_l1_cache_enabled,
        )
        await self._maybe_upsert_nl2sql_qa_feedback(
            cfg_analysis=cfg_analysis,
            db_cfg=db_cfg,
            question=question,
            sql=sql or "",
            analysis_type=analysis_type,
            plan_item_id=plan_item_id,
            plan_template_version=plan_template_version,
            system_prefix_snapshot=system_prefix if system_prefix else None,
            data_source_fp=data_source_fp,
            schema_fp=schema_fp,
            policy_fp=policy_fp,
            fresh_generation=fresh_sql_generation,
        )
        return sql, validation_ctx

    async def _maybe_upsert_nl2sql_qa_feedback(
        self,
        *,
        cfg_analysis: Any,
        db_cfg: Any,
        question: str,
        sql: str,
        analysis_type: str | None,
        plan_item_id: str | None,
        plan_template_version: str | None,
        system_prefix_snapshot: str | None,
        data_source_fp: str,
        schema_fp: str,
        policy_fp: str,
        fresh_generation: bool,
    ) -> None:
        if not getattr(cfg_analysis, "nl2sql_qa_feedback_enabled", False):
            return
        if db_cfg is None or not (data_source_fp and schema_fp):
            return
        if not (sql or "").strip():
            return
        only_fresh = os.getenv("NL2SQL_QA_FEEDBACK_ONLY_FRESH_SQL", "true").lower() == "true"
        if only_fresh and not fresh_generation:
            return
        from app.nl2sql.qa_feedback import analysis_accepts_auto_qa_feedback

        if not analysis_accepts_auto_qa_feedback(analysis_type, plan_item_id):
            logger.debug(
                "NL2SQLChain: QA feedback skipped (requires analysis_type + plan_item_id) "
                "analysis_type=%r plan_item_id=%r",
                analysis_type,
                plan_item_id,
            )
            return
        try:
            await asyncio.to_thread(
                self._rag.upsert_auto_feedback_qa_pair,
                question=question,
                sql=sql,
                data_source_fp=data_source_fp,
                schema_fp=schema_fp,
                policy_fp=policy_fp,
                analysis_type=analysis_type,
                plan_item_id=plan_item_id,
                plan_template_version=plan_template_version,
                prompt_prefix_snapshot=system_prefix_snapshot,
            )
        except Exception:
            logger.exception("NL2SQLChain: nl2sql QA feedback upsert failed")

    @staticmethod
    def _maybe_store_nl2sql_sql_cache(
        *,
        cache_key: str | None,
        backend: Any,
        sql: str,
        enabled: bool,
    ) -> None:
        if not enabled or not cache_key or backend is None:
            return
        if not (sql or "").strip():
            return
        try:
            backend.set(cache_key, sql)
        except Exception:
            logger.exception("NL2SQLChain sql_cache set failed")

    @staticmethod
    def _maybe_store_nl2sql_l1_cache(
        *,
        cache_key: str | None,
        backend: Any,
        sql: str,
        enabled: bool,
    ) -> None:
        if not enabled or not cache_key or backend is None:
            return
        if not (sql or "").strip():
            return
        try:
            from app.nl2sql.sql_skeleton import extract_time_skeleton_from_sql, skeleton_payload_to_json

            payload = extract_time_skeleton_from_sql(sql)
            if not payload:
                return
            backend.set(cache_key, skeleton_payload_to_json(payload))
        except Exception:
            logger.exception("NL2SQLChain sql_l1_cache set failed")

    def _postprocess_and_validate_candidate_sql(
        self,
        sql: str,
        *,
        question: str,
        time_intent_source: str,
        validation_ctx: NL2SQLValidationContext,
        entity_rules: list[EntityRule],
        log_label: str,
        plan_item_id: str | None = None,
    ) -> tuple[str, bool, str | None]:
        """normalize → TiDB 改写 → 时间/区域 filter → 方言与 whitelist 校验（L2/L1/QA replay 共用）。"""
        from app.nl2sql.question_intent import scope_literals_from_parsed_intent

        scope_literals = scope_literals_from_parsed_intent(validation_ctx.parsed_intent)
        sql = self._validator.normalize_sql(sql)
        sql, rewrite_notes = self._rewrite_tidb_compatible_sql(sql)
        rewrite_meta: dict[str, Any] = {}
        sql, filter_notes = self._rewrite_query_filters(
            sql,
            question=question,
            time_intent_source=time_intent_source,
            plan_item_id=plan_item_id,
            scope_literals=scope_literals,
            parsed_intent=validation_ctx.parsed_intent,
            analysis_type=validation_ctx.analysis_type,
            rewrite_meta=rewrite_meta,
        )
        self._apply_rewrite_meta_to_parsed_intent(validation_ctx.parsed_intent, rewrite_meta)
        rewrite_notes.extend(filter_notes)
        if rewrite_notes:
            logger.info(
                "NL2SQLChain %s TiDB/filter rewrite applied: %s",
                log_label,
                "; ".join(rewrite_notes),
            )
        ph_ok, ph_reason = self._validate_unresolved_time_placeholders(sql)
        if not ph_ok:
            return sql, False, ph_reason
        dialect_ok, dialect_reason = self._validate_tidb_dialect(sql)
        if not dialect_ok:
            return sql, False, dialect_reason
        table_columns_map = (
            {k: set(v) for k, v in validation_ctx.table_columns.items()}
            if validation_ctx.schema_ok
            else None
        )
        # QA strict replay 信任 slot 中已审核 SQL，跳过 flat 列白名单（仍保留表白名单与列绑定等校验）。
        enforce_column_whitelist = validation_ctx.schema_ok and log_label != "qa_replay"
        valid, validation_error = self._validate_sql(
            sql,
            question=question,
            allowed_tables=set(validation_ctx.allowed_tables),
            allowed_columns=set(validation_ctx.allowed_columns),
            enforce_column_whitelist=enforce_column_whitelist,
            table_columns=table_columns_map,
            join_whitelist=set(validation_ctx.join_whitelist),
            entity_rules=entity_rules,
        )
        if not valid:
            return sql, False, validation_error
        return sql, True, None

    async def _try_qa_slot_strict_replay(
        self,
        *,
        nl2sql_qa_ctx: Any,
        question: str,
        time_intent_source: str,
        validation_ctx: NL2SQLValidationContext,
        entity_rules: list[EntityRule],
        user_id: str | None,
        plan_item_id: str | None,
    ) -> str | None:
        """slot 命中时解析 QA SQL，经后处理校验通过后跳过 LLM。"""
        from app.nl2sql.qa_feedback import (
            fetch_nl2sql_qa_chunks_by_slot,
            parse_sql_from_nl2sql_qa_text,
        )

        inner_rag = getattr(self._rag, "_rag", None)
        if inner_rag is None:
            logger.info(
                "NL2SQLChain qa_replay=strict_skipped reason=no_rag_backend plan_item_id=%s",
                plan_item_id or "-",
            )
            return None

        chunks = fetch_nl2sql_qa_chunks_by_slot(inner_rag, nl2sql_qa_ctx, max_chunks=1)
        if not chunks:
            return None

        chunk = chunks[0]
        raw_sql = parse_sql_from_nl2sql_qa_text(chunk.text or "")
        if not raw_sql.strip():
            logger.warning(
                "NL2SQLChain qa_replay=strict_failed reason=parse_empty plan_item_id=%s doc_name=%s",
                plan_item_id or "-",
                chunk.doc_name or "-",
            )
            return None

        sql, ok, fail_reason = self._postprocess_and_validate_candidate_sql(
            raw_sql,
            question=question,
            time_intent_source=time_intent_source,
            validation_ctx=validation_ctx,
            entity_rules=entity_rules,
            log_label="qa_replay",
            plan_item_id=plan_item_id,
        )
        if not ok:
            logger.warning(
                "NL2SQLChain qa_replay=strict_failed reason=%s plan_item_id=%s doc_name=%s fallback=cache_llm",
                fail_reason or "unknown",
                plan_item_id or "-",
                chunk.doc_name or "-",
            )
            return None

        logger.info(
            "NL2SQLChain qa_replay=strict ok sql_len=%d plan_item_id=%s doc_name=%s",
            len(sql or ""),
            plan_item_id or "-",
            chunk.doc_name or "-",
        )
        self._ls_tracker.log_run(
            name="nl2sql",
            run_type="llm",
            inputs={
                "user_id": user_id,
                "question": question,
            },
            outputs={"sql": sql},
            metadata={
                "scene": "nl2sql",
                "qa_replay": "strict",
                "plan_item_id": plan_item_id or "-",
            },
        )
        return sql

    async def refine_sql_after_executor_error(
        self,
        question: str,
        bad_sql: str,
        error_message: str,
        *,
        ctx: NL2SQLValidationContext,
        time_intent_text: str | None = None,
    ) -> str:
        """
        在 EXPLAIN / SELECT 执行失败后，将数据库错误信息喂给 LLM 做有限次修正（需 LangChain）。
        返回空字符串表示放弃修正。
        """
        if self._lc_chat_model is None:
            return ""
        time_src = (
            time_intent_text.strip()
            if time_intent_text is not None
            else question
        )
        entity_rules = load_entity_rules_from_env()
        from app.nl2sql.question_intent import scope_literals_from_parsed_intent

        scope_literals = scope_literals_from_parsed_intent(ctx.parsed_intent)
        try:
            refined = await self._refine_sql(
                question=question,
                original_sql=bad_sql,
                validation_error=f"MySQL / executor: {error_message}",
            )
            refined = self._validator.normalize_sql(refined)
            refined, rewrite_notes = self._rewrite_tidb_compatible_sql(refined)
            refined, filter_notes = self._rewrite_query_filters(
                refined,
                question=question,
                time_intent_source=time_src,
                scope_literals=scope_literals,
                parsed_intent=ctx.parsed_intent,
                analysis_type=ctx.analysis_type,
            )
            rewrite_notes.extend(filter_notes)
            ph_ok, ph_reason = self._validate_unresolved_time_placeholders(refined)
            if not ph_ok:
                logger.warning(
                    "NL2SQLChain refine_sql_after_executor_error unresolved placeholders reason=%s",
                    ph_reason,
                )
                return ""
            if rewrite_notes:
                logger.info(
                    "NL2SQLChain TiDB rewrite applied in refine_sql_after_executor_error: %s",
                    "; ".join(rewrite_notes),
                )
            dialect_ok, dialect_reason = self._validate_tidb_dialect(refined)
            if not dialect_ok:
                logger.warning(
                    "NL2SQLChain refine_sql_after_executor_error TiDB dialect invalid preview=%r reason=%s",
                    _text_preview(refined, 0),
                    dialect_reason,
                )
                return ""
            ok, err = self._validate_sql(
                refined,
                question=question,
                allowed_tables=set(ctx.allowed_tables),
                allowed_columns=set(ctx.allowed_columns),
                enforce_column_whitelist=ctx.schema_ok,
                table_columns={k: set(v) for k, v in ctx.table_columns.items()} if ctx.schema_ok else None,
                join_whitelist=set(ctx.join_whitelist),
                entity_rules=entity_rules,
            )
            if not ok:
                logger.warning(
                    "NL2SQLChain refine_sql_after_executor_error still invalid preview=%r reason=%s",
                    _text_preview(refined, 0),
                    err,
                )
                return ""
            return refined
        except Exception:
            logger.exception("NL2SQLChain.refine_sql_after_executor_error failed")
            return ""

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

    async def _refine_sql(
        self,
        question: str,
        original_sql: str,
        validation_error: str | None = None,
        *,
        strict_schema_reminder: bool = False,
    ) -> str:
        """
        当初始 SQL 未通过 SQLValidator 校验时的自检与修正步骤。

        当前版本：
        - 将原始 SQL 与问题一起交给 LLM，请其生成“更安全、仅含 SELECT 的 SQL”；
        - validation_error 中的 unknown tables/columns、binding 失败等应被修正；
        - strict_schema_reminder=True：校验失败后的单次修正（合并原双轮提示），强调消除校验错误中的违规表/列/绑定。
        - strict_schema_reminder=False：方言/执行错误等首轮修正，措辞略简。
        """
        from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore[import-not-found]

        system = (
            "你是一个 NL2SQL SQL 修正助手。"
            "给定用户问题与一条可能存在安全风险或不符合只读要求的 SQL，"
            "请输出一条仅包含安全 SELECT 查询的 SQL，不要包含 DROP/DELETE/UPDATE/INSERT 等写操作。"
            " 输出为单行可执行 SQL：除字符串字面量内部外不要换行或多余缩进。"
            " 若问题涉及锅炉/设备名称与明细记录等多实体，应通过 JOIN 关联台账表与事实表，禁止用 boiler_id='1' 等臆造数字代替「一号锅炉」类名称条件。"
            " 禁止使用 SELECT *、tbl.* 或别名.*；SELECT 列表中的列须为真实业务列名（不得用星号代替）。"
            " 多表 JOIN 时：每个表别名或表名后的限定列必须属于该表对应业务含义下的列，禁止张冠李戴。"
            " 当前数据库方言为 TiDB/MySQL："
            "1) 禁止使用 PostgreSQL 语法（例如 INTERVAL '7 days'）；"
            "2) 禁止使用高风险别名（如 load、row_number）；"
            "3) 默认禁止窗口函数与 OVER()/LAG()/LEAD()/ROW_NUMBER()，请改写为普通聚合或直接去除窗口依赖。"
        )
        if strict_schema_reminder:
            system += (
                " 【校验修正·强制】你必须严格消除「校验失败原因」中列出的违规项："
                "unknown tables/columns 则替换或删除非法标识符；"
                "column-table binding 则修正 alias.col，使 col 属于该别名对应物理表的列集合；"
                "join key not in whitelist 则改用允许的 ON 条件。"
                " 仍须禁止 SELECT *。"
            )
        err = validation_error or "unknown"
        if strict_schema_reminder:
            human_content = (
                f"用户问题: {question}\n"
                f"待修正 SQL: {original_sql}\n"
                f"【须消除的校验错误】\n{err}\n"
                "请逐条对照修正：勿保留违规表名/列名或错误的 alias.col 绑定；禁止 SELECT *。\n"
                "在保证业务语义的前提下，输出单行仅 SELECT（无 markdown）。"
            )
        else:
            human_content = (
                f"用户问题: {question}\n"
                f"初稿 SQL: {original_sql}\n"
                f"校验失败原因: {err}\n"
                "请在保证语义合理的前提下，输出一条安全的仅 SELECT 语句（单行，无 markdown）。"
            )

        messages: list[object] = [
            SystemMessage(content=system),
            HumanMessage(content=human_content),
        ]
        resp = await self._lc_chat_model.ainvoke(messages)  # type: ignore[union-attr]
        content = resp.content if hasattr(resp, "content") else str(resp)
        out = content.strip()
        logger.debug("NL2SQLChain._refine_sql output_len=%d preview=%r", len(out), _text_preview(out, 160))
        return out

    def _rewrite_tidb_compatible_sql(self, sql: str) -> tuple[str, list[str]]:
        """对 LLM SQL 进行 TiDB 兼容重写（高风险 alias + PostgreSQL interval + 可选窗口降级）。"""
        s = self._validator.normalize_sql(sql)
        notes: list[str] = []
        if not s:
            return s, notes
        s, alias_notes = self._rewrite_high_risk_aliases(s)
        notes.extend(alias_notes)
        s, interval_notes = self._rewrite_postgres_interval_literal(s)
        notes.extend(interval_notes)
        window_policy = os.getenv("NL2SQL_TIDB_WINDOW_POLICY", "refine").strip().lower()
        if window_policy == "degrade" and self._contains_window_functions(s):
            s, window_notes = self._degrade_window_functions(s)
            notes.extend(window_notes)
        return s, notes

    def _rewrite_high_risk_aliases(self, sql: str) -> tuple[str, list[str]]:
        notes: list[str] = []
        rewritten = sql
        for bad in sorted(self._tidb_forbidden_aliases):
            good = self._safe_alias_forbidden(bad)
            pat = re.compile(rf"(?i)\bAS\s+(`?){re.escape(bad)}\1\b")
            if pat.search(rewritten):
                rewritten = pat.sub(lambda m: f"AS {good}", rewritten)
                rewritten = self._replace_identifier_outside_quotes(rewritten, bad, good)
                notes.append(f"alias {bad}->{good}")
        return rewritten, notes

    def _rewrite_postgres_interval_literal(self, sql: str) -> tuple[str, list[str]]:
        notes: list[str] = []
        rewritten = sql
        unit_map = {
            "days": "DAY",
            "day": "DAY",
            "hours": "HOUR",
            "hour": "HOUR",
            "minutes": "MINUTE",
            "minute": "MINUTE",
            "months": "MONTH",
            "month": "MONTH",
            "years": "YEAR",
            "year": "YEAR",
        }

        def _repl(m: re.Match[str]) -> str:
            num = m.group(1)
            unit = unit_map.get(m.group(2).lower(), m.group(2).upper())
            notes.append(f"interval_literal->{num} {unit}")
            return f"INTERVAL {num} {unit}"

        rewritten = self._tidb_postgres_interval_pattern.sub(_repl, rewritten)
        return rewritten, notes

    def _degrade_window_functions(self, sql: str) -> tuple[str, list[str]]:
        notes: list[str] = []
        rewritten = sql
        patterns = [
            (re.compile(r"\bLAG\s*\([^)]*\)\s*OVER\s*\([^)]*\)", re.IGNORECASE), "NULL"),
            (re.compile(r"\bLEAD\s*\([^)]*\)\s*OVER\s*\([^)]*\)", re.IGNORECASE), "NULL"),
            (re.compile(r"\bROW_NUMBER\s*\(\s*\)\s*OVER\s*\([^)]*\)", re.IGNORECASE), "1"),
            (re.compile(r"\bRANK\s*\(\s*\)\s*OVER\s*\([^)]*\)", re.IGNORECASE), "1"),
            (re.compile(r"\bDENSE_RANK\s*\(\s*\)\s*OVER\s*\([^)]*\)", re.IGNORECASE), "1"),
        ]
        for pat, replacement in patterns:
            if pat.search(rewritten):
                rewritten = pat.sub(replacement, rewritten)
                notes.append("degrade_window_function")
        return rewritten, notes

    def _contains_window_functions(self, sql: str) -> bool:
        return bool(self._tidb_window_pattern.search(sql) or self._tidb_lag_like_pattern.search(sql))

    def _rewrite_query_filters(
        self,
        sql: str,
        *,
        question: str,
        time_intent_source: str | None = None,
        plan_item_id: str | None = None,
        scope_literals: dict[str, str | int | None] | None = None,
        parsed_intent: dict[str, Any] | None = None,
        analysis_type: str | None = None,
        rewrite_meta: dict[str, Any] | None = None,
    ) -> tuple[str, list[str]]:
        """P2：优化口径（通用时间语义动态窗 + 机组/锅炉范围 + 区域放宽匹配）。"""
        notes: list[str] = []
        rewritten = sql
        meta = rewrite_meta if rewrite_meta is not None else {}
        time_window = self._resolve_time_window_for_rewrite(
            question=question,
            time_intent_source=time_intent_source,
            parsed_intent=parsed_intent,
            analysis_type=analysis_type,
            rewrite_meta=meta,
        )
        if time_window is not None:
            start_expr, end_expr, tag = time_window
            if self._sql_has_time_placeholders(rewritten):
                rewritten, ph_notes = self._rewrite_time_placeholders(
                    rewritten,
                    start_expr=start_expr,
                    end_expr=end_expr,
                )
                notes.extend(ph_notes)
            rewritten, time_notes = self._rewrite_dynamic_time_window(
                rewritten,
                start_expr=start_expr,
                end_expr=end_expr,
                tag=tag,
            )
            notes.extend(time_notes)
            rewritten, bound_notes = self._inject_missing_time_upper_bounds(
                rewritten,
                start_expr=start_expr,
                end_expr=end_expr,
                tag=tag,
            )
            notes.extend(bound_notes)
            rewritten = self._normalize_end_time_upper_to_start_time(rewritten, end_expr=end_expr)
            rewritten = self._dedupe_redundant_time_upper_bounds(rewritten, end_expr=end_expr)
        rewritten, gc_notes = self._rewrite_group_concat_utf8_safe(rewritten, plan_item_id=plan_item_id)
        notes.extend(gc_notes)
        rewritten, scope_notes = self._rewrite_entity_scope_literals(
            rewritten,
            question=question,
            time_intent_source=time_intent_source,
            scope_literals=scope_literals,
        )
        notes.extend(scope_notes)
        rewritten, region_notes = self._rewrite_relaxed_region_match(rewritten, question=question)
        notes.extend(region_notes)
        return rewritten, notes

    @staticmethod
    def _sql_has_time_placeholders(sql: str) -> bool:
        return bool(re.search(r"@t_(?:start|end|after)\b", sql, re.IGNORECASE))

    @classmethod
    def _is_plan_override_window_tag(cls, tag: str) -> bool:
        if tag in cls._PLAN_OVERRIDE_WINDOW_TAGS:
            return True
        return tag.startswith("recent_") and tag not in cls._DAY_WINDOW_TAGS

    def _rewrite_time_placeholders(
        self,
        sql: str,
        *,
        start_expr: str,
        end_expr: str,
    ) -> tuple[str, list[str]]:
        """将 @t_start/@t_end/@t_after 替换为当前生效时间窗（与字面量改写一致）。"""
        notes: list[str] = []
        rewritten = sql
        replacements = (
            (r"@t_start\b", start_expr, "time_placeholder_t_start"),
            (r"@t_end\b", end_expr, "time_placeholder_t_end"),
            (r"@t_after\b", end_expr, "time_placeholder_t_after"),
        )
        for pat, expr, note in replacements:
            if re.search(pat, rewritten, re.IGNORECASE):
                rewritten = re.sub(pat, expr, rewritten, flags=re.IGNORECASE)
                notes.append(note)
        return rewritten, notes

    @staticmethod
    def _time_anchor_from_parsed_intent(
        parsed_intent: dict[str, Any] | None,
    ) -> tuple[str, str] | None:
        if not parsed_intent:
            return None
        raw = parsed_intent.get("time_anchor")
        if not isinstance(raw, dict):
            return None
        end_expr = raw.get("end_expr")
        tag = raw.get("tag")
        if not end_expr or not tag:
            return None
        return str(end_expr), str(tag)

    @staticmethod
    def _apply_rewrite_meta_to_parsed_intent(
        parsed_intent: dict[str, Any] | None,
        rewrite_meta: dict[str, Any],
    ) -> None:
        if parsed_intent is None:
            return
        warnings = rewrite_meta.get("time_rewrite_warnings")
        if isinstance(warnings, list) and warnings:
            parsed_intent["time_rewrite_warnings"] = list(warnings)
        effective = rewrite_meta.get("effective_time_window")
        if isinstance(effective, dict) and effective:
            parsed_intent["effective_time_window"] = dict(effective)

    @classmethod
    def _anchor_fallback_now_allowed(cls, analysis_type: str | None) -> bool:
        from app.nl2sql.intent_config import (
            anchor_fallback_analysis_types,
            anchor_fallback_now_enabled,
        )

        if not anchor_fallback_now_enabled():
            return False
        at = (analysis_type or "").strip()
        if not at:
            return False
        return at in anchor_fallback_analysis_types()

    @staticmethod
    def _record_effective_time_window(
        rewrite_meta: dict[str, Any],
        start_expr: str,
        end_expr: str,
        tag: str,
    ) -> None:
        rewrite_meta["effective_time_window"] = {
            "start_expr": start_expr,
            "end_expr": end_expr,
            "tag": tag,
        }

    @staticmethod
    def _append_time_rewrite_warning(rewrite_meta: dict[str, Any], code: str) -> None:
        warnings = rewrite_meta.setdefault("time_rewrite_warnings", [])
        if code not in warnings:
            warnings.append(code)

    def _validate_unresolved_time_placeholders(self, sql: str) -> tuple[bool, str | None]:
        from app.nl2sql.intent_config import reject_unresolved_time_placeholders

        if not reject_unresolved_time_placeholders():
            return True, None
        if self._sql_has_time_placeholders(sql):
            return (
                False,
                "unresolved time placeholders (@t_start/@t_end/@t_after): "
                "用户问句未解析到可改写时间窗，请在问题中补充明确时间或事故时刻",
            )
        return True, None

    def _resolve_time_window_for_rewrite(
        self,
        *,
        question: str,
        time_intent_source: str | None,
        parsed_intent: dict[str, Any] | None = None,
        analysis_type: str | None = None,
        rewrite_meta: dict[str, Any] | None = None,
    ) -> tuple[str, str, str] | None:
        """
        解析生效时间窗：
        0) plan 含「锚点向前 N 天」且用户问句已解析锚点 → [anchor_end - N, anchor_end)；
           无锚点且允许 fallback → [NOW()-N, NOW())（泄爆等）；
           无锚点且不允许 fallback → 记录 anchor_lookback_skipped_no_anchor；
        1) plan 子任务显式长窗（近一年等）优先；
        2) 用户 time_intent 的 today/yesterday/前天 优先于问句内误触发的字面年月；
        3) 用户 time_intent 任意解析结果（含 day_cur_* 等具体日期）优先于 plan 长问句
           尾部 RAG 规则线索中的 yesterday/today 等 DAY 标签；
        4) 其余从 task / 默认回落。
        """
        from app.nl2sql.time_intent_display import (
            build_anchor_lookback_time_window,
            parse_plan_anchor_lookback_days,
        )

        meta = rewrite_meta if rewrite_meta is not None else {}

        task_q = (question or "").strip()
        lookback = parse_plan_anchor_lookback_days(task_q) if task_q else None
        if lookback is not None:
            anchor = self._time_anchor_from_parsed_intent(parsed_intent)
            if anchor is not None:
                anchor_end, _anchor_tag = anchor
                win = build_anchor_lookback_time_window(anchor_end, lookback)
                self._record_effective_time_window(meta, win[0], win[1], win[2])
                return win
            if self._anchor_fallback_now_allowed(analysis_type):
                win = build_anchor_lookback_time_window("NOW()", lookback)
                start, end, tag = win
                self._record_effective_time_window(
                    meta, start, end, f"{tag}_fallback_now"
                )
                self._append_time_rewrite_warning(meta, "anchor_fallback_now")
                return win
            self._append_time_rewrite_warning(meta, "anchor_lookback_skipped_no_anchor")
            return None

        intent_q = (time_intent_source or "").strip() or task_q
        task_win = self._extract_time_window_from_question(task_q) if task_q else None
        intent_win = self._extract_time_window_from_question(intent_q) if intent_q else None

        if task_win and self._is_plan_override_window_tag(task_win[2]):
            self._record_effective_time_window(meta, task_win[0], task_win[1], task_win[2])
            return task_win

        if intent_win and intent_win[2] in self._DAY_WINDOW_TAGS:
            self._record_effective_time_window(meta, intent_win[0], intent_win[1], intent_win[2])
            return intent_win

        if intent_win:
            self._record_effective_time_window(meta, intent_win[0], intent_win[1], intent_win[2])
            return intent_win

        if task_win and task_win[2] in self._DAY_WINDOW_TAGS:
            self._record_effective_time_window(meta, task_win[0], task_win[1], task_win[2])
            return task_win
        if task_win:
            self._record_effective_time_window(meta, task_win[0], task_win[1], task_win[2])
            return task_win
        from app.nl2sql.time_intent_display import default_time_window_sql_fallback

        win = default_time_window_sql_fallback()
        self._record_effective_time_window(meta, win[0], win[1], win[2])
        if not parsed_intent or not parsed_intent.get("time_window_tag"):
            self._append_time_rewrite_warning(meta, "default_yesterday_fallback")
        return win

    @staticmethod
    def _extract_numeric_window(q: str, unit_keys: tuple[str, ...]) -> int | None:
        from app.nl2sql.time_intent_display import extract_numeric_window

        return extract_numeric_window(q, unit_keys)

    def _extract_time_window_from_question(self, question: str) -> tuple[str, str, str] | None:
        from app.nl2sql.time_intent_display import extract_time_window_from_question

        return extract_time_window_from_question(question)

    def _rewrite_dynamic_time_window(
        self,
        sql: str,
        *,
        start_expr: str,
        end_expr: str,
        tag: str,
    ) -> tuple[str, list[str]]:
        notes: list[str] = []
        rewritten = sql
        lit = NL2SQLChain._TIME_LITERAL_RHS

        def _is_time_col(col: str) -> bool:
            c = col.lower().split(".")[-1]
            return c.endswith("time") or c.endswith("date") or c == "ts" or c.endswith("timestamp")

        def _is_date_literal(val: str) -> bool:
            core = val.strip().strip("'")
            return bool(
                re.match(r"^\d{4}-\d{2}-\d{2}", core)
                or re.match(r"^\d{4}/\d{2}/\d{2}", core)
            )

        between_pat = re.compile(
            rf"(?i)\b([a-zA-Z_][a-zA-Z0-9_\.]*)\s+BETWEEN\s+({lit})\s+AND\s+({lit})"
        )

        def _between_repl(m: re.Match[str]) -> str:
            col = m.group(1)
            if not _is_time_col(col):
                return m.group(0)
            notes.append(f"dynamic_time_window_between:{tag}")
            return f"{col} >= {start_expr} AND {col} < {end_expr}"

        rewritten = between_pat.sub(_between_repl, rewritten)
        ge_pat = re.compile(rf"(?i)\b([a-zA-Z_][a-zA-Z0-9_\.]*)\s*>=\s*({lit})")
        if ge_pat.search(rewritten):
            rewritten = ge_pat.sub(
                lambda m: (
                    f"{m.group(1)} >= {start_expr}"
                    if _is_time_col(m.group(1)) and _is_date_literal(m.group(2))
                    else m.group(0)
                ),
                rewritten,
            )
            notes.append(f"dynamic_time_window_ge:{tag}")
        le_pat = re.compile(rf"(?i)\b([a-zA-Z_][a-zA-Z0-9_\.]*)\s*<=\s*({lit})")
        if le_pat.search(rewritten):
            rewritten = le_pat.sub(
                lambda m: (
                    f"{m.group(1)} < {end_expr}"
                    if _is_time_col(m.group(1)) and _is_date_literal(m.group(2))
                    else m.group(0)
                ),
                rewritten,
            )
            notes.append(f"dynamic_time_window_le:{tag}")
        lt_pat = re.compile(rf"(?i)\b([a-zA-Z_][a-zA-Z0-9_\.]*)\s*<\s*({lit})")
        if lt_pat.search(rewritten):
            rewritten = lt_pat.sub(
                lambda m: (
                    f"{m.group(1)} < {end_expr}"
                    if _is_time_col(m.group(1)) and _is_date_literal(m.group(2))
                    else m.group(0)
                ),
                rewritten,
            )
            notes.append(f"dynamic_time_window_lt:{tag}")
        eq_pat = re.compile(rf"(?i)\b([a-zA-Z_][a-zA-Z0-9_\.]*)\s*=\s*({lit})")
        if eq_pat.search(rewritten):
            rewritten = eq_pat.sub(
                lambda m: (
                    f"{m.group(1)} >= {start_expr} AND {m.group(1)} < {end_expr}"
                    if _is_time_col(m.group(1)) and _is_date_literal(m.group(2))
                    else m.group(0)
                ),
                rewritten,
            )
            notes.append(f"dynamic_time_window_eq_to_range:{tag}")
        rewritten, ds_notes = self._rewrite_date_sub_time_anchors(
            rewritten,
            start_expr=start_expr,
            end_expr=end_expr,
            tag=tag,
        )
        notes.extend(ds_notes)
        if not notes:
            col_hint = re.search(r"(?i)\b([a-zA-Z_][a-zA-Z0-9_\.]*)\b", rewritten)
            if col_hint:
                col = col_hint.group(1)
                if not _is_time_col(col):
                    return rewritten, notes
                if re.search(r"(?i)\bwhere\b", rewritten):
                    rewritten = re.sub(
                        r"(?i)\bwhere\b",
                        f"WHERE {col} >= {start_expr} AND {col} < {end_expr} AND ",
                        rewritten,
                        count=1,
                    )
                else:
                    rewritten = f"{rewritten} WHERE {col} >= {start_expr} AND {col} < {end_expr}"
                notes.append(f"dynamic_time_window_injected:{tag}")
        return rewritten, notes

    @classmethod
    def _should_inject_time_upper_bound(cls, tag: str) -> bool:
        if tag.startswith("anchor_lookback_"):
            return False
        if cls._is_plan_override_window_tag(tag) or tag.startswith("recent_"):
            return False
        if tag in cls._HALF_OPEN_WINDOW_TAGS:
            return True
        return (
            tag.startswith("month_")
            or tag.startswith("year_")
            or tag.startswith("quarter_")
            or tag.startswith("day_")
            or tag.startswith("month_cur_")
        )

    @classmethod
    def _time_clause_local_region(cls, sql: str, ge_end: int, *, max_len: int = 640) -> str:
        """从 >= 匹配点向后截取同一 WHERE 子句片段，用于判断该处是否已有上界。"""
        chunk = sql[ge_end : ge_end + max_len]
        boundary = re.search(
            r"(?i)\b(GROUP\s+BY|ORDER\s+BY|LIMIT|UNION\b|\)\s*(?:GROUP|ORDER|LIMIT|WHERE|\w+))",
            chunk,
        )
        return chunk[: boundary.start()] if boundary else chunk

    @classmethod
    def _has_local_time_upper_bound(
        cls,
        sql: str,
        *,
        col: str,
        end_expr: str,
        ge_end: int,
    ) -> bool:
        region = cls._time_clause_local_region(sql, ge_end)
        upper_pat = re.compile(
            rf"(?i){re.escape(col)}\s*(?:<|<=)\s*{re.escape(end_expr)}"
        )
        return bool(upper_pat.search(region))

    def _inject_missing_time_upper_bounds(
        self,
        sql: str,
        *,
        start_expr: str,
        end_expr: str,
        tag: str,
    ) -> tuple[str, list[str]]:
        """对仅有下界（>= start_expr）而无上界（< end_expr）的时间列补齐半开区间上界。"""
        if not self._should_inject_time_upper_bound(tag):
            return sql, []
        notes: list[str] = []
        rewritten = sql
        ge_pat = re.compile(
            rf"(?i)(\b[a-zA-Z_][\w]*\.(?:start_time|record_time|data_time|leakage_date|mark_time|ts|timestamp)"
            rf"|(?<![\w.])(?:start_time|record_time|data_time|leakage_date|mark_time|ts|timestamp))"
            rf"\s*>=\s*{re.escape(start_expr)}"
        )
        offset = 0
        for m in ge_pat.finditer(sql):
            col = m.group(1)
            ge_end = m.end() + offset
            if self._has_local_time_upper_bound(
                rewritten, col=col, end_expr=end_expr, ge_end=ge_end
            ):
                continue
            insert_at = ge_end
            suffix = f" AND {col} < {end_expr}"
            rewritten = rewritten[:insert_at] + suffix + rewritten[insert_at:]
            offset += len(suffix)
            notes.append(f"dynamic_time_window_injected_lt:{tag}")
        return rewritten, notes

    @staticmethod
    def _dedupe_redundant_time_upper_bounds(sql: str, *, end_expr: str) -> str:
        """移除同一列上重复的 `< end_expr` 条件（q6a 等 le+inject 叠加）。"""
        esc_end = re.escape(end_expr)
        pat = re.compile(
            rf"(?i)((?:\b[a-zA-Z_][\w]*\.)?(?:start_time|record_time|data_time|leakage_date|mark_time|ts|timestamp)\s*<\s*{esc_end})"
            rf"(?:\s+AND\s+\1)+"
        )
        return pat.sub(r"\1", sql)

    @staticmethod
    def _normalize_end_time_upper_to_start_time(sql: str, *, end_expr: str) -> str:
        """将 end_time 的上界条件归一到 start_time，避免跨天事件漏计/多计。"""
        esc_end = re.escape(end_expr)
        return re.sub(
            rf"(?i)(\b[a-zA-Z_][\w]*\.)end_time(\s*<\s*{esc_end})",
            r"\1start_time\2",
            sql,
        )

    @staticmethod
    def _rewrite_group_concat_utf8_safe(
        sql: str,
        *,
        plan_item_id: str | None = None,
    ) -> tuple[str, list[str]]:
        """
        q2c 等含 GROUP_CONCAT 中文拼接的查询：限制长度并显式 utf8mb4，降低驱动解码失败概率。
        """
        if (plan_item_id or "").lower() != "q2c":
            return sql, []
        if "GROUP_CONCAT" not in sql.upper():
            return sql, []
        marker = "CAST(GROUP_CONCAT("
        if marker in sql:
            return sql, []
        notes: list[str] = []
        rewritten = re.sub(
            r"(?is)GROUP_CONCAT\s*\((.*?)\)",
            lambda m: (
                "SUBSTRING(CAST(GROUP_CONCAT("
                f"{m.group(1)}) AS CHAR CHARACTER SET utf8mb4), 1, 4096)"
            ),
            sql,
            count=1,
        )
        if rewritten != sql:
            notes.append("group_concat_utf8_safe")
        return rewritten, notes

    def _rewrite_date_sub_time_anchors(
        self,
        sql: str,
        *,
        start_expr: str,
        end_expr: str,
        tag: str,
    ) -> tuple[str, list[str]]:
        """
        统一 QA 中 DATE_SUB(NOW()/CURDATE()/字面量, INTERVAL …) 锚点为当前时间窗（P1/P2）。
        """
        notes: list[str] = []
        lit = NL2SQLChain._TIME_LITERAL_RHS

        def _is_time_col(col: str) -> bool:
            c = col.lower().split(".")[-1]
            return c.endswith("time") or c.endswith("date") or c == "ts" or c.endswith("timestamp")

        rewritten = sql
        ge_datesub = re.compile(
            rf"(?i)\b([a-zA-Z_][a-zA-Z0-9_\.]*)\s*>=\s*"
            rf"DATE_SUB\s*\(\s*(?:{lit}|NOW\(\)|CURDATE\(\))\s*,\s*INTERVAL\s+\d+\s+\w+\s*\)"
        )
        if ge_datesub.search(rewritten):
            rewritten = ge_datesub.sub(
                lambda m: f"{m.group(1)} >= {start_expr}" if _is_time_col(m.group(1)) else m.group(0),
                rewritten,
            )
            notes.append(f"dynamic_time_window_ge_datesub:{tag}")

        lt_datesub = re.compile(
            rf"(?i)\b([a-zA-Z_][a-zA-Z0-9_\.]*)\s*<\s*"
            rf"DATE_SUB\s*\(\s*(?:{lit}|NOW\(\)|CURDATE\(\))\s*,\s*INTERVAL\s+\d+\s+\w+\s*\)"
        )
        if lt_datesub.search(rewritten):
            rewritten = lt_datesub.sub(
                lambda m: f"{m.group(1)} < {end_expr}" if _is_time_col(m.group(1)) else m.group(0),
                rewritten,
            )
            notes.append(f"dynamic_time_window_lt_datesub:{tag}")

        le_datesub = re.compile(
            rf"(?i)\b([a-zA-Z_][a-zA-Z0-9_\.]*)\s*<=\s*"
            rf"DATE_SUB\s*\(\s*(?:{lit}|NOW\(\)|CURDATE\(\))\s*,\s*INTERVAL\s+\d+\s+\w+\s*\)"
        )
        if le_datesub.search(rewritten):
            rewritten = le_datesub.sub(
                lambda m: f"{m.group(1)} < {end_expr}" if _is_time_col(m.group(1)) else m.group(0),
                rewritten,
            )
            notes.append(f"dynamic_time_window_le_datesub:{tag}")

        # anchor_lookback_* 下 @t_start 已替换为 DATE_SUB(锚点, INTERVAL n DAY)；
        # standalone 会把字面量锚点打回 CURDATE()，导致下界>上界，须跳过。
        if not tag.startswith("anchor_lookback_"):
            standalone_datesub = re.compile(
                rf"(?i)DATE_SUB\s*\(\s*{lit}\s*,\s*(INTERVAL\s+\d+\s+\w+)\s*\)"
            )

            def _standalone_repl(m: re.Match[str]) -> str:
                notes.append(f"dynamic_time_window_datesub_literal:{tag}")
                if tag == "recent_1_year":
                    return f"DATE_SUB(CURDATE(), {m.group(1)})"
                return f"DATE_SUB(CURDATE(), {m.group(1)})"

            if standalone_datesub.search(rewritten):
                rewritten = standalone_datesub.sub(_standalone_repl, rewritten)

        return rewritten, notes

    @staticmethod
    def _resolve_entity_scope_question(
        *, question: str, time_intent_source: str | None
    ) -> str:
        """
        实体范围解析用问句：优先用户原始 query（time_intent），避免 plan 任务长问句
        尾部 RAG「请结合以下规则线索」中的示例「1号锅炉」覆盖用户「2号机组」。
        """
        intent_q = (time_intent_source or "").strip()
        if intent_q:
            return intent_q
        return strip_plan_context_guide_suffix(question)

    @staticmethod
    def _cn_unit_index_to_int(raw: str) -> int | None:
        """机组/锅炉序号：阿拉伯或常见中文数字 → int（如 一→1、十二→12）。"""
        s = (raw or "").strip()
        if not s:
            return None
        if s.isdigit():
            return int(s)
        digit_map = {
            "零": 0,
            "一": 1,
            "二": 2,
            "两": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
        }
        if s in digit_map:
            return digit_map[s]
        if s == "十":
            return 10
        if "十" in s:
            left, _, right = s.partition("十")
            lv = digit_map.get(left, 1 if left == "" else -1)
            rv = digit_map.get(right, 0 if right == "" else -1)
            if lv >= 0 and rv >= 0:
                return lv * 10 + rv
        return None

    @classmethod
    def _boiler_scope_label_from_index(cls, raw: str) -> str | None:
        """序号归一化为 account_boiler.boiler_name 常用片段「阿拉伯数字+号锅炉」。"""
        s = (raw or "").strip()
        if not s:
            return None
        n = cls._cn_unit_index_to_int(s)
        if n is not None and n > 0:
            return f"{n}号锅炉"
        return f"{s}号锅炉"

    @classmethod
    def _extract_boiler_scope_label_from_question(cls, question: str) -> str | None:
        """
        从问句解析锅炉范围（与 account_boiler.boiler_name 一致，统一为「N号锅炉」）。
        用户若写「N号机组」「N#机组」「#N机组」「一号锅炉」等，均归一为「1号锅炉」形式。
        """
        q = (question or "").strip()
        if not q:
            return None
        m_boiler = re.search(r"(\d+|[一二两三四五六七八九十百]+)号锅炉", q)
        if m_boiler:
            return cls._boiler_scope_label_from_index(m_boiler.group(1))
        unit_as_boiler_patterns = (
            r"(\d+)号机组",
            r"([一二两三四五六七八九十百]+)号机组",
            r"(\d+)#机组",
            r"#(\d+)机组",
        )
        for pat in unit_as_boiler_patterns:
            m = re.search(pat, q)
            if m:
                return cls._boiler_scope_label_from_index(m.group(1))
        return None

    # 显式全厂/全机组意图（须在单机组解析之后判定，避免「1号锅炉」误触）
    _EXPLICIT_FULL_PLANT_SCOPE_RE = re.compile(
        r"(?:"
        r"全厂"
        r"|(?:所有|全部|各).{0,6}(?:锅炉|机组|单元)"
        r"|(?:锅炉|机组).{0,4}(?:整体|全部|所有)"
        r"|全.{0,2}(?:锅炉|机组)"
        r")"
    )

    @classmethod
    def _has_explicit_full_plant_scope(cls, question: str) -> bool:
        """问句是否显式要求全厂/所有机组（不含「N号锅炉」类单机组表述）。"""
        q = (question or "").strip()
        if not q:
            return False
        if cls._extract_boiler_scope_label_from_question(q):
            return False
        return bool(cls._EXPLICIT_FULL_PLANT_SCOPE_RE.search(q))

    @classmethod
    def _extract_unit_keyword_from_question(cls, question: str) -> str | None:
        """
        解析机组过滤关键字，供 @unit_keyword 占位符与 boiler_name LIKE 使用。

        返回值语义（与参考 SQL「空则全厂」一致）：
        - ``str``：单机组，如 ``1号锅炉``（归一化后的 boiler_name 片段）；
        - ``None``：全厂——含显式「所有机组/全厂/…」，或问句未指定任何机组。

        全厂返回 None 而非空串：解析层用 None 表达「无需过滤」；
        SQL 改写时将 None 落为 ``''``，使
        ``(@unit_keyword IS NULL OR @unit_keyword = '' OR … LIKE …)`` 中第二支为真。
        """
        q = (question or "").strip()
        if not q:
            return None
        boiler = cls._extract_boiler_scope_label_from_question(q)
        if boiler:
            return boiler
        if cls._has_explicit_full_plant_scope(q):
            return None
        return None

    @staticmethod
    def _extract_scope_literals_from_question(
        question: str,
        *,
        time_intent_source: str | None = None,
    ) -> dict[str, str | int | None]:
        """从问句提取锅炉/机组与设备范围；unit_keyword/boiler 为 None 表示全厂。"""
        from app.nl2sql.question_intent import scope_literals_from_question

        return scope_literals_from_question(
            question,
            time_intent_source=time_intent_source,
        )

    # QA 模板中常见的示例锅炉名字面量（strict replay 需按问句全局替换）
    _BOILER_UNIT_LITERAL_IN_QUOTES = re.compile(
        r"'(\d+号锅炉|[一二两三四五六七八九十百]+号锅炉)'"
    )

    @staticmethod
    def _sql_has_unit_keyword_placeholder(sql: str) -> bool:
        return bool(re.search(r"@unit_keyword\b", sql, re.IGNORECASE))

    @classmethod
    def _rewrite_unit_keyword_placeholders(
        cls,
        sql: str,
        unit_keyword: str | None,
    ) -> tuple[str, list[str]]:
        """
        将 @unit_keyword 替换为 SQL 字面量（与 @t_start/@t_end 时间占位符改写对称）。

        - 单机组：``'1号锅炉'``；
        - 全厂（None）：``''``，使模板中 ``@unit_keyword = ''`` 为真、跳过后续 LIKE。
        """
        if not cls._sql_has_unit_keyword_placeholder(sql):
            return sql, []
        notes: list[str] = []
        if unit_keyword is None:
            replacement = "''"
            notes.append("unit_keyword_placeholder_all_plants")
        else:
            safe = unit_keyword.replace("'", "''")
            replacement = f"'{safe}'"
            notes.append("unit_keyword_placeholder_single")
        rewritten = re.sub(r"@unit_keyword\b", replacement, sql, flags=re.IGNORECASE)
        return rewritten, notes

    def _rewrite_entity_scope_literals(
        self,
        sql: str,
        *,
        question: str,
        time_intent_source: str | None = None,
        scope_literals: dict[str, str | int | None] | None = None,
    ) -> tuple[str, list[str]]:
        """将 QA/LLM SQL 中示例锅炉名与 @unit_keyword 占位符替换为当前问句实体范围。"""
        notes: list[str] = []
        scopes = scope_literals
        if scopes is None:
            scopes = self._extract_scope_literals_from_question(
                question,
                time_intent_source=time_intent_source,
            )
        unit_keyword = scopes.get("unit_keyword")
        rewritten = sql

        if self._sql_has_unit_keyword_placeholder(rewritten):
            rewritten, uk_notes = self._rewrite_unit_keyword_placeholders(
                rewritten, unit_keyword
            )
            notes.extend(uk_notes)

        if boiler := scopes.get("boiler"):
            safe = boiler.replace("'", "''")
            boiler_pat = re.compile(
                r"(?i)\b([a-zA-Z_][a-zA-Z0-9_\.]*)\s*=\s*'([^']*锅炉[^']*)'"
            )

            def _boiler_repl(m: re.Match[str]) -> str:
                col = m.group(1)
                if "boiler" not in col.lower():
                    return m.group(0)
                notes.append("entity_scope_boiler_name")
                return f"{col} = '{safe}'"

            rewritten = boiler_pat.sub(_boiler_repl, rewritten)

            like_concat_pat = re.compile(
                r"(?i)\b([a-zA-Z_][a-zA-Z0-9_\.]*)\s+LIKE\s+CONCAT\s*\(\s*'%'\s*,\s*"
                r"'([^']*号锅炉[^']*)'\s*,\s*'%'\s*\)"
            )

            def _boiler_like_concat_repl(m: re.Match[str]) -> str:
                col = m.group(1)
                if "boiler" not in col.lower():
                    return m.group(0)
                notes.append("entity_scope_boiler_name")
                return f"{col} LIKE CONCAT('%', '{safe}', '%')"

            rewritten = like_concat_pat.sub(_boiler_like_concat_repl, rewritten)

            def _global_boiler_lit_repl(m: re.Match[str]) -> str:
                old = m.group(1)
                if old == boiler:
                    return m.group(0)
                notes.append("entity_scope_boiler_name")
                return f"'{safe}'"

            rewritten = self._BOILER_UNIT_LITERAL_IN_QUOTES.sub(_global_boiler_lit_repl, rewritten)

        from app.nl2sql.scope_sql_rewrite import rewrite_scope_sql_placeholders

        rewritten, scope_ph_notes = rewrite_scope_sql_placeholders(rewritten, scopes)
        notes.extend(scope_ph_notes)

        return rewritten, notes

    def _rewrite_relaxed_region_match(self, sql: str, *, question: str) -> tuple[str, list[str]]:
        notes: list[str] = []
        rewritten = sql
        # 对“区域/部位”类条件放宽匹配，避免严格等值导致 0 行。
        col_pat = re.compile(r"(?i)\b([a-zA-Z_][a-zA-Z0-9_\.]*)\s*=\s*'([^']{2,48})'")
        col_signals = (
            "area",
            "region",
            "zone",
            "location",
            "position",
            "part",
            "wall",
            "device_name",
            "point_name",
        )

        def _repl(m: re.Match[str]) -> str:
            col = m.group(1)
            col_l = col.lower()
            if not any(k in col_l for k in col_signals):
                return m.group(0)
            val = m.group(2).strip()
            if "%" in val:
                return m.group(0)
            if not any(
                k in val
                for k in ("墙", "壁", "区", "侧", "前", "后", "左", "右", "过热器", "再热器", "水冷", "front", "rear")
            ):
                return m.group(0)
            like_val = val.replace("'", "''")
            notes.append("relax_region_equals_to_like")
            return f"{col} LIKE '%{like_val}%'"

        rewritten = col_pat.sub(_repl, rewritten)
        return rewritten, notes

    def _validate_tidb_dialect(self, sql: str) -> tuple[bool, str | None]:
        s = self._validator.normalize_sql(sql)
        if not s:
            return False, "empty sql"
        aliases = self._extract_aliases(s)
        bad_aliases = sorted(a for a in aliases if a in self._tidb_forbidden_aliases)
        if bad_aliases:
            return False, f"forbidden alias for TiDB: {', '.join(bad_aliases)}"
        if self._tidb_postgres_interval_pattern.search(s):
            return False, "postgres interval literal is forbidden in TiDB/MySQL"
        allow_window = os.getenv("NL2SQL_TIDB_ALLOW_WINDOW", "false").strip().lower() == "true"
        if not allow_window and self._contains_window_functions(s):
            return False, "window functions (OVER/LAG/LEAD/ROW_NUMBER) are forbidden by TiDB policy"
        return True, None

    def _load_tidb_forbidden_aliases_from_env(self) -> set[str]:
        aliases = set(self._tidb_forbidden_aliases_default)
        raw = os.getenv("NL2SQL_TIDB_FORBIDDEN_ALIASES", "").strip()
        if not raw:
            return aliases
        for token in raw.split(","):
            t = token.strip().strip("`").strip('"').lower()
            if t and re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", t):
                aliases.add(t)
        return aliases

    def _safe_alias_forbidden(self, alias: str) -> str:
        base = re.sub(r"[^a-zA-Z0-9_]+", "_", alias.lower()).strip("_") or "col"
        if base.endswith("_alias"):
            candidate = base
        else:
            candidate = f"{base}_alias"
        while candidate in self._tidb_forbidden_aliases:
            candidate = f"{candidate}_x"
        return candidate

    def _extract_aliases(self, sql: str) -> set[str]:
        aliases: set[str] = set()
        for m in re.finditer(r"(?i)\bAS\s+(`?)([a-zA-Z_][a-zA-Z0-9_]*)\1\b", sql):
            aliases.add(m.group(2).lower())
        for a in self._validator.parse_table_aliases_from_sql(sql).keys():
            aliases.add(a.lower())
        return aliases

    def _replace_identifier_outside_quotes(self, sql: str, src: str, dst: str) -> str:
        pat = re.compile(rf"\b{re.escape(src)}\b", re.IGNORECASE)
        quote: str | None = None
        allowed_positions: set[int] = set()
        i = 0
        n = len(sql)
        while i < n:
            ch = sql[i]
            if quote == "'":
                if ch == "'" and (i + 1 < n and sql[i + 1] == "'"):
                    i += 2
                    continue
                if ch == "'":
                    quote = None
                i += 1
                continue
            if quote in ('"', "`"):
                if ch == quote and (i + 1 < n and sql[i + 1] == quote):
                    i += 2
                    continue
                if ch == quote:
                    quote = None
                i += 1
                continue
            if ch in ("'", '"', "`"):
                quote = ch
                i += 1
                continue
            if quote is None:
                allowed_positions.add(i)
            i += 1

        def _repl(m: re.Match[str]) -> str:
            idx = m.start()
            if idx in allowed_positions:
                return dst
            return m.group(0)

        return pat.sub(_repl, sql)

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

    def _table_columns_map(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for t in self._schema.list_tables():
            if not t.name:
                continue
            out[t.name.lower()] = {c.name.lower() for c in t.columns if c.name}
        return out

    def _validate_sql(
        self,
        sql: str,
        *,
        question: str | None = None,
        allowed_tables: set[str],
        allowed_columns: set[str],
        enforce_column_whitelist: bool,
        table_columns: dict[str, set[str]] | None = None,
        join_whitelist: set[str] | None = None,
        entity_rules: list[EntityRule] | None = None,
    ) -> tuple[bool, str | None]:
        if not self._validator.validate(sql):
            return False, "sql safety validation failed"
        cols = allowed_columns if enforce_column_whitelist else None
        ok, reason = self._validator.validate_identifiers(
            sql,
            allowed_tables=allowed_tables or None,
            allowed_columns=cols,
        )
        if not ok:
            return ok, reason
        if table_columns:
            ok_b, reason_b = self._validator.validate_column_table_binding(sql, table_columns=table_columns)
            if not ok_b:
                return ok_b, reason_b
        if table_columns and join_whitelist:
            ok_j, reason_j = self._validate_join_whitelist(sql, table_columns, join_whitelist)
            if not ok_j:
                return ok_j, reason_j
        if question is not None and entity_rules:
            ok_e, msg = check_entity_rules(question, sql, entity_rules)
            if not ok_e:
                return False, msg or "entity rule violation"
        return True, None

    @staticmethod
    def _parse_csv_env_set(key: str) -> set[str]:
        raw = (os.getenv(key) or "").strip()
        if not raw:
            return set()
        out: set[str] = set()
        for tok in raw.split(","):
            t = tok.strip().strip("`").strip('"').lower()
            if t:
                out.add(t)
        return out

    def _resolve_table_scope(
        self,
        *,
        analysis_type: str | None,
        table_columns: dict[str, set[str]],
    ) -> set[str]:
        scoped = self._parse_csv_env_set("ANALYSIS_NL2SQL_TABLE_SCOPE_DEFAULT")
        if not scoped:
            return set()
        existing = set(table_columns.keys())
        hit = {t for t in scoped if t in existing}
        logger.info(
            "NL2SQLChain table_scope analysis_type=%s configured=%d matched=%d",
            (analysis_type or "-"),
            len(scoped),
            len(hit),
        )
        return hit

    @staticmethod
    def _join_pair_key(left_tbl: str, left_col: str, right_tbl: str, right_col: str) -> str:
        a = f"{left_tbl.lower()}.{left_col.lower()}"
        b = f"{right_tbl.lower()}.{right_col.lower()}"
        return f"{a}={b}" if a <= b else f"{b}={a}"

    def _parse_manual_join_whitelist(self, analysis_type: str | None) -> set[str]:
        keys: set[str] = set()
        raw = (os.getenv("ANALYSIS_NL2SQL_JOIN_WHITELIST") or "").strip()
        at = (analysis_type or "").strip().lower()
        scoped_raw = (os.getenv(f"ANALYSIS_NL2SQL_JOIN_WHITELIST_{at.upper()}") or "").strip() if at else ""
        src = ";".join([x for x in (raw, scoped_raw) if x])
        if not src:
            return keys
        pat = re.compile(
            r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)\s*$"
        )
        for seg in src.split(";"):
            s = seg.strip()
            if not s:
                continue
            m = pat.match(s)
            if not m:
                continue
            keys.add(self._join_pair_key(m.group(1), m.group(2), m.group(3), m.group(4)))
        return keys

    def _build_join_whitelist(
        self,
        table_columns: dict[str, set[str]],
        *,
        analysis_type: str | None,
    ) -> set[str]:
        allow_tables = set(table_columns.keys())
        out: set[str] = set()
        for t in self._schema.list_tables():
            tname = (t.name or "").lower()
            if not tname or (allow_tables and tname not in allow_tables):
                continue
            for lcol, rtab, rcol in (t.foreign_keys or []):
                lt = tname
                lc = (lcol or "").lower()
                rt = (rtab or "").lower()
                rc = (rcol or "").lower()
                if not (lt and lc and rt and rc):
                    continue
                if allow_tables and (rt not in allow_tables):
                    continue
                if lc not in table_columns.get(lt, set()) or rc not in table_columns.get(rt, set()):
                    continue
                out.add(self._join_pair_key(lt, lc, rt, rc))
        out |= self._parse_manual_join_whitelist(analysis_type)
        return out

    def _validate_join_whitelist(
        self,
        sql: str,
        table_columns: dict[str, set[str]],
        join_whitelist: set[str],
    ) -> tuple[bool, str | None]:
        alias_map = self._validator.parse_table_aliases_from_sql(sql)
        eq_pat = re.compile(
            r"\b([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)\b",
            re.IGNORECASE,
        )

        def _to_tbl(token: str) -> str | None:
            tk = token.lower()
            if tk in alias_map:
                return alias_map[tk]
            if tk in table_columns:
                return tk
            return None

        bad: list[str] = []
        for m in eq_pat.finditer(sql):
            lt = _to_tbl(m.group(1))
            lc = m.group(2).lower()
            rt = _to_tbl(m.group(3))
            rc = m.group(4).lower()
            if not lt or not rt or lt == rt:
                continue
            if lc not in table_columns.get(lt, set()) or rc not in table_columns.get(rt, set()):
                continue
            key = self._join_pair_key(lt, lc, rt, rc)
            if key not in join_whitelist:
                bad.append(key)
        if bad:
            return False, "join key not in whitelist: " + "; ".join(bad[:4])
        return True, None

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

