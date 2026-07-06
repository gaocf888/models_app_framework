"""
复现用户多轮 scope HITL：错图 → 换图 → 同 URL 重复提交 + user_supplement。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.llm.graphs.img_diag_scope_graph import (
    ImgDiagScopeHitlRunner,
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


def _full_draft() -> ImgDiagScopeDraft:
    tm = parse_img_diag_scope_draft("").time_meta
    return ImgDiagScopeDraft(
        boiler="1号锅炉",
        device_name="水冷壁螺旋段前墙",
        check_location_name="吹灰孔77",
        row_no=None,
        tube_no=None,
        confidence="high",
        confidence_reasons=(),
        time_meta=tm,
    )


def _mock_parse_scope_draft(text: str, **kwargs: object) -> ImgDiagScopeDraft:
    cumulative = (text or "").strip()
    if USER_SUPPLEMENT in cumulative or ("1号锅炉" in cumulative and "吹灰孔77" in cumulative):
        return _full_draft()
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
        aupdate_mock = AsyncMock(wraps=scope_runner._graph.aupdate_state)
        with patch.object(scope_runner._graph, "aupdate_state", aupdate_mock):
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

        # 同 URL 重复提交不应因 prep 写 checkpoint
        aupdate_mock.assert_not_called()

        snap = await _checkpoint_snapshot(scope_runner, request_id)
        cumulative = str(snap.get("scope_cumulative_text") or "")
        assert USER_SUPPLEMENT in cumulative or "1号锅炉" in cumulative, (
            f"expected supplement merged, got cumulative={cumulative!r}, result={r3}"
        )
        if r3["status"] == "interrupt":
            intr = r3.get("interrupt_payload") or {}
            assert intr.get("scope_cumulative_text") or cumulative


@pytest.mark.asyncio
async def test_prepare_skips_aupdate_when_same_urls_and_supplement_only(
    scope_runner: ImgDiagScopeHitlRunner,
) -> None:
    assert scope_runner.available()
    request_id = "anl_test_prep_skip_same_url"
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
                vision_refresh=_vision_refresh,
            )
        assert err is None
        aupdate_mock.assert_not_called()
