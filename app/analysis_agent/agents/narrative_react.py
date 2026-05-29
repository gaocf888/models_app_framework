from __future__ import annotations

from typing import Any

from app.analysis_agent.agents.section_prompt import build_section_user_prompt
from app.analysis_agent.agents.section_result import SectionSynthesisResult
from app.analysis_agent.renderers import section_data as core
from app.analysis_agent.slots.kinds import AnalysisAgentSlot
from app.analysis_agent.tools.agent_tools import build_narrative_tools
from app.analysis_agent.tools.slot_context import reset_slot_tool_context, set_slot_tool_context
from app.core.config import get_app_config
from app.core.logging import get_logger
from app.analysis_agent.plans.loader import get_synthesis_template
from app.llm.prompt_registry import PromptTemplateRegistry

logger = get_logger(__name__)


def _build_chat_model() -> Any | None:
    try:
        from langchain_openai import ChatOpenAI  # type: ignore[import-not-found]
    except ImportError:
        return None
    cfg = get_app_config()
    model_cfg = cfg.llm.models[cfg.llm.default_model]
    return ChatOpenAI(
        model=model_cfg.model_id,
        base_url=model_cfg.endpoint.rstrip("/"),
        api_key=model_cfg.api_key or "EMPTY",
        temperature=model_cfg.temperature,
        max_tokens=cfg.analysis_agent.narrative_max_tokens,
    )


def _create_react_executor(system: str, tools: list[Any]) -> Any | None:
    """尝试 langgraph.prebuilt / langchain create_react_agent。"""
    llm = _build_chat_model()
    if llm is None or not tools:
        return None
    try:
        from langgraph.prebuilt import create_react_agent  # type: ignore[import-not-found]

        try:
            return create_react_agent(llm, tools, prompt=system)
        except TypeError:
            agent = create_react_agent(llm, tools)
            agent._analysis_agent_system = system  # type: ignore[attr-defined]
            return agent
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("langgraph create_react_agent failed: %s", exc)
    try:
        from langchain.agents import AgentExecutor, create_react_agent  # type: ignore[import-not-found]
        from langchain_core.prompts import PromptTemplate  # type: ignore[import-not-found]

        template = (
            system
            + "\n\n{tools}\n\nUse the following format:\n\n"
            "Question: the input question\nThought: consider tools\n"
            "Action: tool name\nAction Input: input\n"
            "... (repeat)\nFinal Answer: chapter body in Chinese markdown paragraphs only\n\n"
            "Question: {input}\nThought:{agent_scratchpad}"
        )
        prompt = PromptTemplate.from_template(template)
        agent = create_react_agent(llm, tools, prompt)
        return AgentExecutor(
            agent=agent,
            tools=tools,
            verbose=False,
            max_iterations=get_app_config().analysis_agent.react_max_iterations,
            handle_parsing_errors=True,
        )
    except ImportError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("langchain create_react_agent failed: %s", exc)
        return None


def _merge_artifacts(ctx: dict[str, Any], body: str) -> SectionSynthesisResult:
    art = ctx.get("section_artifacts") or {}
    tables = list(art.get("tables") or [])
    charts = list(art.get("charts") or [])
    table_mds = list(art.get("table_markdowns") or [])
    return SectionSynthesisResult(
        markdown=body.strip() or "（待补充）",
        tables=tables,
        charts=charts,
        table_markdowns=table_mds,
    )


async def run_narrative_react(
    *,
    prompts: PromptTemplateRegistry,
    slot: AnalysisAgentSlot,
    query: str,
    gathered_data: dict[str, list[dict]],
    context_snippets: list[str],
    task_status: dict[str, str] | None,
    hybrid_rag: Any,
    analysis_type: str,
    user_prompt_override: str | None = None,
    include_emit_tools: bool | None = None,
) -> SectionSynthesisResult:
    subset = core.resolve_data_subset(
        gathered_data, slot.source_item_ids, strict=True
    )
    system_tpl, _scene = get_synthesis_template(analysis_type, prompts=prompts)
    default_system = "你是电站锅炉综合分析报告撰写专家。"
    if analysis_type == "overheat_guidance":
        default_system = "你是电站锅炉超温分析报告撰写专家。"
    system = (system_tpl.content if system_tpl else "").strip() or default_system
    if include_emit_tools is None:
        include_emit_tools = bool(slot.use_emit_tools or slot.kind == "llm_section")
    if user_prompt_override:
        user_prompt = user_prompt_override
    else:
        facts = core.build_audit_facts(subset, query, slot_id=slot.id, task_status=task_status)
        coverage = core.build_data_coverage_note(subset, task_status=task_status)
        user_prompt = build_section_user_prompt(
            slot=slot,
            query=query,
            coverage=coverage,
            facts=facts,
        )

    tools = build_narrative_tools(include_emit=include_emit_tools)
    executor = _create_react_executor(system, tools)
    if executor is None:
        raise RuntimeError("react_agent_unavailable")

    cfg = get_app_config().analysis_agent
    ctx: dict[str, Any] = {
        "gathered_data": gathered_data,
        "source_item_ids": slot.source_item_ids,
        "gathered_json_max_chars": cfg.gathered_json_max_chars,
        "hybrid_rag": hybrid_rag,
        "analysis_type": analysis_type,
        "rag_top_k": min(4, cfg.rag_top_k),
        "slot_id": slot.id,
        "section_artifacts": {"tables": [], "charts": [], "table_markdowns": []},
    }
    token = set_slot_tool_context(ctx)
    try:
        if hasattr(executor, "ainvoke"):
            sys_prefix = getattr(executor, "_analysis_agent_system", None) or system
            result = await executor.ainvoke(
                {"messages": [("system", sys_prefix), ("user", user_prompt)]}
            )
            if isinstance(result, dict):
                messages = result.get("messages")
                if messages:
                    last = messages[-1]
                    content = getattr(last, "content", None) or last.get("content")
                    if content:
                        return _merge_artifacts(ctx, str(content))
                out = str(result.get("output") or result.get("answer") or "")
                return _merge_artifacts(ctx, out)
            return _merge_artifacts(ctx, str(result))
        result = executor.invoke({"input": user_prompt})
        if isinstance(result, dict):
            return _merge_artifacts(ctx, str(result.get("output") or ""))
        return _merge_artifacts(ctx, str(result))
    finally:
        reset_slot_tool_context(token)
