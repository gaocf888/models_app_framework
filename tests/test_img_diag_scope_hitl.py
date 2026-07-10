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
from app.llm.graphs.img_diag_scope_validate import (
    bind_scope_validate_sql,
    default_scope_validate_sql,
    validate_scope_with_relaxation,
)
from app.nl2sql.question_intent import resolve_question_intent
from app.llm.graphs.img_diag_vision_display import VISION_REJECT_INTERRUPT_REASON


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


def test_default_scope_validate_sql_uses_checklocation_hierarchy() -> None:
    sql = default_scope_validate_sql()
    assert "onc_surface" in sql
    assert "parent_id" in sql
    assert "base_temp_point" in sql
    assert "account_static_device" not in sql
    assert "overhaul_thickness_rate" not in sql
    assert "account_device_piperow" not in sql


def test_bind_scope_validate_sql_checklocation_hierarchy() -> None:
    bound = bind_scope_validate_sql(
        default_scope_validate_sql(),
        {
            "boiler": "1号锅炉",
            "device_name": "水冷壁螺旋段前墙",
            "check_location_name": "吹灰孔33",
        },
    )
    assert "1号锅炉" in bound
    assert "水冷壁螺旋段前墙" in bound
    assert "吹灰孔33" in bound
    assert "parent_id = onc_surface.id" in bound
    assert "onc_surface.device_id" in bound


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


def test_scope_auto_relax_allowed_default_off() -> None:
    from app.llm.graphs.img_diag_scope_graph import scope_auto_relax_allowed

    with patch("app.llm.graphs.img_diag_scope_graph._cfg") as mock_cfg:
        mock_cfg.return_value.img_diag_scope_auto_relax_enabled = False
        assert scope_auto_relax_allowed(hitl_rounds=0) is False
        assert scope_auto_relax_allowed(hitl_rounds=2) is False
        assert scope_auto_relax_allowed(hitl_rounds=5) is False


def test_scope_auto_relax_allowed_when_enabled_after_two_rounds() -> None:
    from app.llm.graphs.img_diag_scope_graph import scope_auto_relax_allowed

    with patch("app.llm.graphs.img_diag_scope_graph._cfg") as mock_cfg:
        mock_cfg.return_value.img_diag_scope_auto_relax_enabled = True
        assert scope_auto_relax_allowed(hitl_rounds=1) is False
        assert scope_auto_relax_allowed(hitl_rounds=2) is True


def test_scope_draft_to_display_omits_unparsed_null_fields() -> None:
    from app.llm.graphs.img_diag_scope_display import scope_draft_to_display

    display = scope_draft_to_display(
        {
            "boiler": "1号锅炉",
            "device_name": "水冷壁螺旋段前墙",
            "check_location_name": None,
            "row_no": None,
            "tube_no": None,
        }
    )
    assert display == {"机组": "1号锅炉", "受热面": "水冷壁螺旋段前墙"}
    assert "检测位置" not in display
    assert "排数" not in display
    assert "管数" not in display


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
async def test_validate_scope_with_relaxation_disabled() -> None:
    calls: list[dict] = []

    async def fake_validate(scope: dict, *, executor=None):
        calls.append(dict(scope))
        return 0, None

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
            allow_auto_relax=False,
        )
    assert count == 0
    assert len(calls) == 1
    assert effective["tube_no"] == 3
    assert relaxed == []


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


def test_detect_scope_field_exclusions_from_text() -> None:
    from app.llm.graphs.img_diag_scope_exclusions import (
        detect_scope_field_exclusions_from_text,
    )

    ex = detect_scope_field_exclusions_from_text("去除排数和管数")
    assert ex == frozenset({"row_no", "tube_no"})
    ex2 = detect_scope_field_exclusions_from_text("不要检测位置")
    assert ex2 == frozenset({"check_location_name"})
    ex3 = detect_scope_field_exclusions_from_text("仅按机组和受热面核对")
    assert ex3 == frozenset({"check_location_name", "row_no", "tube_no"})


def test_finalize_img_diag_llm_scope_respects_excluded_row_no() -> None:
    from app.nl2sql.img_diag_scope_parser_llm import (
        ImgDiagScopeParseLLMOutput,
        finalize_img_diag_llm_scope,
    )

    parsed = ImgDiagScopeParseLLMOutput(
        device_name="水冷壁",
        check_location_name="右墙A2",
        row_no=None,
        tube_no=56,
    )
    out = finalize_img_diag_llm_scope(
        parsed,
        scope_question="1号锅炉水冷壁右墙A2第56根管",
        excluded_fields=frozenset({"row_no", "tube_no"}),
    )
    assert out["row_no"] is None
    assert out["tube_no"] is None
    assert out["check_location_name"] == "右墙A2"


def test_parse_img_diag_scope_draft_applies_field_exclusions() -> None:
    llm_out = {
        "device_name": "水冷壁螺旋段前墙",
        "check_location_name": "吹灰孔33",
        "row_no": 1,
        "tube_no": 56,
    }
    with patch(
        "app.llm.graphs.img_diag_scope_intent.parse_img_diag_scope_llm_sync",
        return_value=llm_out,
    ):
        draft = parse_img_diag_scope_draft(
            "1号锅炉水冷壁螺旋段前墙吹灰孔33第56根管\n去除排数和管数",
            scope_field_exclusions=frozenset({"row_no", "tube_no"}),
        )
    assert draft.row_no is None
    assert draft.tube_no is None
    assert draft.check_location_name == "吹灰孔33"


def test_apply_human_scope_response_records_field_exclusions() -> None:
    from app.llm.graphs.img_diag_scope_graph import _apply_human_scope_response

    state = {
        "query": "1号锅炉水冷壁螺旋段前墙吹灰孔33第56根管",
        "scope_cumulative_text": "1号锅炉水冷壁螺旋段前墙吹灰孔33第56根管",
        "scope_draft": {
            "boiler": "1号锅炉",
            "device_name": "水冷壁螺旋段前墙",
            "check_location_name": "吹灰孔33",
            "row_no": 1,
            "tube_no": 56,
        },
    }
    updated = _apply_human_scope_response(
        state,
        {"action": "edit_scope", "payload": {"user_supplement": "去除排数和管数"}},
    )
    assert updated["scope_field_exclusions"] == ["row_no", "tube_no"]
    assert updated["scope_draft"]["row_no"] is None
    assert updated["scope_draft"]["tube_no"] is None
    assert updated["scope_draft"]["check_location_name"] == "吹灰孔33"


def test_scope_hitl_db_matched_prompt() -> None:
    from app.llm.graphs.img_diag_scope_display import (
        SCOPE_HITL_DB_MATCHED_PROMPT,
        SCOPE_HITL_DB_NOT_MATCHED_PROMPT,
    )

    assert "匹配成功" in SCOPE_HITL_DB_MATCHED_PROMPT
    assert SCOPE_HITL_DB_MATCHED_PROMPT != SCOPE_HITL_DB_NOT_MATCHED_PROMPT


def test_route_after_validate_pending_matched_confirm() -> None:
    from app.llm.graphs.img_diag_scope_graph import _route_after_validate

    assert _route_after_validate({"pending_matched_confirm": True}) == "scope_human_confirm"


def test_matched_confirm_affirmative_finalizes_without_reparse() -> None:
    from app.llm.graphs.img_diag_scope_graph import _apply_human_scope_response

    state = {
        "pending_matched_confirm": True,
        "scope_cumulative_text": "1号锅炉低温过热器",
        "scope_draft": {
            "boiler": "1号锅炉",
            "device_name": "低温过热器",
            "check_location_name": None,
            "row_no": None,
            "tube_no": None,
        },
    }
    updated = _apply_human_scope_response(
        state,
        {"action": "confirm_scope", "payload": {}},
    )
    assert not updated.get("pending_matched_confirm")
    assert updated.get("confirmed_scope_intent", {}).get("boiler") == "1号锅炉"
    assert updated.get("scope_cumulative_text") == "1号锅炉低温过热器"


def test_matched_confirm_affirmative_text_finalizes() -> None:
    from app.llm.graphs.img_diag_scope_graph import _apply_human_scope_response

    state = {
        "pending_matched_confirm": True,
        "scope_cumulative_text": "1号锅炉低温过热器",
        "scope_draft": {
            "boiler": "1号锅炉",
            "device_name": "低温过热器",
        },
    }
    updated = _apply_human_scope_response(
        state,
        {"action": "edit_scope", "payload": {"user_supplement": "确认，继续"}},
    )
    assert updated.get("confirmed_scope_intent")
    assert updated.get("scope_cumulative_text") == "1号锅炉低温过热器"


def test_matched_confirm_with_correction_reruns_preflight() -> None:
    from app.llm.graphs.img_diag_scope_graph import _apply_human_scope_response

    state = {
        "pending_matched_confirm": True,
        "scope_cumulative_text": "1号锅炉水冷壁螺旋段前墙吹灰孔33第56根管",
        "scope_draft": {
            "boiler": "1号锅炉",
            "device_name": "水冷壁螺旋段前墙",
            "check_location_name": "吹灰孔33",
            "row_no": 1,
            "tube_no": 56,
        },
    }
    updated = _apply_human_scope_response(
        state,
        {
            "action": "edit_scope",
            "payload": {"user_supplement": "检测位置应为吹灰孔33"},
        },
    )
    assert not updated.get("pending_matched_confirm")
    assert "检测位置应为吹灰孔33" in (updated.get("scope_cumulative_text") or "")
    assert not updated.get("confirmed_scope_intent")


def test_is_matched_confirm_affirmative_response() -> None:
    from app.llm.graphs.img_diag_scope_affirmation import (
        is_affirmative_supplement,
        is_matched_confirm_affirmative_response,
    )

    assert is_matched_confirm_affirmative_response("confirm_scope", {}) is True
    assert is_matched_confirm_affirmative_response(
        "edit_scope", {"user_supplement": "确认"}
    ) is True
    assert is_matched_confirm_affirmative_response(
        "edit_scope", {"user_supplement": "好的，继续"}
    ) is True
    assert is_matched_confirm_affirmative_response(
        "edit_scope", {"user_supplement": "检测位置应为吹灰孔33"}
    ) is False
    assert is_matched_confirm_affirmative_response(
        "edit_scope", {"scope_patch": {"检测位置": "吹灰孔33"}}
    ) is False

    # 口语变体：后缀/前缀归一
    for phrase in (
        "没问题了",
        "可以的",
        "请继续",
        "那就继续吧",
        "嗯",
        "行吧",
        "开始吧",
        "yes",
        "confirm",
        "确认以上信息",
        "👍",
    ):
        assert is_affirmative_supplement(phrase) is True, phrase

    # 含校正语义仍为非肯定
    for phrase in (
        "检测位置应为吹灰孔33",
        "去除排数和管数",
        "不对，应该是吹灰孔33",
    ):
        assert is_affirmative_supplement(phrase) is False, phrase


@pytest.mark.asyncio
async def test_db_validate_leakage_burst_no_image_skips_matched_confirm() -> None:
    """泄爆分析无图：库匹配成功后直接 confirmed，不再中断让用户确认。"""
    from app.llm.graphs.img_diag_scope_graph import make_img_diag_scope_nodes

    nodes = make_img_diag_scope_nodes()
    db_validate = nodes["scope_db_validate"]
    state = {
        "scope_draft": {
            "boiler": "2号锅炉",
            "device_name": "水冷壁螺旋段前墙",
            "check_location_name": "吹灰孔19",
            "row_no": 1,
            "tube_no": None,
        },
        "scope_cumulative_text": "2号锅炉水冷壁螺旋段前墙吹灰孔19 1排",
        "hitl_rounds": 0,
        "img_diag_subtype": "leakage_burst",
        "img_diag_request": {"image_urls": [], "img_diag_subtype": "leakage_burst"},
    }
    with patch(
        "app.llm.graphs.img_diag_scope_graph.validate_scope_with_relaxation",
        new_callable=AsyncMock,
        return_value=(
            1,
            {
                "boiler": "2号锅炉",
                "device_name": "水冷壁螺旋段前墙",
                "check_location_name": "吹灰孔19",
                "row_no": 1,
            },
            [],
            None,
        ),
    ), patch(
        "app.llm.graphs.img_diag_scope_graph._scope_matched_confirm_enabled",
        return_value=True,
    ):
        out = await db_validate(state)
    assert not out.get("pending_matched_confirm")
    assert out.get("confirmed_scope_intent", {}).get("boiler") == "2号锅炉"
    assert out.get("scope_intent_text")
    assert out.get("interrupt_reason") != "db_validate_matched"


@pytest.mark.asyncio
async def test_db_validate_leakage_burst_with_image_skips_matched_confirm() -> None:
    """泄爆分析有图：库匹配成功后同样直接 confirmed，不再 matched 待确认。"""
    from app.llm.graphs.img_diag_scope_graph import make_img_diag_scope_nodes

    nodes = make_img_diag_scope_nodes()
    db_validate = nodes["scope_db_validate"]
    state = {
        "scope_draft": {
            "boiler": "2号锅炉",
            "device_name": "水冷壁螺旋段前墙",
            "check_location_name": "吹灰孔19",
            "row_no": 1,
            "tube_no": None,
        },
        "scope_cumulative_text": "2号锅炉水冷壁螺旋段前墙吹灰孔19",
        "hitl_rounds": 0,
        "img_diag_subtype": "leakage_burst",
        "img_diag_request": {
            "image_urls": ["http://a.jpg"],
            "img_diag_subtype": "leakage_burst",
        },
        "vision_prefetch_data": {
            "is_boiler_pressure_part_image": True,
            "vision_narrative": "- 爆口形貌",
        },
    }
    with patch(
        "app.llm.graphs.img_diag_scope_graph.validate_scope_with_relaxation",
        new_callable=AsyncMock,
        return_value=(
            1,
            {
                "boiler": "2号锅炉",
                "device_name": "水冷壁螺旋段前墙",
                "check_location_name": "吹灰孔19",
                "row_no": 1,
            },
            [],
            None,
        ),
    ), patch(
        "app.llm.graphs.img_diag_scope_graph._scope_matched_confirm_enabled",
        return_value=True,
    ):
        out = await db_validate(state)
    assert not out.get("pending_matched_confirm")
    assert out.get("confirmed_scope_intent", {}).get("boiler") == "2号锅炉"
    assert out.get("scope_intent_text")
    assert out.get("interrupt_reason") != "db_validate_matched"


@pytest.mark.asyncio
async def test_db_validate_first_success_auto_confirms_scope() -> None:
    from app.llm.graphs.img_diag_scope_graph import make_img_diag_scope_nodes

    nodes = make_img_diag_scope_nodes()
    db_validate = nodes["scope_db_validate"]
    state = {
        "scope_draft": {
            "boiler": "1号锅炉",
            "device_name": "低温过热器",
            "check_location_name": None,
            "row_no": None,
            "tube_no": None,
        },
        "scope_cumulative_text": "1号锅炉低温过热器",
        "hitl_rounds": 0,
    }
    with patch(
        "app.llm.graphs.img_diag_scope_graph.validate_scope_with_relaxation",
        new_callable=AsyncMock,
        return_value=(1, {"boiler": "1号锅炉", "device_name": "低温过热器"}, [], None),
    ), patch(
        "app.llm.graphs.img_diag_scope_graph._scope_matched_confirm_enabled",
        return_value=True,
    ):
        out = await db_validate(state)
    assert not out.get("pending_matched_confirm")
    assert out.get("confirmed_scope_intent", {}).get("boiler") == "1号锅炉"
    assert out.get("scope_intent_text")


@pytest.mark.asyncio
async def test_db_validate_after_hitl_skips_matched_confirm() -> None:
    from app.llm.graphs.img_diag_scope_graph import make_img_diag_scope_nodes

    nodes = make_img_diag_scope_nodes()
    db_validate = nodes["scope_db_validate"]
    state = {
        "scope_draft": {
            "boiler": "1号锅炉",
            "device_name": "低温过热器",
            "check_location_name": None,
            "row_no": None,
            "tube_no": None,
        },
        "scope_cumulative_text": "1号锅炉低温过热器",
        "hitl_rounds": 1,
    }
    with patch(
        "app.llm.graphs.img_diag_scope_graph.validate_scope_with_relaxation",
        new_callable=AsyncMock,
        return_value=(1, {"boiler": "1号锅炉", "device_name": "低温过热器"}, [], None),
    ), patch(
        "app.llm.graphs.img_diag_scope_graph._scope_matched_confirm_enabled",
        return_value=True,
    ):
        out = await db_validate(state)
    assert not out.get("pending_matched_confirm")
    assert out.get("confirmed_scope_intent", {}).get("boiler") == "1号锅炉"
    assert out.get("scope_intent_text")


@pytest.mark.asyncio
async def test_db_validate_after_hitl_with_vision_block_auto_confirms_scope() -> None:
    """补台账后库表命中但视觉仍拒识：台账自动放行，仅保留视觉门禁 interrupt。"""
    from app.llm.graphs.img_diag_scope_graph import make_img_diag_scope_nodes

    nodes = make_img_diag_scope_nodes()
    db_validate = nodes["scope_db_validate"]
    state = {
        "scope_draft": {
            "boiler": "1号锅炉",
            "device_name": "水冷壁螺旋段前墙",
            "check_location_name": "吹灰孔7",
            "row_no": 1,
            "tube_no": None,
        },
        "scope_cumulative_text": "1号锅炉水冷壁螺旋段前墙吹灰孔7",
        "hitl_rounds": 1,
        "initial_query_empty": True,
        "img_diag_subtype": "defect_ident",
        "img_diag_request": {
            "image_urls": ["http://wrong.jpg"],
            "img_diag_subtype": "defect_ident",
        },
        "vision_prefetch_data": {"is_boiler_pressure_part_image": False},
        "scope_hitl_prompt": "未识别解析到台账信息，请补充！",
        "scope_interrupt_reason": "missing:boiler,device_name",
    }
    with patch(
        "app.llm.graphs.img_diag_scope_graph.validate_scope_with_relaxation",
        new_callable=AsyncMock,
        return_value=(
            1,
            {
                "boiler": "1号锅炉",
                "device_name": "水冷壁螺旋段前墙",
                "check_location_name": "吹灰孔7",
                "row_no": 1,
            },
            [],
            None,
        ),
    ), patch(
        "app.llm.graphs.img_diag_scope_graph._scope_matched_confirm_enabled",
        return_value=True,
    ):
        out = await db_validate(state)
    assert not out.get("pending_matched_confirm")
    assert out.get("confirmed_scope_intent", {}).get("boiler") == "1号锅炉"
    assert out.get("vision_confirm_blocked") is True
    assert out.get("interrupt_reason") == VISION_REJECT_INTERRUPT_REASON

@pytest.mark.asyncio
async def test_db_validate_matched_confirm_disabled() -> None:
    from app.llm.graphs.img_diag_scope_graph import make_img_diag_scope_nodes

    nodes = make_img_diag_scope_nodes()
    db_validate = nodes["scope_db_validate"]
    state = {
        "scope_draft": {
            "boiler": "1号锅炉",
            "device_name": "低温过热器",
        },
        "scope_cumulative_text": "1号锅炉低温过热器",
        "hitl_rounds": 0,
    }
    with patch(
        "app.llm.graphs.img_diag_scope_graph.validate_scope_with_relaxation",
        new_callable=AsyncMock,
        return_value=(1, {"boiler": "1号锅炉", "device_name": "低温过热器"}, [], None),
    ), patch(
        "app.llm.graphs.img_diag_scope_graph._scope_matched_confirm_enabled",
        return_value=False,
    ):
        out = await db_validate(state)
    assert not out.get("pending_matched_confirm")
    assert out.get("confirmed_scope_intent")


