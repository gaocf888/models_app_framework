"""看图诊断 scope 结构化解析：LLM + 规则、置信度、scope_intent_text 合成。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.nl2sql.chain import NL2SQLChain
from app.nl2sql.img_diag_scope_parser_llm import (
    ScopeParseLLMError,
    parse_img_diag_scope_llm_sync,
)
from app.nl2sql.question_scope_models import QuestionScopeIntent
from app.nl2sql.scope_lexicon import get_scope_lexicon
from app.nl2sql.scope_parser_rule import parse_scope_rule
from app.nl2sql.time_intent_display import (
    extract_time_anchor_from_question,
    extract_time_window_from_question,
    resolve_statistical_time_range_display,
)

ScopeConfidence = Literal["high", "low"]

SCOPE_RELAX_ORDER: tuple[str, ...] = ("tube_no", "row_no", "check_location_name")


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
    check_location_name: str | None
    row_no: int | None
    tube_no: int | None
    confidence: ScopeConfidence
    confidence_reasons: tuple[str, ...]
    time_meta: ImgDiagScopeTimeMeta

    def to_dict(self) -> dict[str, Any]:
        return {
            "boiler": self.boiler,
            "device_name": self.device_name,
            "check_location_name": self.check_location_name,
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
            check_location_name=self.check_location_name,
            row_no=self.row_no,
            tube_no=self.tube_no,
        )


def normalize_img_diag_scope_dict(raw: dict[str, Any] | None) -> dict[str, Any]:
    """归一化 scope 字段；兼容旧字段 piperow_name。"""
    if not raw:
        return {}
    out = dict(raw)
    if not out.get("check_location_name") and out.get("piperow_name"):
        out["check_location_name"] = out.get("piperow_name")
    out.pop("piperow_name", None)
    return out


def draft_from_scope_dict(raw: dict[str, Any], *, time_meta: ImgDiagScopeTimeMeta) -> ImgDiagScopeDraft:
    scope = normalize_img_diag_scope_dict(raw)
    row = scope.get("row_no")
    tube = scope.get("tube_no")
    if isinstance(row, str) and row.isdigit():
        row = int(row)
    if isinstance(tube, str) and tube.isdigit():
        tube = int(tube)
    return ImgDiagScopeDraft(
        boiler=(str(scope["boiler"]).strip() if scope.get("boiler") else None),
        device_name=(str(scope["device_name"]).strip() if scope.get("device_name") else None),
        check_location_name=(
            str(scope["check_location_name"]).strip() if scope.get("check_location_name") else None
        ),
        row_no=row if isinstance(row, int) and row > 0 else None,
        tube_no=tube if isinstance(tube, int) and tube > 0 else None,
        confidence=scope.get("confidence", "high"),
        confidence_reasons=tuple(scope.get("confidence_reasons") or ()),
        time_meta=time_meta,
    )


def relax_scope_one_level(scope: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """去掉最细一级范围字段；返回 (新 scope, 被去掉的字段名)。"""
    normalized = normalize_img_diag_scope_dict(scope)
    for field in SCOPE_RELAX_ORDER:
        if normalized.get(field) is not None:
            relaxed = dict(normalized)
            relaxed[field] = None
            return relaxed, field
    return normalized, None


def scope_dict_for_validate(scope: dict[str, Any]) -> dict[str, Any]:
    s = normalize_img_diag_scope_dict(scope)
    return {
        "boiler": s.get("boiler"),
        "device_name": s.get("device_name"),
        "check_location_name": s.get("check_location_name"),
        "row_no": s.get("row_no"),
        "tube_no": s.get("tube_no"),
    }


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
    llm_device: str | None,
) -> tuple[ScopeConfidence, tuple[str, ...]]:
    reasons: list[str] = []
    if rule_scope.device_name != llm_device and (rule_scope.device_name or llm_device):
        reasons.append("rule_llm_device_mismatch")
    device = llm_device or rule_scope.device_name
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
    scope_field_exclusions: frozenset[str] | set[str] | None = None,
) -> ImgDiagScopeDraft:
    """第一层：看图诊断专用 LLM 解析 + 锅炉规则覆盖 + 时间程序解析。"""
    q = (scope_question or "").strip()
    excluded = frozenset(scope_field_exclusions or ())
    rule_scope = parse_scope_rule(q)
    time_meta = _extract_time_meta(q)
    try:
        llm_fields = parse_img_diag_scope_llm_sync(
            q,
            llm_client=llm_client,
            prompt_registry=prompt_registry,
            excluded_fields=excluded,
        )
    except ScopeParseLLMError:
        llm_fields = {
            "device_name": rule_scope.device_name,
            "check_location_name": rule_scope.piperow_name,
            "row_no": rule_scope.row_no,
            "tube_no": rule_scope.tube_no,
        }
    boiler = NL2SQLChain._extract_unit_keyword_from_question(q) or rule_scope.boiler
    device_name = llm_fields.get("device_name") or rule_scope.device_name
    confidence, reasons = _assess_confidence(rule_scope=rule_scope, llm_device=llm_fields.get("device_name"))

    draft = ImgDiagScopeDraft(
        boiler=boiler,
        device_name=device_name,
        check_location_name=llm_fields.get("check_location_name"),
        row_no=llm_fields.get("row_no"),
        tube_no=llm_fields.get("tube_no"),
        confidence=confidence,
        confidence_reasons=reasons,
        time_meta=time_meta,
    )
    return apply_scope_field_exclusions_to_draft(draft, excluded)


def apply_scope_field_exclusions_to_draft(
    draft: ImgDiagScopeDraft,
    excluded: frozenset[str] | set[str],
) -> ImgDiagScopeDraft:
    if not excluded:
        return draft
    ex = set(excluded)
    return ImgDiagScopeDraft(
        boiler=draft.boiler,
        device_name=draft.device_name,
        check_location_name=(
            None if "check_location_name" in ex else draft.check_location_name
        ),
        row_no=None if "row_no" in ex else draft.row_no,
        tube_no=None if "tube_no" in ex else draft.tube_no,
        confidence=draft.confidence,
        confidence_reasons=draft.confidence_reasons,
        time_meta=draft.time_meta,
    )


def apply_scope_patch(draft: ImgDiagScopeDraft, patch: dict[str, Any] | None) -> ImgDiagScopeDraft:
    if not patch:
        return draft
    patch = normalize_img_diag_scope_dict(patch)
    boiler = patch.get("boiler", draft.boiler)
    device_name = patch.get("device_name", draft.device_name)
    check_location_name = patch.get("check_location_name", draft.check_location_name)
    row_no = patch.get("row_no", draft.row_no)
    tube_no = patch.get("tube_no", draft.tube_no)
    if isinstance(boiler, str):
        boiler = boiler.strip() or None
    if isinstance(device_name, str):
        device_name = device_name.strip() or None
    if isinstance(check_location_name, str):
        check_location_name = check_location_name.strip() or None
    if isinstance(row_no, str) and row_no.isdigit():
        row_no = int(row_no)
    if isinstance(tube_no, str) and tube_no.isdigit():
        tube_no = int(tube_no)
    return ImgDiagScopeDraft(
        boiler=boiler if boiler else None,
        device_name=device_name if device_name else None,
        check_location_name=check_location_name if check_location_name else None,
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
    missing = missing_required_scope_fields(draft)
    if missing:
        return True, f"missing:{','.join(missing)}"
    return False, ""


def scope_parse_succeeded(draft: ImgDiagScopeDraft) -> bool:
    return not missing_required_scope_fields(draft)


def build_scope_intent_text(
    draft: ImgDiagScopeDraft,
    *,
    scope_question: str | None = None,
) -> str:
    parts: list[str] = []
    if draft.boiler:
        parts.append(str(draft.boiler))
    if draft.device_name:
        parts.append(str(draft.device_name))
    if draft.check_location_name:
        parts.append(str(draft.check_location_name))
    if draft.row_no is not None:
        parts.append(f"第{draft.row_no}排")
    if draft.tube_no is not None:
        parts.append(f"第{draft.tube_no}根")
    time_line = _time_phrase_from_question(scope_question or "")
    if time_line:
        parts.append(time_line)
    return " ".join(p for p in parts if p).strip()


def _time_phrase_from_question(q: str) -> str:
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
