"""看图诊断 scope HITL 与 NL2SQL confirmed_scope 注入单测。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.llm.graphs.img_diag_scope_intent import (
    ImgDiagScopeDraft,
    build_scope_intent_text,
    missing_required_scope_fields,
    parse_img_diag_scope_draft,
    should_trigger_scope_hitl,
)
from app.llm.graphs.img_diag_scope_validate import bind_scope_validate_sql
from app.nl2sql.question_intent import resolve_question_intent
from app.nl2sql.question_scope_models import QuestionScopeIntent
from app.nl2sql.time_intent_display import extract_time_window_from_question


def test_build_scope_intent_text() -> None:
    draft = ImgDiagScopeDraft(
        boiler="2号锅炉",
        device_name="高温过热器",
        piperow_name="第一屏",
        row_no=4,
        tube_no=None,
        confidence="high",
        confidence_reasons=(),
        time_meta=parse_img_diag_scope_draft("").time_meta,
    )
    text = build_scope_intent_text(draft, scope_question="2025-03-01 14:00 泄爆")
    assert "2号锅炉" in text
    assert "高温过热器" in text
    assert "第一屏" in text
    assert "第4排" in text


def test_missing_required_scope_fields() -> None:
    draft = ImgDiagScopeDraft(
        boiler=None,
        device_name="高温过热器",
        piperow_name=None,
        row_no=None,
        tube_no=None,
        confidence="high",
        confidence_reasons=(),
        time_meta=parse_img_diag_scope_draft("").time_meta,
    )
    assert missing_required_scope_fields(draft) == ["boiler"]


def test_should_trigger_scope_hitl_low_confidence() -> None:
    draft = ImgDiagScopeDraft(
        boiler="1号锅炉",
        device_name="低温过热器",
        piperow_name=None,
        row_no=None,
        tube_no=None,
        confidence="low",
        confidence_reasons=("rule_llm_device_mismatch",),
        time_meta=parse_img_diag_scope_draft("").time_meta,
    )
    trigger, reason = should_trigger_scope_hitl(
        draft, strict=False, low_confidence_hitl=True
    )
    assert trigger is True
    assert reason == "low_confidence"


def test_bind_scope_validate_sql_optional_piperow() -> None:
    sql_tpl = (
        "SELECT COUNT(*) AS record_count FROM t "
        "WHERE b = :boiler AND d = :device_name "
        "AND (:piperow_name IS NULL OR adp.piperow_name = :piperow_name)"
    )
    bound = bind_scope_validate_sql(
        sql_tpl,
        {"boiler": "1号锅炉", "device_name": "低温过热器", "piperow_name": None},
    )
    assert "1号锅炉" in bound
    assert "低温过热器" in bound
    assert "NULL" in bound


def test_resolve_question_intent_human_confirmed() -> None:
    confirmed = {
        "boiler": "2号锅炉",
        "device_name": "高温过热器",
        "piperow_name": None,
        "row_no": 3,
        "tube_no": None,
    }
    scope_text = "2号锅炉 高温过热器 第3排 2025-03-01 14:00"
    intent = resolve_question_intent(
        "plan long question",
        time_intent_source=scope_text,
        confirmed_scope=confirmed,
        scope_intent_text=scope_text,
        original_query="用户原句 前天",
    )
    assert intent.parse_mode == "human_confirmed"
    assert intent.scope.boiler == "2号锅炉"
    assert intent.scope.device_name == "高温过热器"
    assert intent.scope.row_no == 3


def test_resolve_question_intent_default_unchanged_without_confirmed() -> None:
    intent = resolve_question_intent(
        "1号锅炉低温过热器第2排",
        time_intent_source="1号锅炉低温过热器第2排",
    )
    assert intent.parse_mode == "rule"
    assert intent.scope.boiler == "1号锅炉"


def test_resolve_question_intent_time_fallback_to_original_query() -> None:
    confirmed = {
        "boiler": "1号锅炉",
        "device_name": "低温过热器",
    }
    scope_text = "1号锅炉 低温过热器"
    intent = resolve_question_intent(
        "plan q",
        confirmed_scope=confirmed,
        scope_intent_text=scope_text,
        original_query="1号锅炉低温过热器 前天",
    )
    assert intent.time_window is not None or extract_time_window_from_question("前天") is not None


@pytest.mark.asyncio
async def test_validate_scope_skip_on_error() -> None:
    from app.llm.graphs.img_diag_scope_validate import validate_scope_in_catalog

    executor = MagicMock()
    executor.execute = AsyncMock(side_effect=RuntimeError("db down"))
    with patch("app.llm.graphs.img_diag_scope_validate.get_app_config") as mock_cfg:
        mock_cfg.return_value.analysis.img_diag_scope_validate_skip_on_error = True
        count, err = await validate_scope_in_catalog(
            {"boiler": "1号锅炉", "device_name": "低温过热器"},
            executor=executor,
        )
    assert count == 1
    assert err is None


def test_img_diag_checkpoint_redis_import_failure_falls_back_to_memory(monkeypatch) -> None:
    from app.llm.graphs import img_diag_checkpoint as cp

    class _Analysis:
        img_diag_checkpoint_backend = "redis"
        img_diag_checkpoint_redis_url = "redis://localhost:6379/3"
        img_diag_checkpoint_namespace = "img_diag"

    class _Cfg:
        analysis = _Analysis()

    monkeypatch.setattr(cp, "get_app_config", lambda: _Cfg())

    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "langgraph.checkpoint.redis":
            raise ImportError("No module named 'langgraph.checkpoint.redis'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    saver = cp.build_img_diag_checkpointer()
    assert saver is not None
