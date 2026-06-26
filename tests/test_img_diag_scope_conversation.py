"""看图诊断 scope HITL 会话历史写入测试。"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.llm.graphs.analysis_img_diag_runner import AnalysisImgDiagGraphRunner, ImgDiagScopeInterrupt
from app.llm.graphs.img_diag_scope_display import (
    format_scope_hitl_assistant_message,
    format_scope_hitl_user_message,
)
from app.models.analysis import AnalysisImgDiagRequest, AnalysisOptions


class TestScopeHitlConversationFormat(unittest.TestCase):
    def test_assistant_message_includes_title_and_draft(self) -> None:
        text = format_scope_hitl_assistant_message(
            {
                "prompt": "请补充机组、受热面",
                "missing_fields": ["机组", "受热面"],
                "scope_draft_display": {"机组": "1号锅炉", "受热面": "低温过热器"},
            }
        )
        self.assertIn("【台账信息确认】", text)
        self.assertIn("请补充机组、受热面", text)
        self.assertIn("1号锅炉", text)

    def test_user_message_supplement_and_patch(self) -> None:
        text = format_scope_hitl_user_message(
            action="edit_scope",
            payload={
                "user_supplement": "检测位置应为吹灰孔33",
                "scope_patch": {"受热面": "高温过热器"},
            },
        )
        self.assertIn("检测位置应为吹灰孔33", text)
        self.assertIn("高温过热器", text)


def _make_runner() -> AnalysisImgDiagGraphRunner:
    runner = AnalysisImgDiagGraphRunner(
        conv_manager=MagicMock(),
        llm_client=MagicMock(),
        prompt_registry=MagicMock(),
        hybrid_rag=MagicMock(),
        nl2sql_service=MagicMock(),
    )
    runner._analysis_cfg.img_diag_lane_timeout_seconds = 30.0
    runner._analysis_cfg.nl2sql_llm_planner_enabled = False
    return runner


def _sample_req() -> AnalysisImgDiagRequest:
    return AnalysisImgDiagRequest(
        user_id="u1",
        session_id="s1",
        img_diag_subtype="defect_ident",
        query="1号炉低温过热器第2排缺陷识别",
        image_urls=["http://example.com/a.jpg"],
        options=AnalysisOptions(enable_rag=False),
    )


class TestImgDiagScopeConversationPersist(unittest.IsolatedAsyncioTestCase):
    async def test_sync_interrupt_persists_user_and_assistant(self) -> None:
        runner = _make_runner()
        req = _sample_req()
        interrupt_payload = {
            "prompt": "请补充机组",
            "scope_draft_display": {"机组": "1号锅炉"},
        }
        with patch.object(
            runner,
            "_run_scope_hitl_phase",
            new=AsyncMock(
                return_value={
                    "status": "interrupt",
                    "request_id": "anl_hitl",
                    "interrupt_payload": interrupt_payload,
                }
            ),
        ):
            with self.assertRaises(ImgDiagScopeInterrupt):
                await runner.run_with_img_diag(req)

        runner._conv.append_user_message.assert_called_once()
        runner._conv.append_assistant_message.assert_called_once()
        assistant_text = runner._conv.append_assistant_message.call_args[0][2]
        self.assertIn("【台账信息确认】", assistant_text)

    async def test_stream_interrupt_persists_without_duplicate_user_on_gather(self) -> None:
        runner = _make_runner()
        req = _sample_req()
        with (
            patch.object(
                runner,
                "_run_scope_hitl_phase",
                new=AsyncMock(
                    return_value={
                        "status": "interrupt",
                        "request_id": "anl_hitl",
                        "resume_token": "tok",
                        "interrupt_payload": {"prompt": "请确认台账"},
                    }
                ),
            ),
            patch.object(runner, "_gather_img_diag_pack", new=AsyncMock()) as gather_mock,
        ):
            events: list[dict] = []
            async for ev in runner.iter_img_diag_stream_events(req):
                events.append(ev)

        self.assertEqual("img_diag_scope_input_required", events[-1].get("event"))
        runner._conv.append_user_message.assert_called_once()
        runner._conv.append_assistant_message.assert_called_once()
        gather_mock.assert_not_called()

    async def test_resume_persists_user_supplement(self) -> None:
        runner = _make_runner()
        with patch.object(
            runner,
            "_get_scope_hitl_runner",
        ) as get_runner:
            scope_runner = MagicMock()
            scope_runner.resume_until_confirmed_or_interrupt = AsyncMock(
                return_value={
                    "status": "interrupt",
                    "request_id": "anl_hitl",
                    "resume_token": "tok2",
                    "interrupt_payload": {"prompt": "仍未匹配"},
                }
            )
            get_runner.return_value = scope_runner

            events: list[dict] = []
            async for ev in runner.iter_img_diag_scope_resume_stream_events(
                resume_token="tok",
                user_id="u1",
                session_id="s1",
                action="edit_scope",
                payload={"user_supplement": "受热面是高温过热器"},
            ):
                events.append(ev)

        runner._conv.append_user_message.assert_called_once()
        user_text = runner._conv.append_user_message.call_args[0][2]
        self.assertIn("高温过热器", user_text)
        runner._conv.append_assistant_message.assert_called_once()


if __name__ == "__main__":
    unittest.main()
