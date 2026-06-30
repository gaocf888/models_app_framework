"""看图诊断 scope LLM 解析（scene=img_diag_scope_parse）。"""

from __future__ import annotations

import asyncio
import concurrent.futures
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
    scope_parse_prompt_version,
)
from app.nl2sql.scope_lexicon import ScopeLexicon, get_scope_lexicon
from app.nl2sql.scope_parser_llm import ScopeParseLLMError
from app.nl2sql.scope_parser_rule import (
    _has_explicit_row_no,
    _is_wall_device,
    expand_abbreviations,
    parse_scope_rule,
)

logger = get_logger(__name__)

_SCENE = "img_diag_scope_parse"
_DEFAULT_PROMPT = """\
你是看图诊断（缺陷识别/泄爆分析）问句「实体范围」解析器。根据用户问题，仅输出一个 JSON 对象，不要 markdown，不要解释。

【人机协同 / 多段输入 — 最高优先级】
- 用户问题可能由多段组成：首段为初始问句，其后每段为用户在台账确认环节追加的补充或修正（程序以换行拼接）。
- 须先读完全部段落再输出 JSON；同一字段若前后不一致，以最后一段（最靠近文末）为准。
- 用户补充多为口语，须理解语义，不要机械拼接数字。
- 若用户明确要求 **不解析/去除/不要** 某些可选字段（检测位置、排数、管数），对应 JSON 字段 **必须为 null**，即使问句正文中仍出现相关数字或描述；不得再填默认值（如水冷壁 row_no=1）。

【用户声明排除字段 — 最高优先级】
- 用户在人机协同补充中说「去除排数和管数」「不要检测位置」「仅核机组和受热面」等，表示 **禁止解析** 对应可选字段。
- 被排除字段在 JSON 中 **一律为 null**；不得从问句正文回填，也不得应用水冷壁默认 row_no=1。
- 「仅/只保留机组和受热面」→ check_location_name、row_no、tube_no 均为 null。

【重要约束】
- 时间语义由程序侧处理；不要因时间词填充 scope。
- boiler 由程序规则覆盖；JSON 中可省略 boiler 或填 null。

【检测位置与受热面】
- 检测位置（check_location_name）对应业务表 overhaul_new_checklocation.name。
- 用户问句中的区域/管段描述，可能**与受热面名称相同或高度相似**（如「水冷壁右墙 A2」「低温过热器出口段」）；
  须结合完整问句判断：若短语更像检修测厚/检测点位，填入 check_location_name；若为标准受热面台账名，填入 device_name。
- 同一短语**不要同时**填入 device_name 与 check_location_name，优先拆分为：受热面 + 更细的检测位置（若有）。
- 问句仅提到受热面、未提到更细点位时，check_location_name 为 null。

【检测位置与管号分离 — 禁止拼接】
- check_location_name 与 tube_no 是不同字段；禁止将管号拼入检测位置名（如「吹灰孔33第56根管」→ check_location_name=吹灰孔33，tube_no=56，禁止写成吹灰孔336）。

【范围解析规则】
1. 分级解析：问句只提到哪一层就只填那一层；未出现字段必须为 null。
2. 受热面简称展开：低过→低温过热器，高过→高温过热器，高再→高温再热器，低再→低温再热器，屏过→屏式过热器。
3. 排数：「第一排」「第1排」「第一行」→ row_no；未写则为 null；水冷壁/包墙/后竖井/冷灰斗且未写排数时 row_no=1。
4. 管数：「第一根」「第1根」「第N根管」→ tube_no；未写则为 null。

【输出 schema】
{"boiler":string|null,"device_name":string|null,"check_location_name":string|null,"row_no":number|null,"tube_no":number|null}

【用户问题】
{{QUESTION}}
"""


class ImgDiagScopeParseLLMOutput(BaseModel):
    boiler: str | None = None
    device_name: str | None = None
    check_location_name: str | None = None
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

    @field_validator("boiler", "device_name", "check_location_name", mode="before")
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


def build_img_diag_scope_parse_prompt(
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


def finalize_img_diag_llm_scope(
    parsed: ImgDiagScopeParseLLMOutput,
    *,
    scope_question: str,
    lexicon: ScopeLexicon | None = None,
    excluded_fields: frozenset[str] | set[str] | None = None,
) -> dict[str, Any]:
    lex = lexicon or get_scope_lexicon()
    ex = frozenset(excluded_fields or ())
    device_name = _normalize_device_name(parsed.device_name, lex)
    check_location = (parsed.check_location_name or "").strip() or None
    row_no = parsed.row_no
    tube_no = parsed.tube_no

    if check_location and device_name and check_location == device_name:
        check_location = None

    if (
        "row_no" not in ex
        and _is_wall_device(device_name, lex)
        and row_no is None
        and not _has_explicit_row_no(scope_question)
    ):
        row_no = 1

    if "check_location_name" in ex:
        check_location = None
    if "row_no" in ex:
        row_no = None
    if "tube_no" in ex:
        tube_no = None

    return {
        "device_name": device_name,
        "check_location_name": check_location,
        "row_no": row_no,
        "tube_no": tube_no,
    }


def parse_img_diag_scope_llm_output(raw_text: str) -> ImgDiagScopeParseLLMOutput:
    obj = extract_json_object_from_llm_text(raw_text)
    if obj is None:
        raise ScopeParseLLMError("LLM response is not valid JSON object")
    if isinstance(obj, dict) and "piperow_name" in obj and "check_location_name" not in obj:
        legacy = obj.get("piperow_name")
        if legacy:
            obj = dict(obj)
            obj["check_location_name"] = legacy
    try:
        return ImgDiagScopeParseLLMOutput.model_validate(obj)
    except ValidationError as exc:
        raise ScopeParseLLMError(f"LLM JSON validation failed: {exc}") from exc


async def parse_img_diag_scope_llm_async(
    scope_question: str,
    *,
    llm_client: VLLMHttpClient | None = None,
    prompt_registry: PromptTemplateRegistry | None = None,
    lexicon: ScopeLexicon | None = None,
    excluded_fields: frozenset[str] | set[str] | None = None,
) -> dict[str, Any]:
    q = (scope_question or "").strip()
    if not q:
        return {"device_name": None, "check_location_name": None, "row_no": None, "tube_no": None}

    prompt = build_img_diag_scope_parse_prompt(q, prompt_registry=prompt_registry)
    client = llm_client or VLLMHttpClient()
    model = get_app_config().llm.default_model
    timeout = scope_parse_llm_timeout_seconds()
    temperature = scope_parse_llm_temperature()

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

    parsed = parse_img_diag_scope_llm_output(raw)
    return finalize_img_diag_llm_scope(
        parsed,
        scope_question=q,
        lexicon=lexicon,
        excluded_fields=excluded_fields,
    )


def parse_img_diag_scope_llm_sync(
    scope_question: str,
    *,
    llm_client: VLLMHttpClient | None = None,
    prompt_registry: PromptTemplateRegistry | None = None,
    lexicon: ScopeLexicon | None = None,
    excluded_fields: frozenset[str] | set[str] | None = None,
) -> dict[str, Any]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            parse_img_diag_scope_llm_async(
                scope_question,
                llm_client=llm_client,
                prompt_registry=prompt_registry,
                lexicon=lexicon,
                excluded_fields=excluded_fields,
            )
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            asyncio.run,
            parse_img_diag_scope_llm_async(
                scope_question,
                llm_client=llm_client,
                prompt_registry=prompt_registry,
                lexicon=lexicon,
                excluded_fields=excluded_fields,
            ),
        )
        return future.result()
