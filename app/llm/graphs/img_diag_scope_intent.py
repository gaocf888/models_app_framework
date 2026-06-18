"""看图诊断 scope 结构化解析：LLM + 规则、置信度、scope_intent_text 合成。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.nl2sql.chain import NL2SQLChain
from app.nl2sql.question_scope_models import QuestionScopeIntent
from app.nl2sql.scope_lexicon import get_scope_lexicon
from app.nl2sql.scope_parser_llm import ScopeParseLLMError, parse_scope_llm_sync
from app.nl2sql.scope_parser_rule import parse_scope_rule
from app.nl2sql.time_intent_display import (
    extract_time_anchor_from_question,
    extract_time_window_from_question,
    resolve_statistical_time_range_display,
)

ScopeConfidence = Literal["high", "low"]


@dataclass(frozen=True)
class ImgDiagScopeTimeMeta:
    time_window: dict[str, str] | None
    time_anchor: dict[str, str] | None
    time_window_tag: str | None
    time_anchor_tag: str | None
    statistical_time_range: dict[str, str] | None


@dataclass(frozen=True)
class ImgDiagScopeDraft:
    boiler: str | None
    device_name: str | None
    piperow_name: str | None
    row_no: int | None
    tube_no: int | None
    confidence: ScopeConfidence
    confidence_reasons: tuple[str, ...]
    time_meta: ImgDiagScopeTimeMeta

    def to_dict(self) -> dict[str, Any]:
        return {
            "boiler": self.boiler,
            "device_name": self.device_name,
            "piperow_name": self.piperow_name,
            "row_no": self.row_no,
            "tube_no": self.tube_no,
            "confidence": self.confidence,
            "confidence_reasons": list(self.confidence_reasons),
            "time_window": self.time_meta.time_window,
            "time_anchor": self.time_meta.time_anchor,
            "time_window_tag": self.time_meta.time_window_tag,
            "time_anchor_tag": self.time_meta.time_anchor_tag,
            "statistical_time_range": self.time_meta.statistical_time_range,
        }

    def to_scope_intent(self) -> QuestionScopeIntent:
        return QuestionScopeIntent(
            boiler=self.boiler,
            device_name=self.device_name,
            piperow_name=self.piperow_name,
            row_no=self.row_no,
            tube_no=self.tube_no,
        )


def _extract_time_meta(scope_question: str) -> ImgDiagScopeTimeMeta:
    q = (scope_question or "").strip()
    tw = extract_time_window_from_question(q)
    ta = extract_time_anchor_from_question(q)
    time_window: dict[str, str] | None = None
    time_window_tag: str | None = None
    if tw is not None:
        time_window = {"start_expr": tw[0], "end_expr": tw[1], "tag": tw[2]}
        time_window_tag = tw[2]
    time_anchor: dict[str, str] | None = None
    time_anchor_tag: str | None = None
    if ta is not None:
        time_anchor = {"end_expr": ta[0], "tag": ta[1]}
        time_anchor_tag = ta[1]
    stat = resolve_statistical_time_range_display(q)
    stat_range = {"start": stat[0], "end": stat[1]} if stat else None
    return ImgDiagScopeTimeMeta(
        time_window=time_window,
        time_anchor=time_anchor,
        time_window_tag=time_window_tag,
        time_anchor_tag=time_anchor_tag,
        statistical_time_range=stat_range,
    )


def _device_in_lexicon(device_name: str | None) -> bool:
    if not device_name:
        return False
    lex = get_scope_lexicon()
    return device_name in lex.devices or any(device_name in d or d in device_name for d in lex.devices)


def _assess_confidence(
    *,
    rule_scope: QuestionScopeIntent,
    llm_scope: QuestionScopeIntent,
) -> tuple[ScopeConfidence, tuple[str, ...]]:
    reasons: list[str] = []
    if rule_scope.device_name != llm_scope.device_name and (
        rule_scope.device_name or llm_scope.device_name
    ):
        reasons.append("rule_llm_device_mismatch")
    device = llm_scope.device_name or rule_scope.device_name
    if device and not _device_in_lexicon(device):
        reasons.append("device_not_in_lexicon")
    if reasons:
        return "low", tuple(reasons)
    return "high", ()


def parse_img_diag_scope_draft(
    scope_question: str,
    *,
    llm_client: Any | None = None,
    prompt_registry: Any | None = None,
) -> ImgDiagScopeDraft:
    """第一层：LLM 解析 + 锅炉规则覆盖 + 时间程序解析。"""
    q = (scope_question or "").strip()
    rule_scope = parse_scope_rule(q)
    time_meta = _extract_time_meta(q)
    try:
        llm_scope = parse_scope_llm_sync(
            q,
            llm_client=llm_client,
            prompt_registry=prompt_registry,
        )
    except ScopeParseLLMError:
        llm_scope = rule_scope
    boiler = NL2SQLChain._extract_unit_keyword_from_question(q) or rule_scope.boiler
    confidence, reasons = _assess_confidence(rule_scope=rule_scope, llm_scope=llm_scope)
    return ImgDiagScopeDraft(
        boiler=boiler,
        device_name=llm_scope.device_name,
        piperow_name=llm_scope.piperow_name,
        row_no=llm_scope.row_no,
        tube_no=llm_scope.tube_no,
        confidence=confidence,
        confidence_reasons=reasons,
        time_meta=time_meta,
    )


def apply_scope_patch(draft: ImgDiagScopeDraft, patch: dict[str, Any] | None) -> ImgDiagScopeDraft:
    if not patch:
        return draft
    boiler = patch.get("boiler", draft.boiler)
    device_name = patch.get("device_name", draft.device_name)
    piperow_name = patch.get("piperow_name", draft.piperow_name)
    row_no = patch.get("row_no", draft.row_no)
    tube_no = patch.get("tube_no", draft.tube_no)
    if isinstance(boiler, str):
        boiler = boiler.strip() or None
    if isinstance(device_name, str):
        device_name = device_name.strip() or None
    if isinstance(piperow_name, str):
        piperow_name = piperow_name.strip() or None
    if isinstance(row_no, str) and row_no.isdigit():
        row_no = int(row_no)
    if isinstance(tube_no, str) and tube_no.isdigit():
        tube_no = int(tube_no)
    return ImgDiagScopeDraft(
        boiler=boiler if boiler else None,
        device_name=device_name if device_name else None,
        piperow_name=piperow_name if piperow_name else None,
        row_no=row_no if isinstance(row_no, int) and row_no > 0 else None,
        tube_no=tube_no if isinstance(tube_no, int) and tube_no > 0 else None,
        confidence=draft.confidence,
        confidence_reasons=draft.confidence_reasons,
        time_meta=draft.time_meta,
    )


def missing_required_scope_fields(draft: ImgDiagScopeDraft) -> list[str]:
    missing: list[str] = []
    if not draft.boiler:
        missing.append("boiler")
    if not draft.device_name:
        missing.append("device_name")
    return missing


def should_trigger_scope_hitl(draft: ImgDiagScopeDraft) -> tuple[bool, str]:
    """
    第一层人机协同：仅当结构化解析未成功（机组/受热面必填缺失）时触发。

    解析成功（boiler + device_name 均非空）时不 interrupt，交由库表 SQL 校验；
    SQL 无结果时在 scope_db_validate 节点触发第二层人机协同。
    """
    missing = missing_required_scope_fields(draft)
    if missing:
        return True, f"missing:{','.join(missing)}"
    return False, ""


def scope_parse_succeeded(draft: ImgDiagScopeDraft) -> bool:
    """机组与受热面均已解析出非空值。"""
    return not missing_required_scope_fields(draft)


def build_scope_intent_text(
    draft: ImgDiagScopeDraft,
    *,
    scope_question: str | None = None,
) -> str:
    """将结构化 scope + 时间合成 NL2SQL 解析用短句。"""
    parts: list[str] = []
    if draft.boiler:
        parts.append(str(draft.boiler))
    if draft.device_name:
        parts.append(str(draft.device_name))
    if draft.piperow_name:
        parts.append(str(draft.piperow_name))
    if draft.row_no is not None:
        parts.append(f"第{draft.row_no}排")
    if draft.tube_no is not None:
        parts.append(f"第{draft.tube_no}根")
    time_line = _time_phrase_from_question(scope_question or "")
    if time_line:
        parts.append(time_line)
    return " ".join(p for p in parts if p).strip()


def _time_phrase_from_question(q: str) -> str:
    """从问句提取可读时间片段（用于 scope_intent_text）。"""
    import re

    text = (q or "").strip()
    if not text:
        return ""
    m = re.search(
        r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)",
        text,
    )
    if m:
        return m.group(1).replace("年", "-").replace("月", "-").replace("日", "")
    for kw in ("今天", "昨天", "前天", "大前天"):
        if kw in text:
            return kw
    return ""


def confirmed_scope_from_draft(draft: ImgDiagScopeDraft) -> dict[str, Any]:
    out = draft.to_dict()
    out.pop("confidence", None)
    out.pop("confidence_reasons", None)
    return out
