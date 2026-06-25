"""看图诊断 scope HITL 与 NL2SQL confirmed_scope 注入单测。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.llm.graphs.img_diag_scope_intent import (
    ImgDiagScopeDraft,
    build_scope_intent_text,
    missing_required_scope_fields,
    normalize_img_diag_scope_dict,
    parse_img_diag_scope_draft,
    relax_scope_one_level,
    should_trigger_scope_hitl,
)
from app.llm.graphs.img_diag_scope_validate import bind_scope_validate_sql, validate_scope_with_relaxation
from app.nl2sql.question_intent import resolve_question_intent


def test_build_scope_intent_text() -> None:
    draft = ImgDiagScopeDraft(
        boiler="2号锅炉",
        device_name="高温过热器",
        check_location_name="出口段",
        row_no=4,
        tube_no=None,
        confidence="high",
        confidence_reasons=(),
        time_meta=parse_img_diag_scope_draft("").time_meta,
    )
    text = build_scope_intent_text(draft, scope_question="2025-03-01 14:00 泄爆")
    assert "2号锅炉" in text
    assert "高温过热器" in text
    assert "出口段" in text
    assert "第4排" in text


def test_missing_required_scope_fields() -> None:
    draft = ImgDiagScopeDraft(
        boiler=None,
        device_name="高温过热器",
        check_location_name=None,
        row_no=None,
        tube_no=None,
        confidence="high",
        confidence_reasons=(),
        time_meta=parse_img_diag_scope_draft("").time_meta,
    )
    assert missing_required_scope_fields(draft) == ["boiler"]


def test_should_trigger_scope_hitl_only_when_required_fields_missing() -> None:
    complete = ImgDiagScopeDraft(
        boiler="1号锅炉",
        device_name="低温过热器",
        check_location_name=None,
        row_no=None,
        tube_no=None,
        confidence="low",
        confidence_reasons=("rule_llm_device_mismatch",),
        time_meta=parse_img_diag_scope_draft("").time_meta,
    )
    trigger, reason = should_trigger_scope_hitl(complete)
    assert trigger is False
    assert reason == ""

    incomplete = ImgDiagScopeDraft(
        boiler="1号锅炉",
        device_name=None,
        check_location_name=None,
        row_no=None,
        tube_no=None,
        confidence="high",
        confidence_reasons=(),
        time_meta=parse_img_diag_scope_draft("").time_meta,
    )
    trigger, reason = should_trigger_scope_hitl(incomplete)
    assert trigger is True
    assert reason == "missing:device_name"


def test_scope_draft_to_display_cn_labels() -> None:
    from app.llm.graphs.img_diag_scope_display import (
        SCOPE_HITL_DB_NOT_MATCHED_PROMPT,
        format_missing_fields_cn,
        normalize_scope_patch_keys,
        scope_draft_to_display,
    )

    display = scope_draft_to_display(
        {
            "boiler": "1号锅炉",
            "device_name": "水冷壁",
            "check_location_name": "水冷壁右墙A2",
            "row_no": 3,
            "tube_no": 56,
        }
    )
    assert display == {
        "机组": "1号锅炉",
        "受热面": "水冷壁",
        "检测位置": "水冷壁右墙A2",
        "排数": 3,
        "管数": 56,
    }
    assert format_missing_fields_cn(["boiler", "device_name"]) == "机组、受热面"
    assert "业务库中未匹配" in SCOPE_HITL_DB_NOT_MATCHED_PROMPT
    assert normalize_scope_patch_keys({"机组": "2号锅炉", "检测位置": "出口段"}) == {
        "boiler": "2号锅炉",
        "check_location_name": "出口段",
    }


def test_normalize_legacy_piperow_name() -> None:
    out = normalize_img_diag_scope_dict(
        {"boiler": "1号锅炉", "piperow_name": "第一层", "device_name": "低过"}
    )
    assert out["check_location_name"] == "第一层"
    assert "piperow_name" not in out


def test_relax_scope_one_level_order() -> None:
    scope = {
        "boiler": "1号锅炉",
        "device_name": "低过",
        "check_location_name": "第一层",
        "row_no": 2,
        "tube_no": 3,
    }
    s1, f1 = relax_scope_one_level(scope)
    assert f1 == "tube_no"
    assert s1["tube_no"] is None
    s2, f2 = relax_scope_one_level(s1)
    assert f2 == "row_no"
    s3, f3 = relax_scope_one_level(s2)
    assert f3 == "check_location_name"
    s4, f4 = relax_scope_one_level(s3)
    assert f4 is None


def test_bind_scope_validate_sql_check_location() -> None:
    sql_tpl = (
        "SELECT COUNT(*) AS record_count FROM t "
        "WHERE b = :boiler AND d = :device_name "
        "AND (:check_location_name IS NULL OR loc LIKE CONCAT('%', :check_location_name, '%'))"
    )
    bound = bind_scope_validate_sql(
        sql_tpl,
        {
            "boiler": "1号锅炉",
            "device_name": "低温过热器",
            "check_location_name": "出口段",
        },
    )
    assert "出口段" in bound
    assert "1号锅炉" in bound


def test_resolve_question_intent_human_confirmed() -> None:
    confirmed = {
        "boiler": "2号锅炉",
        "device_name": "高温过热器",
        "check_location_name": "出口段",
        "row_no": 3,
        "tube_no": None,
    }
    scope_text = "2号锅炉 高温过热器 出口段 第3排 2025-03-01 14:00"
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
    assert intent.scope.check_location_name == "出口段"
    assert intent.scope.row_no == 3


@pytest.mark.asyncio
async def test_validate_scope_with_relaxation_auto() -> None:
    calls: list[dict] = []

    async def fake_validate(scope: dict, *, executor=None):
        calls.append(dict(scope))
        if scope.get("tube_no") is not None:
            return 0, None
        if scope.get("row_no") is not None:
            return 0, None
        return 1, None

    with patch(
        "app.llm.graphs.img_diag_scope_validate.validate_scope_in_catalog",
        side_effect=fake_validate,
    ):
        count, effective, relaxed, err = await validate_scope_with_relaxation(
            {
                "boiler": "1号锅炉",
                "device_name": "低过",
                "check_location_name": "第一层",
                "row_no": 2,
                "tube_no": 3,
            },
            allow_auto_relax=True,
        )
    assert count == 1
    assert effective["row_no"] is None
    assert effective["tube_no"] is None
    assert "tube_no" in relaxed
    assert "row_no" in relaxed


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
