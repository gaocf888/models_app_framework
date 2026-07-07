"""
复现用户多轮 scope HITL：错图 → 换图 → 同 URL 重复提交 + user_supplement。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.llm.graphs.img_diag_scope_graph import (
    ImgDiagScopeHitlRunner,
    _mark_scope_correction_parsed,
    _prepare_scope_resume_state,
    img_diag_graph_configurable,
)
from app.llm.graphs.img_diag_scope_intent import (
    ImgDiagScopeDraft,
    parse_img_diag_scope_draft,
)

USER_ID = "u_3002"
SESSION_ID = "u_3002_s_1"
WRONG_IMAGE = "http://minio/wrong.jpg"
GOOD_IMAGE = "http://minio/good.jpg"
USER_SUPPLEMENT = "1号锅炉水冷壁螺旋段前墙吹灰孔77"


def _empty_draft() -> ImgDiagScopeDraft:
    tm = parse_img_diag_scope_draft("").time_meta
    return ImgDiagScopeDraft(
        boiler=None,
        device_name=None,
        check_location_name=None,
        row_no=None,
        tube_no=None,
        confidence="low",
        confidence_reasons=("empty",),
        time_meta=tm,
    )


def _full_draft(check_location: str = "吹灰孔77") -> ImgDiagScopeDraft:
    tm = parse_img_diag_scope_draft("").time_meta
    return ImgDiagScopeDraft(
        boiler="1号锅炉",
        device_name="水冷壁螺旋段前墙",
        check_location_name=check_location,
        row_no=None,
        tube_no=None,
        confidence="high",
        confidence_reasons=(),
        time_meta=tm,
    )


def _partial_wrong_location_draft() -> ImgDiagScopeDraft:
    return _full_draft(check_location="吹灰孔99")


def _mock_parse_scope_draft(text: str, **kwargs: object) -> ImgDiagScopeDraft:
    cumulative = (text or "").strip()
    # 模拟生产 LLM：多轮校正同时出现时可能仍命中较早的错误行
    if "吹灰孔77" in cumulative and "吹灰孔88" in cumulative:
        idx77 = cumulative.rfind("吹灰孔77")
        idx88 = cumulative.rfind("吹灰孔88")
        if idx88 > idx77:
            return _full_draft("吹灰孔88")
    if "吹灰孔77" in cumulative and (
        "应为吹灰孔77" in cumulative or USER_SUPPLEMENT in cumulative
    ):
        return _full_draft("吹灰孔77")
    if "吹灰孔88" in cumulative or "应为吹灰孔88" in cumulative:
        return _full_draft("吹灰孔88")
    if USER_SUPPLEMENT in cumulative or ("1号锅炉" in cumulative and "吹灰孔77" in cumulative):
        return _full_draft("吹灰孔77")
    if "1号锅炉" in cumulative and "吹灰孔99" in cumulative:
        return _partial_wrong_location_draft()
    if "1号锅炉" in cumulative and "水冷壁" in cumulative:
        return _partial_wrong_location_draft()
    if cumulative:
        return _full_draft()
    return _empty_draft()


async def _vision_refresh(req: dict) -> tuple[dict, int, str]:
    urls = [u for u in (req.get("image_urls") or []) if isinstance(u, str)]
    if urls and GOOD_IMAGE in urls:
        return {
            "is_boiler_pressure_part_image": True,
            "vision_narrative": "- 管壁裂纹",
        }, 100, "ok"
    return {"is_boiler_pressure_part_image": False, "vision_narrative": "- 电机"}, 80, "ok"


async def _checkpoint_snapshot(runner: ImgDiagScopeHitlRunner, thread_id: str) -> dict:
    assert runner._graph is not None
    snap = await runner._graph.aget_state(img_diag_graph_configurable(thread_id))
    return dict(snap.values or {})


@pytest.fixture
def scope_runner() -> ImgDiagScopeHitlRunner:
    with patch("app.llm.graphs.img_diag_scope_graph.build_img_diag_checkpointer") as mock_cp:
        from langgraph.checkpoint.memory import MemorySaver  # type: ignore[import-not-found]

        mock_cp.return_value = MemorySaver()
        yield ImgDiagScopeHitlRunner()
    # fixture teardown


@pytest.mark.asyncio
async def test_same_image_urls_with_supplement_merges_cumulative(scope_runner: ImgDiagScopeHitlRunner) -> None:
    """第 4 轮：image_urls 与上一轮相同 + user_supplement（用户日志场景）。"""
    assert scope_runner.available()
    request_id = "anl_test_same_url_supplement"
    img_diag_request = {
        "user_id": USER_ID,
        "session_id": SESSION_ID,
        "query": "",
        "img_diag_subtype": "defect_ident",
        "image_urls": [GOOD_IMAGE],
    }

    with patch(
        "app.llm.graphs.img_diag_scope_graph.parse_img_diag_scope_draft",
        side_effect=_mock_parse_scope_draft,
    ), patch(
        "app.llm.graphs.img_diag_scope_graph.validate_scope_with_relaxation",
        new_callable=AsyncMock,
        return_value=(
            1,
            {
                "boiler": "1号锅炉",
                "device_name": "水冷壁螺旋段前墙",
                "check_location_name": "吹灰孔77",
            },
            [],
            None,
        ),
    ):
        r1 = await scope_runner.run_until_scope_confirmed_or_interrupt(
            img_diag_request,
            request_id=request_id,
            orchestrator_path="vision_first",
            vision_prefetch={"is_boiler_pressure_part_image": True, "vision_narrative": "- ok"},
        )
        assert r1["status"] == "interrupt"

        # 第 2 轮：换图（同 GOOD_IMAGE 已在上面的 request 里）
        r2 = await scope_runner.resume_until_confirmed_or_interrupt(
            resume_token=r1["resume_token"],
            user_id=USER_ID,
            session_id=SESSION_ID,
            action="confirm_scope",
            payload={"image_urls": [GOOD_IMAGE]},
            vision_refresh=_vision_refresh,
        )
        assert r2["status"] == "interrupt"

        assert scope_runner._graph is not None
        r3 = await scope_runner.resume_until_confirmed_or_interrupt(
            resume_token=r2["resume_token"],
            user_id=USER_ID,
            session_id=SESSION_ID,
            action="confirm_scope",
            payload={
                "user_supplement": USER_SUPPLEMENT,
                "image_urls": [GOOD_IMAGE],
            },
            vision_refresh=_vision_refresh,
        )

        snap = await _checkpoint_snapshot(scope_runner, request_id)
        cumulative = str(snap.get("scope_cumulative_text") or "")
        assert USER_SUPPLEMENT in cumulative or "1号锅炉" in cumulative, (
            f"expected supplement merged, got cumulative={cumulative!r}, result={r3}"
        )
        draft = snap.get("scope_draft") or {}
        assert draft.get("boiler") == "1号锅炉"
        if r3["status"] == "interrupt":
            intr = r3.get("interrupt_payload") or {}
            assert intr.get("scope_cumulative_text") or cumulative


@pytest.mark.asyncio
async def test_resume_empty_query_wrong_image_plus_supplement_reparses_scope(
    scope_runner: ImgDiagScopeHitlRunner,
) -> None:
    """场景2：首问 query 为空；resume 错图 + user_supplement 应重新解析台账。"""
    assert scope_runner.available()
    request_id = "anl_test_empty_query_supplement"
    img_diag_request = {
        "user_id": USER_ID,
        "session_id": SESSION_ID,
        "query": "",
        "img_diag_subtype": "defect_ident",
        "image_urls": [WRONG_IMAGE],
    }
    validate_mock = AsyncMock(
        return_value=(
            1,
            {
                "boiler": "1号锅炉",
                "device_name": "水冷壁螺旋段前墙",
                "check_location_name": "吹灰孔77",
            },
            [],
            None,
        ),
    )

    with patch(
        "app.llm.graphs.img_diag_scope_graph.parse_img_diag_scope_draft",
        side_effect=_mock_parse_scope_draft,
    ), patch(
        "app.llm.graphs.img_diag_scope_graph.validate_scope_with_relaxation",
        validate_mock,
    ):
        r1 = await scope_runner.run_until_scope_confirmed_or_interrupt(
            img_diag_request,
            request_id=request_id,
            orchestrator_path="vision_first",
            vision_prefetch={"is_boiler_pressure_part_image": False, "vision_narrative": "- 电机"},
        )
        assert r1["status"] == "interrupt"
        intr1 = r1.get("interrupt_payload") or {}
        assert "未识别" in str(intr1.get("prompt") or "")

        r2 = await scope_runner.resume_until_confirmed_or_interrupt(
            resume_token=r1["resume_token"],
            user_id=USER_ID,
            session_id=SESSION_ID,
            action="confirm_scope",
            payload={
                "image_urls": [WRONG_IMAGE],
                "user_supplement": USER_SUPPLEMENT,
            },
            vision_refresh=_vision_refresh,
        )

        snap = await _checkpoint_snapshot(scope_runner, request_id)
        cumulative = str(snap.get("scope_cumulative_text") or "")
        assert USER_SUPPLEMENT in cumulative or "吹灰孔77" in cumulative
        draft = snap.get("scope_draft") or {}
        assert draft.get("boiler") == "1号锅炉"
        assert draft.get("check_location_name") == "吹灰孔77"
        assert validate_mock.await_count >= 1
        if r2["status"] == "interrupt":
            intr2 = r2.get("interrupt_payload") or {}
            assert "未识别" not in str(intr2.get("prompt") or "")


@pytest.mark.asyncio
async def test_resume_supplement_corrects_wrong_check_location(
    scope_runner: ImgDiagScopeHitlRunner,
) -> None:
    """场景1：首问台账检测位置错误；resume 错图 + 校正 supplement 应更新 scope。"""
    assert scope_runner.available()
    request_id = "anl_test_wrong_location_supplement"
    wrong_query = "1号锅炉水冷壁螺旋段前墙吹灰孔99"
    img_diag_request = {
        "user_id": USER_ID,
        "session_id": SESSION_ID,
        "query": wrong_query,
        "img_diag_subtype": "defect_ident",
        "image_urls": [WRONG_IMAGE],
    }
    validate_mock = AsyncMock(
        side_effect=lambda scope, **kwargs: (
            1,
            {
                "boiler": "1号锅炉",
                "device_name": "水冷壁螺旋段前墙",
                "check_location_name": (
                    "吹灰孔77"
                    if str(scope.get("check_location_name") or "") == "吹灰孔77"
                    else "吹灰孔99"
                ),
            },
            [],
            None,
        ),
    )

    with patch(
        "app.llm.graphs.img_diag_scope_graph.parse_img_diag_scope_draft",
        side_effect=_mock_parse_scope_draft,
    ), patch(
        "app.llm.graphs.img_diag_scope_graph.validate_scope_with_relaxation",
        validate_mock,
    ):
        r1 = await scope_runner.run_until_scope_confirmed_or_interrupt(
            img_diag_request,
            request_id=request_id,
            orchestrator_path="vision_first",
            vision_prefetch={"is_boiler_pressure_part_image": False},
        )
        assert r1["status"] == "interrupt"

        correction = "检测位置应为吹灰孔77"
        r2 = await scope_runner.resume_until_confirmed_or_interrupt(
            resume_token=r1["resume_token"],
            user_id=USER_ID,
            session_id=SESSION_ID,
            action="confirm_scope",
            payload={
                "image_urls": [WRONG_IMAGE],
                "user_supplement": correction,
            },
            vision_refresh=_vision_refresh,
        )

        snap = await _checkpoint_snapshot(scope_runner, request_id)
        cumulative = str(snap.get("scope_cumulative_text") or "")
        assert correction in cumulative
        draft = snap.get("scope_draft") or {}
        assert draft.get("check_location_name") == "吹灰孔77"
        assert validate_mock.await_count >= 2
        if r2["status"] == "interrupt":
            display = (r2.get("interrupt_payload") or {}).get("scope_draft_display") or {}
            loc = str(display.get("检测位置") or display.get("check_location_name") or "")
            assert "吹灰孔77" in loc or draft.get("check_location_name") == "吹灰孔77"


@pytest.mark.asyncio
async def test_multiround_wrong_then_correct_supplement_reparses(
    scope_runner: ImgDiagScopeHitlRunner,
) -> None:
    """错误校正库表未命中仍 interrupt；正确校正解析校验+视觉通过后自动 confirmed。"""
    assert scope_runner.available()
    request_id = "anl_test_wrong_then_correct_supplement"
    wrong_query = "1号锅炉水冷壁螺旋段前墙吹灰孔99"
    img_diag_request = {
        "user_id": USER_ID,
        "session_id": SESSION_ID,
        "query": wrong_query,
        "img_diag_subtype": "defect_ident",
        "image_urls": [GOOD_IMAGE],
    }
    wrong_correction = "检测位置应为吹灰孔88"
    correct_correction = "检测位置应为吹灰孔77"

    def _validate_side_effect(scope: dict, **kwargs: object):
        loc = str(scope.get("check_location_name") or "")
        if loc == "吹灰孔88":
            return (0, scope, [], "not_found")
        if loc == "吹灰孔77":
            effective = "吹灰孔77"
        else:
            effective = loc or "吹灰孔99"
        return (
            1,
            {
                "boiler": "1号锅炉",
                "device_name": "水冷壁螺旋段前墙",
                "check_location_name": effective,
            },
            [],
            None,
        )

    validate_mock = AsyncMock(side_effect=_validate_side_effect)

    with patch(
        "app.llm.graphs.img_diag_scope_graph.parse_img_diag_scope_draft",
        side_effect=_mock_parse_scope_draft,
    ), patch(
        "app.llm.graphs.img_diag_scope_graph.validate_scope_with_relaxation",
        validate_mock,
    ):
        r1 = await scope_runner.run_until_scope_confirmed_or_interrupt(
            img_diag_request,
            request_id=request_id,
            orchestrator_path="vision_first",
            vision_prefetch={"is_boiler_pressure_part_image": True},
        )
        assert r1["status"] == "interrupt"

        r2 = await scope_runner.resume_until_confirmed_or_interrupt(
            resume_token=r1["resume_token"],
            user_id=USER_ID,
            session_id=SESSION_ID,
            action="confirm_scope",
            payload={"user_supplement": wrong_correction},
            vision_refresh=_vision_refresh,
        )
        assert r2["status"] == "interrupt"
        snap2 = await _checkpoint_snapshot(scope_runner, request_id)
        assert wrong_correction in str(snap2.get("scope_cumulative_text") or "")
        assert snap2.get("needs_db_retry") is True

        r3 = await scope_runner.resume_until_confirmed_or_interrupt(
            resume_token=r2["resume_token"],
            user_id=USER_ID,
            session_id=SESSION_ID,
            action="confirm_scope",
            payload={"user_supplement": correct_correction},
            vision_refresh=_vision_refresh,
        )

        snap3 = await _checkpoint_snapshot(scope_runner, request_id)
        cumulative3 = str(snap3.get("scope_cumulative_text") or "")
        assert correct_correction in cumulative3
        draft3 = snap3.get("scope_draft") or {}
        assert draft3.get("check_location_name") == "吹灰孔77", (
            f"expected 吹灰孔77 after correct supplement, draft={draft3!r}, r3={r3}"
        )
        assert r3["status"] == "confirmed"
        assert validate_mock.await_count >= 3


@pytest.mark.asyncio
async def test_wrong_then_correct_supplement_uses_latest_correction_for_reparse(
    scope_runner: ImgDiagScopeHitlRunner,
) -> None:
    """错误校正后再提交正确校正：重解析须以最新 supplement 为准，不能沿用上一轮错误结果。"""
    assert scope_runner.available()
    request_id = "anl_test_wrong_correct_latest_supplement"
    wrong_query = "1号锅炉水冷壁螺旋段前墙吹灰孔99"
    wrong_correction = "检测位置应为吹灰孔88"
    correct_correction = "检测位置应为吹灰孔77"
    parse_inputs: list[str] = []

    def _parse_with_trace(text: str, **kwargs: object) -> ImgDiagScopeDraft:
        parse_inputs.append(text)
        return _mock_parse_scope_draft(text, **kwargs)

    def _validate_side_effect(scope: dict, **kwargs: object):
        loc = str(scope.get("check_location_name") or "")
        if loc == "吹灰孔88":
            return (0, scope, [], "not_found")
        return (
            1,
            {
                "boiler": "1号锅炉",
                "device_name": "水冷壁螺旋段前墙",
                "check_location_name": loc or "吹灰孔99",
            },
            [],
            None,
        )

    with patch(
        "app.llm.graphs.img_diag_scope_graph.parse_img_diag_scope_draft",
        side_effect=_parse_with_trace,
    ), patch(
        "app.llm.graphs.img_diag_scope_graph.validate_scope_with_relaxation",
        new_callable=AsyncMock,
        side_effect=_validate_side_effect,
    ):
        r1 = await scope_runner.run_until_scope_confirmed_or_interrupt(
            {
                "user_id": USER_ID,
                "session_id": SESSION_ID,
                "query": wrong_query,
                "img_diag_subtype": "defect_ident",
                "image_urls": [GOOD_IMAGE],
            },
            request_id=request_id,
            orchestrator_path="vision_first",
            vision_prefetch={"is_boiler_pressure_part_image": True},
        )
        assert r1["status"] == "interrupt"

        r2 = await scope_runner.resume_until_confirmed_or_interrupt(
            resume_token=r1["resume_token"],
            user_id=USER_ID,
            session_id=SESSION_ID,
            action="confirm_scope",
            payload={"user_supplement": wrong_correction},
            vision_refresh=_vision_refresh,
        )
        assert r2["status"] == "interrupt"
        snap2 = await _checkpoint_snapshot(scope_runner, request_id)
        assert wrong_correction in str(snap2.get("scope_cumulative_text") or "")

        parse_inputs.clear()
        r3 = await scope_runner.resume_until_confirmed_or_interrupt(
            resume_token=r2["resume_token"],
            user_id=USER_ID,
            session_id=SESSION_ID,
            action="confirm_scope",
            payload={"user_supplement": correct_correction},
            vision_refresh=_vision_refresh,
        )

        snap3 = await _checkpoint_snapshot(scope_runner, request_id)
        assert correct_correction in str(snap3.get("scope_cumulative_text") or "")
        assert (snap3.get("scope_draft") or {}).get("check_location_name") == "吹灰孔77"
        assert r3["status"] == "confirmed"
        assert any(
            correct_correction in inp and wrong_correction not in inp for inp in parse_inputs
        ), f"expected latest correction parse input, got {parse_inputs!r}"


@pytest.mark.asyncio
async def test_correction_reparse_auto_confirms_when_scope_and_vision_pass(
    scope_runner: ImgDiagScopeHitlRunner,
) -> None:
    """hitl_rounds>=1 时：校正解析+库表校验+视觉均通过 → 自动 confirmed，不再 interrupt。"""
    assert scope_runner.available()
    request_id = "anl_test_correction_auto_confirm"
    wrong_query = "1号锅炉水冷壁螺旋段前墙吹灰孔99"
    img_diag_request = {
        "user_id": USER_ID,
        "session_id": SESSION_ID,
        "query": wrong_query,
        "img_diag_subtype": "defect_ident",
        "image_urls": [GOOD_IMAGE],
    }
    correction = "检测位置应为吹灰孔77"

    with patch(
        "app.llm.graphs.img_diag_scope_graph.parse_img_diag_scope_draft",
        side_effect=_mock_parse_scope_draft,
    ), patch(
        "app.llm.graphs.img_diag_scope_graph.validate_scope_with_relaxation",
        new_callable=AsyncMock,
        return_value=(
            1,
            {
                "boiler": "1号锅炉",
                "device_name": "水冷壁螺旋段前墙",
                "check_location_name": "吹灰孔77",
            },
            [],
            None,
        ),
    ):
        r1 = await scope_runner.run_until_scope_confirmed_or_interrupt(
            img_diag_request,
            request_id=request_id,
            orchestrator_path="vision_first",
            vision_prefetch={"is_boiler_pressure_part_image": True, "vision_narrative": "- ok"},
        )
        assert r1["status"] == "interrupt"

        r2 = await scope_runner.resume_until_confirmed_or_interrupt(
            resume_token=r1["resume_token"],
            user_id=USER_ID,
            session_id=SESSION_ID,
            action="confirm_scope",
            payload={"user_supplement": correction},
            vision_refresh=_vision_refresh,
        )

        assert r2["status"] == "confirmed"
        snap = await _checkpoint_snapshot(scope_runner, request_id)
        assert (snap.get("scope_draft") or {}).get("check_location_name") == "吹灰孔77"
        assert snap.get("confirmed_scope_intent")


@pytest.mark.asyncio
async def test_prepare_persists_aupdate_when_same_urls_with_supplement(
    scope_runner: ImgDiagScopeHitlRunner,
) -> None:
    assert scope_runner.available()
    request_id = "anl_test_prep_persist_supplement"
    img_diag_request = {
        "user_id": USER_ID,
        "session_id": SESSION_ID,
        "query": "",
        "img_diag_subtype": "defect_ident",
        "image_urls": [GOOD_IMAGE],
    }

    with patch(
        "app.llm.graphs.img_diag_scope_graph.parse_img_diag_scope_draft",
        side_effect=_mock_parse_scope_draft,
    ):
        r1 = await scope_runner.run_until_scope_confirmed_or_interrupt(
            img_diag_request,
            request_id=request_id,
            orchestrator_path="vision_first",
            vision_prefetch={"is_boiler_pressure_part_image": True},
        )
        session = __import__(
            "app.llm.graphs.img_diag_session_store",
            fromlist=["get_img_diag_resume_session"],
        ).get_img_diag_resume_session(r1["resume_token"])
        assert scope_runner._graph is not None
        aupdate_mock = AsyncMock(wraps=scope_runner._graph.aupdate_state)
        with patch.object(scope_runner._graph, "aupdate_state", aupdate_mock):
            err = await _prepare_scope_resume_state(
                scope_runner._graph,
                thread_id=request_id,
                session=session,
                payload={
                    "user_supplement": USER_SUPPLEMENT,
                    "image_urls": [GOOD_IMAGE],
                },
                action="confirm_scope",
                vision_refresh=_vision_refresh,
            )
        assert err is None
        aupdate_mock.assert_called_once()


def test_mark_parsed_skipped_when_pending_supplement_not_in_cumulative() -> None:
    """stale preflight 不应在 cumulative 未含最新 supplement 时清除 pending reparse。"""
    state = {
        "scope_cumulative_text": "1号锅炉水冷壁螺旋段前墙吹灰孔99",
        "scope_correction_epoch": 2,
        "scope_correction_parsed_epoch": 1,
        "scope_correction_pending_reparse": True,
        "scope_pending_reparse_supplement": "检测位置应为吹灰孔77",
    }
    _mark_scope_correction_parsed(state)
    assert state.get("scope_correction_pending_reparse") is True
    assert state.get("scope_pending_reparse_supplement") == "检测位置应为吹灰孔77"
    assert int(state.get("scope_correction_parsed_epoch") or 0) == 1

    state["scope_cumulative_text"] = (
        "1号锅炉水冷壁螺旋段前墙吹灰孔99\n检测位置应为吹灰孔77"
    )
    _mark_scope_correction_parsed(state)
    assert state.get("scope_correction_pending_reparse") is None
    assert int(state.get("scope_correction_parsed_epoch") or 0) == 2
