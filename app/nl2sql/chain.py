from __future__ import annotations

from app.core.logging import get_logger
from app.llm.client import VLLMHttpClient
from app.llm.prompt_registry import PromptTemplateRegistry
from app.llm.langsmith_tracker import LangSmithTracker
from app.nl2sql.prompt_builder import PromptBuilder
from app.nl2sql.rag_service import NL2SQLRAGService
from app.nl2sql.schema_service import SchemaMetadataService
from app.nl2sql.validator import SQLValidator

logger = get_logger(__name__)


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
        # Step 1: 问题理解与规划（若 LangChain 可用）
        plan_summary: str | None = None
        if self._lc_chat_model is not None:
            try:
                plan_summary = await self._plan(question=question)
            except Exception:
                logger.exception("NL2SQLChain: planning step failed, fallback to simple flow.")
                plan_summary = None

        # Step 2: 基于规划结果从 NL2SQL 专用 RAG 检索 Schema/业务知识/样例 Q&A 片段
        rag_query = question
        if plan_summary:
            rag_query = f"【NL2SQL 规划】{plan_summary}\n【用户问题】{question}"
        # 优先使用结构化 chunk，再渲染为 prompt 文本（保留来源线索）
        schema_snippets = self._rag.retrieve(rag_query)

        # NL2SQL 专用 Prompt 前缀（scene=nl2sql）
        tpl = self._prompts.get_template(scene="nl2sql", user_id=user_id, version=None)
        system_prefix = tpl.content if tpl else None

        prompt = self._prompt_builder.build(question, schema_snippets, system_prefix=system_prefix)

        if self._lc_chat_model is not None:
            sql = await self._generate_via_langchain(prompt)
        else:
            sql = await self._llm.generate(model=None, prompt=prompt)  # type: ignore[arg-type]

        if not self._validator.validate(sql):
            logger.warning("generated SQL did not pass validation, question=%s, sql=%s", question, sql)
            # 可选 Step 4: 在 LangChain 可用时尝试自检与修正
            if self._lc_chat_model is not None:
                try:
                    sql = await self._refine_sql(
                        question=question,
                        original_sql=sql,
                    )
                    if not self._validator.validate(sql):
                        logger.warning(
                            "refined SQL still did not pass validation, question=%s, sql=%s",
                            question,
                            sql,
                        )
                        return ""
                except Exception:
                    logger.exception("NL2SQLChain: refine_sql failed, return empty SQL.")
                    return ""
            else:
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
            "1) 可能涉及的业务实体或表名（仅作为建议，不要求与真实数据库完全一致）；"
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

    async def _refine_sql(self, question: str, original_sql: str) -> str:
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
        )
        messages: list[object] = [
            SystemMessage(content=system),
            HumanMessage(
                content=(
                    f"用户问题: {question}\n"
                    f"初稿 SQL: {original_sql}\n"
                    "请在保证语义合理的前提下，输出一条安全的仅 SELECT 语句。"
                )
            ),
        ]
        resp = await self._lc_chat_model.ainvoke(messages)  # type: ignore[union-attr]
        content = resp.content if hasattr(resp, "content") else str(resp)
        return content.strip()

    async def _generate_via_langchain(self, prompt: str) -> str:
        from langchain_core.messages import HumanMessage  # type: ignore[import-not-found]

        resp = await self._lc_chat_model.ainvoke([HumanMessage(content=prompt)])  # type: ignore[union-attr]
        content = resp.content if hasattr(resp, "content") else str(resp)
        return content.strip()

