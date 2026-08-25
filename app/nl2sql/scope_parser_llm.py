from __future__ import annotations

import asyncio
import concurrent.futures
import re
from typing import Any

from pydantic import BaseModel, ValidationError, field_validator

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.llm.client import VLLMHttpClient
from app.llm.prompt_registry import PromptTemplateRegistry
from app.models.analysis_nl2sql_llm import extract_json_object_from_llm_text
from app.nl2sql.chain import NL2SQLChain
from app.nl2sql.intent_config import (
    scope_parse_llm_max_tokens,
    scope_parse_llm_temperature,
    scope_parse_llm_timeout_seconds,
    scope_parse_log_rule_llm_diff,
    scope_parse_prompt_version,
)
from app.nl2sql.question_scope_models import QuestionScopeIntent
from app.nl2sql.scope_lexicon import ScopeLexicon, get_scope_lexicon
from app.nl2sql.scope_parser_rule import (
    _has_explicit_row_no,
    _is_wall_device,
    expand_abbreviations,
    parse_scope_rule,
)

logger = get_logger(__name__)

_SCENE = "nl2sql_scope_parse"
_DEFAULT_PROMPT_VERSION = "v1"

_DEFAULT_PROMPT = """\
你是锅炉领域问句「实体范围」解析器。根据用户问题，仅输出一个 JSON 对象，不要 markdown 代码块，不要解释。

【重要约束】
- 时间语义（近一周、昨天、本月等）由程序侧单独处理：不要因时间词填充 scope；无实体范围时各字段为 null。
- boiler（锅炉/机组）由程序规则解析并覆盖本 JSON 中的 boiler；可省略 boiler 或填 null。

【范围解析规则】
1. 分级解析：问句只提到哪一层，就只填那一层；未出现的字段必须为 null。
2. 机组/锅炉：「N号锅炉」「N号机组」「N号炉」「N#机组」「#N机组」等统一为「N号锅炉」；显式全厂时 boiler 为 null。
3. 受热面简称须展开：低过→低温过热器，高过→高温过热器，高再→高温再热器，低再→低温再热器，屏过→屏式过热器；分隔屏过热器→屏式过热器。
4. 设备/受热面：提取完整受热面短语（如水冷壁前墙垂直段、屏式过热器、省煤器）。
5. 管排名称：第一层、炉前向炉后数、第一屏、前屏→第一屏、后屏→第二屏、第一层炉右向炉左数等。
6. 排数：「第一排」「第1排」「第一行」→ row_no 为阿拉伯数字；水冷壁/包墙/后竖井/冷灰斗且未写排数时 row_no=1。
7. 管数：「第一根」「第1根」→ tube_no 为阿拉伯数字；未写则为 null。

【输出 schema】
{"boiler":string|null,"device_name":string|null,"piperow_name":string|null,"row_no":number|null,"tube_no":number|null}

【用户问题】
{{QUESTION}}
"""


class ScopeParseLLMError(Exception):
    """LLM 范围解析失败（可触发 rule fallback）。"""


class ScopeParseLLMOutput(BaseModel):
    boiler: str | None = None
    device_name: str | None = None
    piperow_name: str | None = None
    row_no: int | None = None
    tube_no: int | None = None

    @staticmethod
    def _empty_to_none(v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            if not s or s.lower() in {"null", "none", "nil"}:
                return None
            return s
        return v

    @field_validator("boiler", "device_name", "piperow_name", mode="before")
    @classmethod
    def _norm_str_fields(cls, v: Any) -> str | None:
        out = cls._empty_to_none(v)
        return str(out).strip() if out is not None else None

    @field_validator("row_no", "tube_no", mode="before")
    @classmethod
    def _norm_int_fields(cls, v: Any) -> int | None:
        v = cls._empty_to_none(v)
        if v is None:
            return None
        if isinstance(v, bool):
            return None
        if isinstance(v, int):
            return v if v > 0 else None
        if isinstance(v, float):
            n = int(v)
            return n if n > 0 else None
        if isinstance(v, str):
            s = v.strip()
            if s.isdigit():
                n = int(s)
                return n if n > 0 else None
            n = NL2SQLChain._cn_unit_index_to_int(s)
            return n if n is not None and n > 0 else None
        return None


def _scope_intent_diff_fields(
    rule_scope: QuestionScopeIntent,
    llm_scope: QuestionScopeIntent,
) -> dict[str, tuple[object, object]]:
    diffs: dict[str, tuple[object, object]] = {}
    for field in ("device_name", "piperow_name", "row_no", "tube_no"):
        rule_val = getattr(rule_scope, field)
        llm_val = getattr(llm_scope, field)
        if rule_val != llm_val:
            diffs[field] = (rule_val, llm_val)
    return diffs


def _log_scope_rule_llm_diff(
    *,
    scope_question: str,
    rule_scope: QuestionScopeIntent,
    llm_scope: QuestionScopeIntent,
) -> None:
    if not scope_parse_log_rule_llm_diff():
        return
    diffs = _scope_intent_diff_fields(rule_scope, llm_scope)
    if not diffs:
        return
    logger.info(
        "NL2SQL scope rule vs LLM diff fields=%s question=%r",
        diffs,
        (scope_question or "")[:200],
    )


def build_scope_parse_prompt(
    scope_question: str,
    *,
    prompt_registry: PromptTemplateRegistry | None = None,
) -> str:
    registry = prompt_registry or PromptTemplateRegistry()
    version = scope_parse_prompt_version()
    tpl = registry.get_template(_SCENE, version=version)
    content = (tpl.content if tpl and tpl.content else _DEFAULT_PROMPT).strip()
    return content.replace("{{QUESTION}}", scope_question.strip())


def _normalize_device_name(value: str | None, lexicon: ScopeLexicon) -> str | None:
    if not value:
        return None
    expanded = expand_abbreviations(value.strip(), lexicon.abbreviations)
    return lexicon.device_canonical.get(expanded, expanded)


def _normalize_piperow_name(value: str | None, lexicon: ScopeLexicon) -> str | None:
    if not value:
        return None
    collapsed = re.sub(r"\s+", "", value.strip())
    return lexicon.piperow_aliases.get(collapsed, collapsed)


def finalize_llm_scope(
    parsed: ScopeParseLLMOutput,
    *,
    scope_question: str,
    rule_scope: QuestionScopeIntent,
    lexicon: ScopeLexicon | None = None,
) -> QuestionScopeIntent:
    """LLM 输出后处理：锅炉始终用规则解析；其余字段归一化并补水冷壁默认排数。"""
    lex = lexicon or get_scope_lexicon()
    device_name = _normalize_device_name(parsed.device_name, lex)
    piperow_name = _normalize_piperow_name(parsed.piperow_name, lex)
    row_no = parsed.row_no
    tube_no = parsed.tube_no

    if _is_wall_device(device_name, lex) and row_no is None and not _has_explicit_row_no(scope_question):
        row_no = 1

    return QuestionScopeIntent(
        boiler=rule_scope.boiler,
        device_name=device_name,
        piperow_name=piperow_name,
        row_no=row_no,
        tube_no=tube_no,
    )


def parse_llm_scope_output(raw_text: str) -> ScopeParseLLMOutput:
    obj = extract_json_object_from_llm_text(raw_text)
    if obj is None:
        raise ScopeParseLLMError("LLM response is not valid JSON object")
    try:
        return ScopeParseLLMOutput.model_validate(obj)
    except ValidationError as exc:
        raise ScopeParseLLMError(f"LLM JSON validation failed: {exc}") from exc


async def parse_scope_llm_async(
    scope_question: str,
    *,
    llm_client: VLLMHttpClient | None = None,
    prompt_registry: PromptTemplateRegistry | None = None,
    lexicon: ScopeLexicon | None = None,
) -> QuestionScopeIntent:
    q = (scope_question or "").strip()
    if not q:
        return QuestionScopeIntent()

    rule_scope = parse_scope_rule(q, lexicon=lexicon)
    prompt = build_scope_parse_prompt(q, prompt_registry=prompt_registry)
    client = llm_client or VLLMHttpClient()
    model = get_app_config().llm.default_model
    timeout = scope_parse_llm_timeout_seconds()
    temperature = scope_parse_llm_temperature()

    logger.debug(
        "NL2SQL scope LLM parse start model=%s question_len=%d timeout_s=%.1f",
        model,
        len(q),
        timeout,
    )
    try:
        raw = await client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=scope_parse_llm_max_tokens(),
            temperature=temperature,
            timeout=timeout,
        )
    except Exception as exc:
        raise ScopeParseLLMError(f"LLM call failed: {exc}") from exc

    parsed = parse_llm_scope_output(raw)
    scope = finalize_llm_scope(
        parsed,
        scope_question=q,
        rule_scope=rule_scope,
        lexicon=lexicon,
    )
    logger.debug(
        "NL2SQL scope LLM parse ok boiler=%s device=%s piperow=%s row=%s tube=%s",
        scope.boiler,
        scope.device_name,
        scope.piperow_name,
        scope.row_no,
        scope.tube_no,
    )
    return scope


def parse_scope_llm_sync(
    scope_question: str,
    *,
    llm_client: VLLMHttpClient | None = None,
    prompt_registry: PromptTemplateRegistry | None = None,
    lexicon: ScopeLexicon | None = None,
) -> QuestionScopeIntent:
    """同步封装：供 NL2SQLChain 等同步路径调用。"""
    timeout = scope_parse_llm_timeout_seconds() + 2.0
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            asyncio.run,
            parse_scope_llm_async(
                scope_question,
                llm_client=llm_client,
                prompt_registry=prompt_registry,
                lexicon=lexicon,
            ),
        )
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            raise ScopeParseLLMError("LLM scope parse timed out") from exc


def resolve_scope_with_mode(
    scope_question: str,
    *,
    mode: str,
    llm_client: VLLMHttpClient | None = None,
    prompt_registry: PromptTemplateRegistry | None = None,
    lexicon: ScopeLexicon | None = None,
) -> tuple[QuestionScopeIntent, str]:
    """
    按解析模式返回 scope 与 effective parse_mode 标签。

    - rule：仅程序规则；
    - llm / rule_with_llm_fallback：优先 LLM，失败回退 rule。
    """
    mode_norm = (mode or "rule").strip().lower()

    from app.nl2sql.nl2sql_business_profile import get_business_domain

    if get_business_domain() == "subsidence":
        from app.nl2sql.scope_parser_subsidence import parse_scope_subsidence

        return parse_scope_subsidence(scope_question), "rule"

    rule_scope = parse_scope_rule(scope_question, lexicon=lexicon)

    if mode_norm not in ("llm", "rule_with_llm_fallback"):
        return rule_scope, "rule"

    try:
        llm_scope = parse_scope_llm_sync(
            scope_question,
            llm_client=llm_client,
            prompt_registry=prompt_registry,
            lexicon=lexicon,
        )
    except ScopeParseLLMError as exc:
        logger.warning(
            "NL2SQL scope LLM parse fallback to rule mode=%s err=%s question=%r",
            mode_norm,
            exc,
            (scope_question or "")[:200],
        )
        return rule_scope, "llm_fallback_rule"

    _log_scope_rule_llm_diff(
        scope_question=scope_question,
        rule_scope=rule_scope,
        llm_scope=llm_scope,
    )

    if mode_norm == "llm":
        return llm_scope, "llm"
    return llm_scope, "llm"
