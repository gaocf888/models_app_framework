"""智能客服 NL2SQL 分支：查数后 Markdown 收紧分析（可选 LLM）与友好容错。"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, List
from uuid import UUID
import inspect

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.llm.graphs.chatbot_nl2sql_display import (
    CHATBOT_NL2SQL_SELECT_DISPLAY_RULES,
    filter_chatbot_nl2sql_display_rows,
)
from app.llm.prompt_registry import PromptTemplateRegistry
from app.models.nl2sql import NL2SQLQueryRequest
from app.nl2sql.errors import NL2SQLExecutionError
from app.services.nl2sql_service import NL2SQLService

logger = get_logger(__name__)

_MAX_ROWS_IN_PROMPT_FALLBACK = 80
# 注入分析 LLM 的行数上限（远小于展示上限，避免超窗 400 / 读超时）
_ANALYSIS_PROMPT_MAX_ROWS_DEFAULT = 12
# 用户可见 Markdown 表回退时的行数上限（明细过多时避免刷屏）
_UI_TABLE_MAX_ROWS = 25
_META_SAMPLE_ROWS = 30
_ANALYSIS_PROMPT_MAX_CHARS = 10000
# 注入分析 LLM 的列数上限（宽表如超温明细易拖慢生成）
_ANALYSIS_PROMPT_MAX_COLS = 10
# 优先保留的列名关键字（序号越小越优先；宽表按最优命中分排序后截断）
_ANALYSIS_COL_PRIORITY_KEYS = (
    "最高",
    "壁温",
    "限值",
    "时长",
    "开始",
    "结束",
    "负荷",
    "压力",
    "测点",
    "锅炉",
    "机组",
    "排号",
    "管号",
    "超温",
    "时间",
    "设备",
    "管屏",
    "name",
    "temp",
    "time",
    "load",
)

_DEFAULT_USER_ERROR_MESSAGE = (
    "暂时无法完成本次数据查询，请尝试缩小范围（如指定锅炉、时间）或改用知识库问答。"
    "若问题持续，请联系管理员并提供提问时间。"
)

_USER_ERROR_MESSAGES: dict[str, str] = {
    "unknown_column": (
        "暂时无法完成本次数据查询（查询字段配置异常，已记录）。"
        "请尝试缩小查询范围或改用知识库问答。"
    ),
    "unknown_table": (
        "暂时无法完成本次数据查询（相关数据表未配置或不可用，已记录）。"
        "请尝试缩小查询范围或改用知识库问答。"
    ),
    "sql_syntax_error": (
        "暂时无法完成本次数据查询（查询语句未能正确生成，已记录）。"
        "请换一种方式描述要查的台账或记录条件。"
    ),
    "db_access_denied": (
        "暂时无法完成本次数据查询（数据访问权限异常，已记录）。请联系管理员。"
    ),
    "default": _DEFAULT_USER_ERROR_MESSAGE,
}

_EMPTY_ROWS_FIXED_MESSAGE = (
    "查询已执行，当前条件下没有返回数据行。\n\n"
    "若预期应有数据，请检查筛选条件或确认业务库是否已同步。"
)

_DEFAULT_ANALYSIS_SYSTEM = (
    "你是数据助手。下方查询结果是已执行完的事实源，只能基于其中字段与数值做中文 Markdown 整理与分析，"
    "禁止编造数字或库外因果；禁止输出 SQL。"
    "结构可参考：核心结论、明细表、业务洞察（示例，非强制）。"
    "禁止「注意/注意事项」独立章节与客套收尾；"
    "禁止建议用户补充字段、补充历史数据或扩大分析范围（列由系统生成，非用户手填）；只解读已给出且与问句相关的内容。"
)

_DEFAULT_EMPTY_SYSTEM = (
    "用户查询已执行但无数据行。用简洁中文说明无数据，并给出改问建议；禁止编造数值与 SQL。"
)


def _chatbot_expose_nl2sql_sql_in_meta() -> bool:
    return os.getenv("CHATBOT_EXPOSE_NL2SQL_SQL_IN_META", "false").lower() == "true"


def format_nl2sql_user_error(exc: NL2SQLExecutionError | None = None) -> str:
    """将 NL2SQL 执行失败映射为客服用户可见文案（无 SQL、无堆栈）。"""
    if exc is None:
        return _DEFAULT_USER_ERROR_MESSAGE
    key = exc.user_message_key if exc.user_message_key in _USER_ERROR_MESSAGES else "default"
    return _USER_ERROR_MESSAGES.get(key, _DEFAULT_USER_ERROR_MESSAGE)


@dataclass
class Nl2sqlAnalysisStreamPlan:
    """查数成功且需 LLM 收紧分析时，供 SSE 流式生成；失败则回退 table_fallback。"""

    system: str
    user_content: str
    table_fallback: str
    display_rows: list[dict[str, Any]] = field(default_factory=list)
    total_row_count: int = 0
    sql: str = ""
    user_query: str = ""

    def to_state_dict(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "user_content": self.user_content,
            "table_fallback": self.table_fallback,
            "display_rows": json_safe_rows(list(self.display_rows)),
            "total_row_count": int(self.total_row_count),
            "sql": self.sql or "",
            "user_query": self.user_query or "",
        }

    @classmethod
    def from_state_dict(cls, raw: dict[str, Any] | None) -> Nl2sqlAnalysisStreamPlan | None:
        if not isinstance(raw, dict):
            return None
        system = str(raw.get("system") or "").strip()
        user_content = str(raw.get("user_content") or "").strip()
        table_fallback = str(raw.get("table_fallback") or "").strip()
        if not system or not user_content or not table_fallback:
            return None
        rows_raw = raw.get("display_rows")
        display_rows = [r for r in rows_raw if isinstance(r, dict)] if isinstance(rows_raw, list) else []
        return cls(
            system=system,
            user_content=user_content,
            table_fallback=table_fallback,
            display_rows=display_rows,
            total_row_count=int(raw.get("total_row_count") or len(display_rows)),
            sql=str(raw.get("sql") or ""),
            user_query=str(raw.get("user_query") or ""),
        )


@dataclass
class ChatbotNL2SQLOutcome:
    answer_text: str
    nl2sql_sql: str | None = None
    nl2sql_failed: bool = False
    nl2sql_error_code: str | None = None
    gen_failed: bool = False
    gen_fail_reason: str | None = None
    terminate_reason: str | None = None
    # Phase 3：旁路结构化（列/行样本等），供 finished.meta.nl2sql_analysis
    nl2sql_analysis: dict[str, Any] | None = None
    # 非空时：SQL 已成功，分析待 SSE 流式输出（answer_text 通常为空）
    analysis_stream_plan: Nl2sqlAnalysisStreamPlan | None = None


@dataclass
class Nl2sqlSummarizeResult:
    answer_text: str
    analysis_meta: dict[str, Any] | None = None
    stream_plan: Nl2sqlAnalysisStreamPlan | None = None


def json_safe_value(value: Any) -> Any:
    """将 DB 行值转为 JSON 可序列化类型（Decimal/datetime 等）。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            try:
                return int(value)
            except Exception:
                return float(value)
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat(timespec="seconds")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(k): json_safe_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe_value(v) for v in value]
    return str(value)


def json_safe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{str(k): json_safe_value(v) for k, v in (_row_to_mapping(r).items())} for r in rows]


async def run_chatbot_nl2sql_query(
    nl2sql: NL2SQLService,
    llm_client: Any,
    *,
    user_id: str,
    session_id: str,
    question: str,
    defer_analysis_stream: bool = False,
) -> ChatbotNL2SQLOutcome:
    """智能客服 NL2SQL 统一入口：成功则整理结果，失败则友好文案且不向上抛异常。

    defer_analysis_stream=True：查数成功且需 LLM 分析时不阻塞等待全文，返回 analysis_stream_plan
    供调用方用 stream_chat 推 SSE delta（同步 chat / 关分析时仍返回完整 answer_text）。
    """
    req = NL2SQLQueryRequest(
        user_id=user_id,
        session_id=session_id,
        question=question,
        sql_gen_extra_hint=CHATBOT_NL2SQL_SELECT_DISPLAY_RULES,
    )
    try:
        resp = await nl2sql.query(req, record_conversation=False)
        if not (resp.sql or "").strip():
            fail_reason = getattr(resp, "gen_fail_reason", None) or "empty_sql"
            logger.info(
                "智能客服 NL2SQL：未生成有效 SQL（仅日志）。用户问题摘要=%s reason=%s",
                (question or "")[:400],
                fail_reason,
            )
            return ChatbotNL2SQLOutcome(
                answer_text="",
                nl2sql_sql=None,
                gen_failed=True,
                gen_fail_reason=fail_reason,
                terminate_reason="nl2sql_gen_failed",
            )
        summarized = await summarize_nl2sql_with_llm(
            llm_client,
            user_query=question,
            sql=resp.sql,
            rows=list(resp.rows or []),
            user_id=user_id,
            defer_analysis_stream=defer_analysis_stream,
        )
        return ChatbotNL2SQLOutcome(
            answer_text=summarized.answer_text,
            nl2sql_sql=resp.sql or None,
            nl2sql_analysis=summarized.analysis_meta,
            analysis_stream_plan=summarized.stream_plan,
        )
    except NL2SQLExecutionError as exc:
        logger.warning(
            "智能客服 NL2SQL 执行失败 error_code=%s question=%s detail=%s",
            exc.error_code,
            (question or "")[:400],
            exc.log_detail(),
        )
        if exc.sql:
            logger.info(
                "智能客服 NL2SQL 失败 SQL（仅日志）\n%s",
                exc.sql[:8000] + ("..." if len(exc.sql) > 8000 else ""),
            )
        sql_meta = (exc.sql or None) if _chatbot_expose_nl2sql_sql_in_meta() else None
        return ChatbotNL2SQLOutcome(
            answer_text=format_nl2sql_user_error(exc),
            nl2sql_sql=sql_meta,
            nl2sql_failed=True,
            nl2sql_error_code=exc.error_code,
            terminate_reason="nl2sql_exec_failed",
        )
    except RuntimeError as exc:
        if "SQL execution failed" not in str(exc):
            raise
        logger.warning(
            "智能客服 NL2SQL 执行失败（兼容 RuntimeError） question=%s err=%s",
            (question or "")[:400],
            str(exc)[:240],
        )
        return ChatbotNL2SQLOutcome(
            answer_text=format_nl2sql_user_error(),
            nl2sql_failed=True,
            nl2sql_error_code="sql_exec_failed",
            terminate_reason="nl2sql_exec_failed",
        )


def _row_to_mapping(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    if isinstance(row, (list, tuple)):
        return {f"列{i + 1}": v for i, v in enumerate(row)}
    return {"值": row}


def _markdown_escape_cell(val: Any) -> str:
    if val is None:
        return ""
    s = str(json_safe_value(val)).replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("|", "\\|").replace("\n", "<br>")
    return s


def _collect_column_order(dict_rows: list[dict[str, Any]]) -> list[str]:
    col_order: list[str] = []
    seen: set[str] = set()
    for dr in dict_rows:
        for k in dr.keys():
            sk = str(k)
            if sk not in seen:
                seen.add(sk)
                col_order.append(sk)
    return col_order


def _column_priority_score(col: str) -> int:
    cl = (col or "").lower()
    best = len(_ANALYSIS_COL_PRIORITY_KEYS) + 100
    for i, key in enumerate(_ANALYSIS_COL_PRIORITY_KEYS):
        if key.lower() in cl:
            best = min(best, i)
    return best


def _slim_columns_for_analysis(col_order: list[str], *, max_cols: int = _ANALYSIS_PROMPT_MAX_COLS) -> list[str]:
    """宽表注入分析 LLM 时按关键字优先级保留相关列，控制 prompt 体积与生成耗时。"""
    if len(col_order) <= max_cols:
        return list(col_order)
    ranked = sorted(
        enumerate(col_order),
        key=lambda it: (_column_priority_score(it[1]), it[0]),
    )
    picked_idx = sorted(i for i, _ in ranked[:max_cols])
    return [col_order[i] for i in picked_idx]


def _rows_to_markdown_table(
    slice_rows: List[Any],
    *,
    total_row_count: int,
    columns: list[str] | None = None,
) -> str:
    """
    将查询结果行渲染为 GFM 风格 Markdown 表格（无前言、无 SQL 块）。
    列顺序：首行字段顺序优先，后续行出现的新字段依次追加在表尾；
    若传入 columns 则仅渲染这些列。
    """
    dict_rows = json_safe_rows([_row_to_mapping(r) for r in slice_rows])
    col_order = list(columns) if columns else _collect_column_order(dict_rows)
    if not col_order:
        return "> （无可展示列）"

    header = "| " + " | ".join(_markdown_escape_cell(c) for c in col_order) + " |"
    sep = "| " + " | ".join("---" for _ in col_order) + " |"
    body: list[str] = []
    for dr in dict_rows:
        line = "| " + " | ".join(_markdown_escape_cell(dr.get(c)) for c in col_order) + " |"
        body.append(line)
    out = "\n".join([header, sep, *body])
    if total_row_count > len(slice_rows):
        out += f"\n\n> 共 {total_row_count} 行，以下展示前 {len(slice_rows)} 行。"
    return out


def _analysis_max_rows() -> int:
    try:
        return max(1, int(get_app_config().chatbot.nl2sql_analysis_max_rows))
    except Exception:
        return _MAX_ROWS_IN_PROMPT_FALLBACK


def _analysis_prompt_max_rows() -> int:
    """注入 LLM 的行数：默认更小，可用环境变量覆盖。"""
    raw = os.getenv("CHATBOT_NL2SQL_ANALYSIS_PROMPT_MAX_ROWS")
    if raw is not None and str(raw).strip():
        try:
            return max(1, min(80, int(raw)))
        except Exception:
            pass
    return min(_ANALYSIS_PROMPT_MAX_ROWS_DEFAULT, _analysis_max_rows())


def _build_analysis_meta(
    *,
    display_rows: list[dict[str, Any]],
    total_row_count: int,
    llm_analysis_used: bool,
    empty: bool = False,
) -> dict[str, Any] | None:
    try:
        cfg = get_app_config().chatbot
        if not bool(cfg.nl2sql_analysis_meta_enabled):
            return None
    except Exception:
        return None

    safe_rows = json_safe_rows(display_rows)
    columns: list[str] = []
    seen: set[str] = set()
    for dr in safe_rows:
        for k in dr.keys():
            sk = str(k)
            if sk not in seen:
                seen.add(sk)
                columns.append(sk)
    sample = safe_rows[:_META_SAMPLE_ROWS]
    return {
        "source": "nl2sql",
        "empty": bool(empty),
        "row_count": int(total_row_count),
        "displayed_row_count": len(display_rows),
        "columns": columns,
        "rows": sample,
        "llm_analysis_used": bool(llm_analysis_used),
        "format": "markdown_answer",
    }


def _load_scene_system(scene: str, *, user_id: str | None, fallback: str) -> str:
    try:
        reg = PromptTemplateRegistry()
        tpl = reg.get_template(scene=scene, user_id=user_id, version=None, default_version="v1")
        content = (tpl.content if tpl else "") or ""
        if content.strip():
            return content.strip()
    except Exception:
        logger.warning("chatbot.nl2sql_analysis load prompt scene=%s failed", scene, exc_info=True)
    return fallback


def _clip_prompt_text(text: str, *, max_chars: int = _ANALYSIS_PROMPT_MAX_CHARS) -> str:
    raw = text or ""
    if len(raw) <= max_chars:
        return raw
    return raw[: max_chars - 24] + "\n…(truncated for context)"


def _analysis_timeout_sec() -> float:
    try:
        return max(15.0, float(get_app_config().chatbot.nl2sql_analysis_timeout_sec))
    except Exception:
        return 120.0


def _analysis_llm_kwargs() -> dict[str, Any]:
    cfg = get_app_config().chatbot
    kwargs: dict[str, Any] = {
        "max_tokens": max(256, int(cfg.nl2sql_analysis_max_tokens)),
        "timeout": _analysis_timeout_sec(),
    }
    if cfg.nl2sql_analysis_temperature is not None:
        kwargs["temperature"] = float(cfg.nl2sql_analysis_temperature)
    return kwargs


async def _call_analysis_llm(
    llm_client: Any,
    *,
    system: str,
    user_content: str,
) -> str | None:
    if llm_client is None or not hasattr(llm_client, "chat"):
        return None
    try:
        text = await llm_client.chat(
            model=None,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            **_analysis_llm_kwargs(),
        )
        out = (text or "").strip()
        return out or None
    except Exception:
        logger.warning("chatbot.nl2sql_analysis LLM call failed", exc_info=True)
        return None


async def iter_analysis_llm_deltas(
    llm_client: Any,
    *,
    system: str,
    user_content: str,
) -> AsyncIterator[str]:
    """流式产出收紧分析文本；无可用 stream_chat 时退化为一次 chat 整段 yield。"""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]
    kwargs = _analysis_llm_kwargs()
    stream_fn = getattr(llm_client, "stream_chat", None) if llm_client is not None else None
    raw_fn = getattr(stream_fn, "__func__", stream_fn)
    if stream_fn is not None and inspect.isasyncgenfunction(raw_fn):
        async for delta in stream_fn(model=None, messages=messages, **kwargs):
            if isinstance(delta, str) and delta:
                yield delta
        return
    text = await _call_analysis_llm(llm_client, system=system, user_content=user_content)
    if text:
        yield text


def finalize_streamed_nl2sql_analysis(
    plan: Nl2sqlAnalysisStreamPlan,
    streamed_text: str,
) -> Nl2sqlSummarizeResult:
    """流式结束后组装正文与 meta；无有效输出则回退 Markdown 表。"""
    answer = (streamed_text or "").strip()
    llm_used = bool(answer)
    if not answer:
        answer = plan.table_fallback
        llm_used = False
        logger.info(
            "chatbot.nl2sql_analysis fallback_to_table question=%s prompt_chars=%s",
            (plan.user_query or "")[:200],
            len(plan.user_content or ""),
        )
    logger.info(
        "智能客服 NL2SQL：已向用户返回整理结果（总行数=%s，展示行数=%s，llm_analysis=%s）。用户问题摘要=%s",
        plan.total_row_count,
        len(plan.display_rows),
        llm_used,
        (plan.user_query or "")[:400],
    )
    logger.info(
        "智能客服 NL2SQL：本次查询使用的 SQL（仅日志，不写入用户可见内容）\n%s",
        (plan.sql or "")[:8000] + ("..." if len(plan.sql or "") > 8000 else ""),
    )
    meta = _build_analysis_meta(
        display_rows=list(plan.display_rows),
        total_row_count=int(plan.total_row_count),
        llm_analysis_used=llm_used,
        empty=False,
    )
    return Nl2sqlSummarizeResult(answer_text=answer, analysis_meta=meta)


def _build_analysis_user_content(
    *,
    user_query: str,
    rows_total: int,
    prompt_rows: list[dict[str, Any]],
) -> str:
    full_cols = _collect_column_order(prompt_rows)
    analysis_cols = _slim_columns_for_analysis(full_cols)
    table_for_prompt = _rows_to_markdown_table(
        prompt_rows,
        total_row_count=rows_total,
        columns=analysis_cols,
    )
    col_note = ""
    if len(analysis_cols) < len(full_cols):
        col_note = f"；分析用表已从 {len(full_cols)} 列收窄为 {len(analysis_cols)} 列关键字段"
    return _clip_prompt_text(
        f"用户问题：{user_query or ''}\n\n"
        f"结果总行数：{rows_total}；以下提供前 {len(prompt_rows)} 行作为分析事实源"
        f"{col_note}"
        f"（仅基于下列已给出行与列作答，勿臆造未给出的值；"
        f"勿建议用户补充字段、补充数据或扩大分析范围）。\n\n"
        "【查询结果 · Markdown 表（已执行完的事实源）】\n"
        f"{table_for_prompt}\n\n"
        "请基于以上已执行结果输出用户可读的 Markdown 分析；"
        "明细表请整理精简，不要原样粘贴全部行；不要输出 SQL；"
        "不要写「注意/注意事项」，不要写「未提供××请补充字段」之类内容。"
    )


async def summarize_nl2sql_with_llm(
    llm_client: Any,
    *,
    user_query: str,
    sql: str,
    rows: List[dict],
    user_id: str | None = None,
    defer_analysis_stream: bool = False,
) -> Nl2sqlSummarizeResult:
    """
    智能客服 NL2SQL 结果整理：

    - 有数据 + 分析开关 + defer_analysis_stream：返回 stream_plan，不调 LLM；
    - 有数据 + 分析开关：主模型 Markdown 收紧分析（失败则回退纯表）；
    - 有数据 + 关分析：仅 Markdown 表（旧行为）；
    - 空行：可选 LLM 引导，否则固定文案；
    - SQL 仅日志 / meta，不写入用户可见正文。
    """
    sql = (sql or "").strip()
    if not sql:
        logger.info(
            "智能客服 NL2SQL：未生成有效 SQL（仅日志）。用户问题摘要=%s",
            (user_query or "")[:400],
        )
        return Nl2sqlSummarizeResult(
            answer_text=(
                "未能生成有效的 SQL 查询。请换一种方式描述要查的台账或记录条件，或改用知识库问答。"
            )
        )

    cfg = get_app_config().chatbot
    max_rows = _analysis_max_rows()
    slice_rows = list(rows[:max_rows])

    if not slice_rows:
        logger.info(
            "智能客服 NL2SQL：查询已执行但无数据行（仅日志）。用户问题摘要=%s\n本次生成用 SQL=\n%s",
            (user_query or "")[:400],
            sql[:8000] + ("..." if len(sql) > 8000 else ""),
        )
        answer = _EMPTY_ROWS_FIXED_MESSAGE
        llm_used = False
        if bool(cfg.nl2sql_empty_llm_guide_enabled):
            system = _load_scene_system(
                "chatbot_nl2sql_empty",
                user_id=user_id,
                fallback=_DEFAULT_EMPTY_SYSTEM,
            )
            user_content = (
                f"用户问题：{user_query or ''}\n\n"
                "查询结果：空（0 行）。请按系统要求输出友好引导。"
            )
            guided = await _call_analysis_llm(llm_client, system=system, user_content=user_content)
            if guided:
                answer = guided
                llm_used = True
        meta = _build_analysis_meta(
            display_rows=[],
            total_row_count=0,
            llm_analysis_used=llm_used,
            empty=True,
        )
        return Nl2sqlSummarizeResult(answer_text=answer, analysis_meta=meta)

    display_rows = filter_chatbot_nl2sql_display_rows(
        [_row_to_mapping(r) for r in slice_rows]
    )
    ui_rows = display_rows[:_UI_TABLE_MAX_ROWS]
    table_fallback = _rows_to_markdown_table(ui_rows, total_row_count=len(rows))

    if not bool(cfg.nl2sql_llm_analysis_enabled):
        logger.info(
            "智能客服 NL2SQL：已向用户返回整理结果（总行数=%s，展示行数=%s，llm_analysis=%s）。用户问题摘要=%s",
            len(rows),
            len(display_rows),
            False,
            (user_query or "")[:400],
        )
        logger.info(
            "智能客服 NL2SQL：本次查询使用的 SQL（仅日志，不写入用户可见内容）\n%s",
            sql[:8000] + ("..." if len(sql) > 8000 else ""),
        )
        meta = _build_analysis_meta(
            display_rows=display_rows,
            total_row_count=len(rows),
            llm_analysis_used=False,
            empty=False,
        )
        return Nl2sqlSummarizeResult(answer_text=table_fallback, analysis_meta=meta)

    system = _load_scene_system(
        "chatbot_nl2sql_analysis",
        user_id=user_id,
        fallback=_DEFAULT_ANALYSIS_SYSTEM,
    )
    prompt_n = _analysis_prompt_max_rows()
    prompt_rows = display_rows[:prompt_n]
    user_content = _build_analysis_user_content(
        user_query=user_query or "",
        rows_total=len(rows),
        prompt_rows=prompt_rows,
    )
    plan = Nl2sqlAnalysisStreamPlan(
        system=system,
        user_content=user_content,
        table_fallback=table_fallback,
        display_rows=display_rows,
        total_row_count=len(rows),
        sql=sql,
        user_query=user_query or "",
    )
    if defer_analysis_stream:
        return Nl2sqlSummarizeResult(answer_text="", stream_plan=plan)

    parts: list[str] = []
    try:
        async for delta in iter_analysis_llm_deltas(
            llm_client, system=system, user_content=user_content
        ):
            parts.append(delta)
    except Exception:
        logger.warning("chatbot.nl2sql_analysis LLM stream failed", exc_info=True)
        parts = []
    return finalize_streamed_nl2sql_analysis(plan, "".join(parts))
