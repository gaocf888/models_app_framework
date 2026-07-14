"""临时复现：错图 + 正确台账首请求是否应 interrupt。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.llm.graphs.img_diag_scope_graph import ImgDiagScopeHitlRunner
from app.llm.graphs.img_diag_scope_intent import ImgDiagScopeDraft, parse_img_diag_scope_draft
from app.llm.graphs.img_diag_vision_display import VISION_REJECT_INTERRUPT_REASON


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


@pytest.fixture
def scope_runner() -> ImgDiagScopeHitlRunner:
    with patch("app.llm.graphs.img_diag_scope_graph.build_img_diag_checkpointer") as mock_cp:
        from langgraph.checkpoint.memory import MemorySaver  # type: ignore

        mock_cp.return_value = MemorySaver()
        yield ImgDiagScopeHitlRunner()


@pytest.mark.asyncio
async def test_wrong_image_correct_scope_must_interrupt(scope_runner: ImgDiagScopeHitlRunner) -> None:
    assert scope_runner.available()
    img_diag_request = {
        "user_id": "u_repro",
        "session_id": "u_repro_s1",
        "query": "1号锅炉水冷壁螺旋段前墙吹灰孔77",
        "img_diag_subtype": "defect_ident",
        "image_urls": ["http://minio/wrong.jpg"],
    }
    with patch(
        "app.llm.graphs.img_diag_scope_graph.parse_img_diag_scope_draft",
        return_value=_full_draft(),
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
            request_id="anl_repro_wrong_img_good_scope",
            orchestrator_path="vision_first",
            vision_prefetch={
                "is_boiler_pressure_part_image": False,
                "vision_narrative": "- 办公桌椅",
            },
        )
    assert r1["status"] == "interrupt", r1
    intr = r1.get("interrupt_payload") or {}
    assert intr.get("interrupt_reason") == VISION_REJECT_INTERRUPT_REASON
    assert intr.get("vision_confirm_blocked") is True
    assert not r1.get("confirmed_scope_intent")
