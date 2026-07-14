from __future__ import annotations

"""
看图诊断编排（缺陷识别 img_diag_defect_ident / 泄爆分析 img_diag_leakage_burst）：
  视觉臂 ‖ NL2SQL 臂（规划→取数→质量门）并行
  → 业务 RAG 臂（串行，query 含用户问题 + 视觉 JSON 摘要）
  → synthesis → finalize

入参：`img_diag_subtype` + `query` + `image_urls`（泄爆可选图）。
可选前置：scope HITL（LangGraph）确认机组/受热面后再进入视觉‖NL2SQL 并行臂。
"""

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, AsyncIterator, Awaitable, Callable, cast
from uuid import uuid4

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.core.metrics import ANALYSIS_REQUEST_COUNT
from app.llm.graphs.analysis_finished_meta import (
    analysis_finished_sse_event,
    build_analysis_finished_meta,
)
from app.llm.graphs.analysis_graph_runner import AnalysisGraphRunner
from app.llm.graphs.analysis_stream_cancel import (
    AnalysisStreamCancelled,
    is_stream_cancelled,
    raise_if_stream_cancelled,
)
from app.llm.graphs.img_diag_scope_display import (
    format_scope_hitl_assistant_message,
    format_scope_hitl_user_message,
)
from app.llm.graphs.img_diag_scope_graph import (
    ImgDiagScopeHitlRunner,
    should_emit_img_diag_vision_preview_on_scope_confirmed,
)
from app.llm.graphs.img_diag_scope_probe import probe_img_diag_scope_route
from app.llm.graphs.img_diag_vision_display import (
    build_vision_findings_display,
    format_vision_hitl_assistant_block,
)
from app.llm.graphs.img_diag_vision_parse import parse_vision_lane_llm_output
from app.models.analysis import (
    AnalysisEvidence,
    AnalysisImgDiagRequest,
    AnalysisNL2SQLCall,
    AnalysisNL2SQLRequest,
    AnalysisTrace,
    AnalysisType,
    AnalysisV2Result,
    DataMode,
    ImgDiagSubtype,
)
from app.services.analysis_stream_hooks import dispatch_analysis_nl2sql_stream_structured
from app.services.chatbot_image_preprocessor import ChatbotImagePreprocessor
from app.services.chatbot_image_utils import build_user_message_with_images

logger = get_logger(__name__)

IMG_DIAG_DEFECT_IDENT_TYPE: AnalysisType = "img_diag_defect_ident"
IMG_DIAG_LEAKAGE_BURST_TYPE: AnalysisType = "img_diag_leakage_burst"


def _sanitize_img_diag_report_text(text: str) -> str:
    """移除报告正文中不应出现的 RAG 等技术术语（流式 chunk 与最终 summary 共用）。"""
    if not (text or "").strip():
        return text or ""
    out = text
    for pat, repl in (
        (r"RAG案例分析", "案例分析"),
        (r"RAG参考片段", "知识库参考"),
        (r"RAG片段", "历史案例要点"),
        (r"RAG案例", "历史案例"),
        (r"依据RAG", "依据历史案例"),
        (r"RAG中的", "知识库中的"),
        (r"RAG", "知识库"),
    ):
        out = re.sub(pat, repl, out, flags=re.IGNORECASE)
    return out


# 合成输入：各 plan item 的时间语义（与 reference SQL 一致；q2b/q2c 为历史全量）
_IMG_DIAG_PLAN_TIME_SCOPE: dict[str, str] = {
    "q1": "anchor_lookback_3d",
    "q2a": "anchor_lookback_3d",
    "q2b": "historical_no_time_filter",
    "q2c": "historical_no_time_filter",
    "q2d": "anchor_lookback_3d",
    "q2e": "anchor_lookback_3d",
    "q3": "anchor_lookback_3d",
    "q4": "anchor_lookback_3d",
    "q5": "anchor_lookback_3d",
}
_IMG_DIAG_SYNTHESIS_ROW_CAPS: dict[str, int] = {
    "q2b": 12,
    "q2c": 10,
    "q5": 15,
}
_IMG_DIAG_SYNTHESIS_DEFAULT_ROW_CAP = 40
_IMG_DIAG_TIME_SCOPE_LABELS: dict[str, str] = {
    "anchor_lookback_3d": "事故锚点向前3天",
    "historical_no_time_filter": "历史全量（SQL 无近3天时间过滤）",
    "unknown": "未知",
}


class ImgDiagScopeInterrupt(Exception):
    """scope HITL 需要用户确认时抛出（同步 API 转为 409）。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        super().__init__(payload.get("prompt") or "scope confirmation required")


@dataclass(frozen=True)
class _ImgDiagSubtypeProfile:
    subtype: ImgDiagSubtype
    analysis_type: AnalysisType
    data_mode: DataMode
    vision_scene: str
    rag_scene_label: str
    prefetch_rag_intent: str
    augmented_rag_intent: str
    synthesis_default: str
    report_default: str
    stream_fallback_summary: str
    orchestrator_stream_id: str
    images_required: bool


_IMG_DIAG_PROFILES: dict[ImgDiagSubtype, _ImgDiagSubtypeProfile] = {
    "defect_ident": _ImgDiagSubtypeProfile(
        subtype="defect_ident",
        analysis_type=IMG_DIAG_DEFECT_IDENT_TYPE,
        data_mode="img_diag_defect_ident",
        vision_scene="analysis_img_diag_vision_defect_ident",
        rag_scene_label="缺陷识别",
        prefetch_rag_intent="规程通识 缺陷处置 运行监护 检修工艺",
        augmented_rag_intent="同类型缺陷历史处置案例 打磨补焊 换管 防磨瓦 运行监护 复测周期",
        synthesis_default=(
            "你是电厂承压管系缺陷识别分析师，需融合图像证据、相关数据摘要与知识库片段，"
            "按「外观形貌智能分析→多维数据关联分析→风险等级判定与处置方案」三章输出报告；"
            "第一章 1.1 须含缺陷走向（沿管轴/横跨管轴）；沟槽与裂纹不可混淆；"
            "若 vision 主类型为沟槽但 signals 含裂纹/周向/横向等，1.2 须优先写裂纹并建议 PT/UT 复核；"
            "无相关数据的维度整节省略，不写「未检索到」占位句；"
            "报告正文禁止 RAG/NL2SQL/库表 等技术术语，依据用现场图像/相关数据/知识库等通俗表述；"
            "处置方案中禁止建议停炉、降负荷、停吹等运行退出措施。"
        ),
        report_default=(
            "输出章节含：一、外观形貌智能分析（1.1 宏观形貌简洁 bullet 罗列）；"
            "二、多维数据关联分析（仅有数据维度）；"
            "三、风险等级判定与处置方案；无数据维度/占位句省略；"
            "禁止 RAG/NL2SQL/库表 等技术术语；依据用相关数据等通俗表述；结尾 AI 辅助分析说明。"
        ),
        stream_fallback_summary="缺陷识别分析生成失败，已返回基础报告，请稍后重试。",
        orchestrator_stream_id="img_diag_defect_ident_stream",
        images_required=True,
    ),
    "leakage_burst": _ImgDiagSubtypeProfile(
        subtype="leakage_burst",
        analysis_type=IMG_DIAG_LEAKAGE_BURST_TYPE,
        data_mode="img_diag_leakage_burst",
        vision_scene="analysis_img_diag_vision_leakage_burst",
        rag_scene_label="泄爆分析",
        prefetch_rag_intent="规程通识 爆管预防 运行监护 检修工艺 标准条文",
        augmented_rag_intent=(
            "同位置同类型历史事故案例 同类型机组典型故障处理经验 标准规程条文 "
            "防控技术资料 同类爆管预防措施 同区域改造案例"
        ),
        synthesis_default=(
            "你是电厂锅炉受热面泄爆溯源高级分析师，需融合图像证据（若有）、"
            "相关数据摘要与知识库/历史案例片段；"
            "按「结论摘要（1.1～1.3）→事故分析（2.N.）→后续风险防控措施（3.1）」三章输出报告；"
            "无现场图片时须综合相关数据与知识库撰写 1.1，禁止编造形貌/可见损伤；"
            "不支持/证据不足类及无数据占位句不输出；"
            "报告正文禁止出现 RAG/RAG片段/RAG案例/NL2SQL/库表 等技术术语，依据用现场图像/相关数据/知识库/历史案例等通俗表述；"
            "3.1 防控措施每条为小标题+段落叙述，禁止 bullet；"
            "预防措施中禁止建议停炉、降负荷、停吹等运行退出措施。"
        ),
        report_default=(
            "输出章节含：一、结论摘要（1.1 事件概述；1.2 三层逻辑；1.3 主因方向仅支持类）；"
            "二、事故分析（#### 2.N. 类别分析，仅支持类）；"
            "三、后续风险防控措施（3.1 防控措施，段落叙述）；"
            "禁止无数据占位句、RAG/RAG片段/RAG案例/NL2SQL/库表 等技术术语及解析范围/证据链/同类案例独立章节；"
            "依据用相关数据/知识库/历史案例等通俗表述；结尾 AI 辅助说明。"
        ),
        stream_fallback_summary="泄爆分析生成失败，已返回基础报告，请稍后重试。",
        orchestrator_stream_id="img_diag_leakage_burst_stream",
        images_required=False,
    ),
}


@dataclass
class _ImgDiagPack:
    """并行臂完成后、合成前后的中间态（同步 / 流式 synthesis 共用）。"""

    req: AnalysisImgDiagRequest
    degrade: list[str]
    parallel_trace: dict[str, Any]
    vision_data: dict[str, Any]
    vision_ms: int
    vision_status: str
    biz_snippets: list[str]
    biz_sources: list[dict[str, Any]]
    rag_ms: int
    rag_status: str
    rag_query: str
    nl_state: dict[str, Any]
    nl_status: str
    calls: list[AnalysisNL2SQLCall]
    gathered_data: dict[str, list[dict]]
    planned_calls: int
    plan_rag_sources: list[dict[str, Any]]
    quality_report: dict[str, Any]
    merged_rag_sources: list[dict[str, Any]]
    rag_citations: list[dict[str, Any]]
    used_rag: bool
    used_plan_rag: bool
    used_business_rag: bool
    planning_ctx: str | None
    synthesis_prompt: str
    synthesis_version: str
    report_version: str
    merged_blob: dict[str, Any]
    parsed_intent_snapshot: dict[str, Any]
    parsed_time_intent: dict[str, Any]
    parsed_scope_intent: dict[str, Any]
    profile: _ImgDiagSubtypeProfile
    request_id: str
    plan_id: str
    confirmed_scope_intent: dict[str, Any] | None = None


class AnalysisImgDiagGraphRunner(AnalysisGraphRunner):
    """看图诊断（缺陷识别 / 泄爆分析）：scope HITL（可选）→ 并行 + 串行 RAG 编排。"""

    def _get_scope_hitl_runner(self) -> ImgDiagScopeHitlRunner:
        runner = getattr(self, "_scope_hitl_runner", None)
        if runner is None:
            runner = ImgDiagScopeHitlRunner(
                llm_client=self._llm,
                prompt_registry=self._prompts,
            )
            self._scope_hitl_runner = runner
        return runner

    def _get_vision_image_preprocessor(self) -> ChatbotImagePreprocessor:
        """复用智能客服图片预处理（缩边/JPEG 重编码），与 chatbot 看图分析链路一致。"""
        proc = getattr(self, "_vision_image_preprocessor", None)
        if proc is None:
            proc = ChatbotImagePreprocessor(get_app_config().chatbot)
            self._vision_image_preprocessor = proc
        return proc

    def _vision_user_text(self, profile: _ImgDiagSubtypeProfile) -> str:
        """固定短 user 句（对齐智能客服看图），不使用业务 query。"""
        if profile.subtype == "defect_ident":
            return (self._analysis_cfg.img_diag_vision_user_query_defect_ident or "").strip() or (
                "请帮我分析图片缺陷"
            )
        return (self._analysis_cfg.img_diag_vision_user_query_leakage_burst or "").strip() or (
            "请分析图片中的爆口/泄漏可见形貌特征。"
        )

    def _build_vision_system_instructions(
        self,
        *,
        user_id: str,
        profile: _ImgDiagSubtypeProfile,
    ) -> str:
        """system = 智能客服 chatbot 模板 + 看图视觉臂 JSON 附录（与客服 persona 同步）。"""
        app_cfg = get_app_config()
        chatbot_ver = (
            (self._analysis_cfg.img_diag_vision_chatbot_prompt_version or "").strip()
            or (app_cfg.chatbot.default_prompt_version or "boiler_v1").strip()
            or "boiler_v1"
        )
        chat_tpl = self._prompts.get_template(
            scene="chatbot",
            user_id=user_id,
            version=chatbot_ver,
            default_version=chatbot_ver,
        )
        vision_tpl = self._prompts.get_template(
            scene=profile.vision_scene,
            user_id=user_id,
            version=None,
        )
        chunks: list[str] = []
        if chat_tpl and chat_tpl.content.strip():
            chunks.append(chat_tpl.content.strip())
        if vision_tpl and vision_tpl.content.strip():
            chunks.append(vision_tpl.content.strip())
        if chunks:
            return "\n\n".join(chunks)
        fallback = (
            "你是承压部件缺陷图像分析助手，仅描述可见证据；输出必须为单个 JSON 对象。"
            if profile.subtype == "defect_ident"
            else "你是锅炉受热面泄爆/爆口图像分析助手，仅描述可见证据；输出必须为单个 JSON 对象。"
        )
        return fallback

    @staticmethod
    def _build_vision_llm_messages(
        *,
        system_instructions: str,
        user_text: str,
        image_urls: list[str],
    ) -> list[dict[str, Any]]:
        """与智能客服一致：system 承载领域/输出契约，user 为短 query + 图片。"""
        content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        for url in image_urls:
            content.append({"type": "image_url", "image_url": {"url": url}})
        return [
            {"role": "system", "content": system_instructions},
            {"role": "user", "content": content},
        ]

    async def _run_scope_hitl_phase(
        self,
        req: AnalysisImgDiagRequest,
        *,
        request_id: str | None = None,
        orchestrator_path: str = "scope_first",
        vision_prefetch: dict[str, Any] | None = None,
        vision_prefetch_ms: int = 0,
        vision_prefetch_status: str = "",
    ) -> dict[str, Any]:
        """scope 人机协同；status=skipped|confirmed|interrupt|error。"""
        runner = self._get_scope_hitl_runner()
        return await runner.run_until_scope_confirmed_or_interrupt(
            req.model_dump(mode="json"),
            request_id=request_id,
            orchestrator_path=orchestrator_path,
            vision_prefetch=vision_prefetch,
            vision_prefetch_ms=vision_prefetch_ms,
            vision_prefetch_status=vision_prefetch_status,
        )

    async def _probe_and_run_scope_hitl_phase(
        self,
        req: AnalysisImgDiagRequest,
        *,
        request_id: str | None = None,
        cancel_checker: Callable[[], Awaitable[bool]] | None = None,
    ) -> tuple[dict[str, Any], str, dict[str, Any] | None, int, str]:
        """
        入口探针 + scope HITL。
        Path2：scope 先行（含匹配成功确认）；Path1 且有图：先视觉臂再 scope HITL。
        返回 (scope_result, orchestrator_path, vision_prefetch, vision_ms, vision_status)。
        """
        profile = self._profile(req)
        runner = self._get_scope_hitl_runner()
        if not runner._scope_hitl_enabled() or not runner.available():
            return {"status": "skipped"}, "scope_first", None, 0, "skipped"

        url_diag = self._image_urls_diag(req.image_urls)
        has_images = url_diag["url_count"] > 0
        probe = await probe_img_diag_scope_route(
            req.query.strip(),
            llm_client=self._llm,
            prompt_registry=self._prompts,
        )
        orchestrator_path = "scope_first"
        vision_prefetch: dict[str, Any] | None = None
        vision_ms = 0
        vision_status = "skipped"

        if has_images:
            orchestrator_path = "vision_first"
            await raise_if_stream_cancelled(cancel_checker)
            vision_prefetch, vision_ms = await self._lane_vision(req, profile)
            vision_status = (
                "skipped" if vision_prefetch.get("vision_skipped") else "success"
            )
            logger.info(
                "img_diag vision_first has_images probe_route=%s request_id=%s vision_status=%s ms=%s",
                probe.route,
                request_id,
                vision_status,
                vision_ms,
            )
        else:
            logger.info(
                "img_diag scope_first no_images probe_route=%s request_id=%s",
                probe.route,
                request_id,
            )

        scope_result = await self._run_scope_hitl_phase(
            req,
            request_id=request_id,
            orchestrator_path=orchestrator_path,
            vision_prefetch=vision_prefetch,
            vision_prefetch_ms=vision_ms,
            vision_prefetch_status=vision_status,
        )
        return scope_result, orchestrator_path, vision_prefetch, vision_ms, vision_status

    @staticmethod
    def _effective_img_diag_synthesis_query(
        req: AnalysisImgDiagRequest,
        *,
        confirmed_scope: dict[str, Any] | None,
        scope_intent_text: str | None,
    ) -> str:
        """
        报告合成用用户问题：HITL 已确认台账时以 scope_intent_text 为准，
        避免首问 query 中过时位置/字段污染「事件概述」等叙述。
        """
        original = (req.query or "").strip()
        confirmed_text = (scope_intent_text or "").strip()
        if confirmed_scope and confirmed_text:
            return confirmed_text
        return original

    @staticmethod
    def _synthesis_query_from_pack(pack: _ImgDiagPack) -> str:
        blob_q = str(pack.merged_blob.get("user_query") or "").strip()
        if blob_q:
            return blob_q
        return (pack.req.query or "").strip()

    @staticmethod
    def _image_urls_diag(image_urls: list[str] | None) -> dict[str, Any]:
        """供 vision/resume 诊断：统计有效 URL 数量并截断预览（避免日志过长）。"""
        raw = list(image_urls or [])
        urls = [u for u in raw if isinstance(u, str) and u.strip()]
        previews: list[str] = []
        for u in urls[:3]:
            s = u.strip()
            previews.append(s if len(s) <= 120 else f"{s[:117]}...")
        return {"url_count": len(urls), "raw_list_len": len(raw), "url_previews": previews}

    @staticmethod
    def _build_img_diag_user_message_content(req: AnalysisImgDiagRequest) -> str:
        urls = [u for u in (req.image_urls or []) if isinstance(u, str) and u.strip()]
        query = req.query.strip()
        if urls:
            return build_user_message_with_images(
                query,
                urls,
                original_image_urls=urls,
                processed_image_urls=urls,
            )
        return query

    def _persist_img_diag_initial_user_message(self, req: AnalysisImgDiagRequest) -> None:
        """流式/同步入口：写入用户首问（含图片块），避免 scope HITL 中断时会话历史缺失。"""
        self._conv.append_user_message(
            req.user_id,
            req.session_id,
            self._build_img_diag_user_message_content(req),
        )

    async def _refresh_vision_for_hitl_request(
        self, img_diag_request: dict[str, Any]
    ) -> tuple[dict[str, Any], int, str]:
        req = AnalysisImgDiagRequest.model_validate(img_diag_request)
        profile = self._profile(req)
        data, ms = await self._lane_vision(req, profile)
        status = "skipped" if data.get("vision_skipped") else "success"
        return data, int(ms or 0), status

    def _persist_scope_hitl_assistant_message(
        self,
        *,
        user_id: str,
        session_id: str,
        interrupt_payload: dict[str, Any] | None,
    ) -> None:
        if not interrupt_payload:
            return
        self._conv.append_assistant_message(
            user_id,
            session_id,
            format_scope_hitl_assistant_message(interrupt_payload),
        )

    def _persist_scope_hitl_user_message(
        self,
        *,
        user_id: str,
        session_id: str,
        action: str,
        payload: dict[str, Any] | None,
    ) -> None:
        payload = payload or {}
        image_urls_raw = payload.get("image_urls")
        urls = (
            [u.strip() for u in image_urls_raw if isinstance(u, str) and u.strip()]
            if isinstance(image_urls_raw, list)
            else []
        )
        text_payload = (
            {k: v for k, v in payload.items() if k != "image_urls"}
            if urls
            else payload
        )
        text = format_scope_hitl_user_message(action=action, payload=text_payload)
        content = (
            build_user_message_with_images(
                text,
                urls,
                original_image_urls=urls,
                processed_image_urls=urls,
            )
            if urls
            else text
        )
        self._conv.append_user_message(user_id, session_id, content)

    def _persist_vision_preview_assistant_message(
        self,
        *,
        user_id: str,
        session_id: str,
        vision_data: dict[str, Any] | None,
        img_diag_subtype: str,
    ) -> None:
        block = format_vision_hitl_assistant_block(
            vision_data,
            img_diag_subtype=img_diag_subtype,
            include_macro_appearance_heading=True,
        )
        if block:
            self._conv.append_assistant_message(user_id, session_id, block)

    def _maybe_persist_scope_confirmed_vision_preview(
        self,
        *,
        scope_result: dict[str, Any],
        orchestrator_path: str,
        vision_prefetch: dict[str, Any] | None,
        user_id: str,
        session_id: str,
        img_diag_subtype: str,
    ) -> bool:
        if not should_emit_img_diag_vision_preview_on_scope_confirmed(
            orchestrator_path=orchestrator_path,
            vision_prefetch=vision_prefetch,
            vision_hitl_preview_delivered=bool(scope_result.get("vision_hitl_preview_delivered")),
        ):
            return False
        self._persist_vision_preview_assistant_message(
            user_id=user_id,
            session_id=session_id,
            vision_data=vision_prefetch,
            img_diag_subtype=img_diag_subtype,
        )
        return True

    @staticmethod
    def _scope_interrupt_sse_event(result: dict[str, Any]) -> dict[str, Any]:
        intr = result.get("interrupt_payload") or {}
        include_scope = intr.get("include_scope_confirm_preview", True)
        event: dict[str, Any] = {
            "event": "img_diag_scope_input_required",
            "request_id": result.get("request_id"),
            "resume_token": result.get("resume_token"),
            "prompt": intr.get("prompt"),
            "missing_fields": intr.get("missing_fields") or [] if include_scope else [],
            "suggested_actions": intr.get("suggested_actions")
            or ["confirm_scope", "edit_scope", "abort"],
            "interrupt_reason": intr.get("interrupt_reason"),
            "orchestrator_path": intr.get("orchestrator_path") or result.get("orchestrator_path"),
            "include_vision_preview": bool(intr.get("include_vision_preview")),
            "include_scope_confirm_preview": bool(include_scope),
            "confirm_reply_example": intr.get("confirm_reply_example") if include_scope else "",
            "scope_hitl_assistant_message": intr.get("scope_hitl_assistant_message") if include_scope else "",
            "scope_reply_example_label": intr.get("scope_reply_example_label") if include_scope else "",
            "vision_hitl_assistant_message": intr.get("vision_hitl_assistant_message"),
        }
        if intr.get("hitl_mode"):
            event["hitl_mode"] = intr.get("hitl_mode")
        if intr.get("ui_buttons"):
            event["ui_buttons"] = intr.get("ui_buttons")
        if include_scope:
            event["scope_draft"] = intr.get("scope_draft")
            event["scope_draft_display"] = intr.get("scope_draft_display")
        else:
            event["scope_draft"] = {}
            event["scope_draft_display"] = {}
        if intr.get("initial_query_empty"):
            event["initial_query_empty"] = True
            if intr.get("scope_cumulative_text") is not None:
                event["scope_cumulative_text"] = intr.get("scope_cumulative_text")
        scope_reason = intr.get("scope_interrupt_reason")
        if scope_reason:
            event["scope_interrupt_reason"] = scope_reason
        if intr.get("include_vision_preview"):
            if intr.get("vision_findings_display"):
                event["vision_findings_display"] = intr.get("vision_findings_display")
        return event

    @staticmethod
    def _vision_preview_sse_event(
        *,
        request_id: str,
        img_diag_subtype: str,
        vision_data: dict[str, Any] | None,
        vision_ms: int,
        vision_status: str,
        hitl_mode: str | None = None,
        ui_buttons: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        event: dict[str, Any] = {
            "event": "img_diag_vision_preview",
            "request_id": request_id,
            "orchestrator_path": "vision_first",
            "img_diag_subtype": img_diag_subtype,
            "vision_findings_display": build_vision_findings_display(
                vision_data,
                img_diag_subtype=img_diag_subtype,
            ),
            "vision_hitl_assistant_message": format_vision_hitl_assistant_block(
                vision_data,
                img_diag_subtype=img_diag_subtype,
                include_macro_appearance_heading=True,
            ),
            "include_vision_preview": True,
        }
        if hitl_mode:
            event["hitl_mode"] = hitl_mode
        if ui_buttons:
            event["ui_buttons"] = ui_buttons
        return event

    @staticmethod
    def _profile(req: AnalysisImgDiagRequest) -> _ImgDiagSubtypeProfile:
        return _IMG_DIAG_PROFILES[req.img_diag_subtype]

    @classmethod
    def business_rag_query(
        cls,
        req: AnalysisImgDiagRequest,
        vision_data: dict[str, Any],
        *,
        parsed_scope: dict[str, Any] | None = None,
        profile: _ImgDiagSubtypeProfile | None = None,
    ) -> str:
        """业务 RAG 检索语句：用户问题 + 视觉结论 + 可选 NL2SQL 解析范围。"""
        prof = profile or cls._profile(req)
        scope_parts: list[str] = []
        if parsed_scope:
            for key in ("boiler", "device_name", "check_location_name", "row_no", "tube_no"):
                val = parsed_scope.get(key)
                if val is not None and str(val).strip():
                    scope_parts.append(f"{key}={val}")
        scope_line = " ".join(scope_parts)
        lines = [req.query.strip(), f"场景:{prof.rag_scene_label}"]

        if prof.subtype == "leakage_burst":
            burst_type = vision_data.get("burst_type") or vision_data.get("defect_type") or ""
            defect_signals = vision_data.get("defect_signals") or vision_data.get("burst_signals") or []
            severity = vision_data.get("severity") or vision_data.get("risk_level") or ""
            affected = vision_data.get("affected_surface") or ""
            failure_hints = vision_data.get("failure_mode_hints") or []
            signals_txt = (
                ", ".join(str(x) for x in defect_signals[:12])
                if isinstance(defect_signals, list)
                else str(defect_signals)
            )
            hints_txt = (
                ", ".join(str(x) for x in failure_hints[:8])
                if isinstance(failure_hints, list)
                else str(failure_hints)
            )
            lines.extend([
                f"爆口/泄漏形貌:{burst_type}",
                f"严重度:{severity}",
                f"受热面:{affected}",
                f"可见爆口线索:{signals_txt}",
            ])
            if hints_txt:
                lines.append(f"形貌机理线索:{hints_txt}")
        else:
            defect_type = vision_data.get("defect_type") or ""
            defect_signals = vision_data.get("defect_signals") or []
            risk_level = vision_data.get("risk_level") or vision_data.get("severity") or ""
            affected = vision_data.get("affected_surface") or ""
            failure_hints = vision_data.get("failure_mode_hints") or []
            signals_txt = (
                ", ".join(str(x) for x in defect_signals[:12])
                if isinstance(defect_signals, list)
                else str(defect_signals)
            )
            hints_txt = (
                ", ".join(str(x) for x in failure_hints[:8])
                if isinstance(failure_hints, list)
                else str(failure_hints)
            )
            lines.extend([
                f"缺陷类型:{defect_type}",
                f"风险等级:{risk_level}",
                f"受热面:{affected}",
                f"可见缺陷信号:{signals_txt}",
            ])
            if hints_txt:
                lines.append(f"形貌线索:{hints_txt}")

        if scope_line:
            lines.append(f"已解析范围:{scope_line}")
        lines.append(f"检索意图:{prof.augmented_rag_intent}")
        return "\n".join(lines)

    @staticmethod
    def _extract_parsed_intent_snapshot(calls: list[AnalysisNL2SQLCall]) -> dict[str, Any]:
        for call in calls:
            intent = call.question_intent
            if isinstance(intent, dict) and intent:
                return intent
        return {}

    @staticmethod
    def _scope_from_intent(intent: dict[str, Any]) -> dict[str, Any]:
        scope = intent.get("scope")
        if isinstance(scope, dict):
            return scope
        return {}

    @staticmethod
    def split_parsed_intent_snapshot(
        intent: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """从 NL2SQL parsed_intent 拆出时间窗与范围，供 trace / 审计。"""
        if not intent:
            return {}, {}
        time_part = {
            "time_window": intent.get("time_window"),
            "time_window_tag": intent.get("time_window_tag"),
            "time_anchor": intent.get("time_anchor"),
            "time_anchor_tag": intent.get("time_anchor_tag"),
            "statistical_time_range": intent.get("statistical_time_range"),
            "scope_question": intent.get("scope_question"),
        }
        scope_raw = intent.get("scope")
        scope_part = dict(scope_raw) if isinstance(scope_raw, dict) else {}
        return time_part, scope_part

    @staticmethod
    def _unique_purpose_label(base: str, used: set[str]) -> str:
        label = base
        suffix = 2
        while label in used:
            label = f"{base}·{suffix}"
            suffix += 1
        used.add(label)
        return label

    @classmethod
    def _synthesis_catalog_rule(
        cls,
        *,
        row_count: int,
        rows_in_snapshot: int,
        time_scope: str,
    ) -> tuple[str, str]:
        if row_count <= 0:
            return (
                "empty",
                "库表未检索到任何记录；「库表要点」与三层溯源/判定依据中"
                "不得写支持性数值或管排号；仅可写「库表未检索到」并标注证据不足；"
                "知识库内容仅可出现在「知识库要点」小节，不得冒充库表事实。",
            )
        if time_scope == "historical_no_time_filter":
            truncated = rows_in_snapshot < row_count
            status = "historical_truncated" if truncated else "historical_full"
            rule = (
                "本主题为历史全量统计，非事故近3天窗口；"
                "禁止描述为「近3天」「近期」「事故前3天」；"
                "管排号、壁厚、速率等数值仅可引用 structured_queries_snapshot 中"
                "对应 purpose 的可见行，禁止归纳 snapshot 未出现的排号。"
            )
            if truncated:
                rule += f"（snapshot 仅含 {rows_in_snapshot}/{row_count} 行样本，勿外推全量）"
            return status, rule
        if rows_in_snapshot < row_count:
            return (
                "truncated",
                f"snapshot 含 {rows_in_snapshot}/{row_count} 行；"
                "仅可引用 snapshot 内出现的字段与数值，禁止外推。",
            )
        return (
            "ok",
            "仅可引用 structured_queries_snapshot 中本 purpose 可见行的字段与数值。",
        )

    @classmethod
    def _prepare_img_diag_synthesis_queries(
        cls,
        gathered_data: dict[str, list[dict]],
        plan_tasks: list[dict[str, Any]],
        calls: list[AnalysisNL2SQLCall],
        *,
        analysis_type: str,
    ) -> tuple[dict[str, list[dict]], list[dict[str, Any]]]:
        """合成输入：purpose 语义键 + 行数上限 + 可查 catalog（空表/历史全量约束）。"""
        if analysis_type not in (IMG_DIAG_DEFECT_IDENT_TYPE, IMG_DIAG_LEAKAGE_BURST_TYPE):
            return gathered_data, []
        id_to_purpose: dict[str, str] = {}
        plan_order: list[str] = []
        for task in plan_tasks:
            if not isinstance(task, dict):
                continue
            item_id = str(task.get("item_id") or "").strip()
            purpose = str(task.get("purpose") or "").strip()
            if item_id and purpose:
                id_to_purpose[item_id] = purpose
                plan_order.append(item_id)
        call_rows: dict[str, int] = {}
        for call in calls:
            item_id = str(call.item_id or "").strip()
            if item_id:
                call_rows[item_id] = int(call.row_count or 0)
        ordered_ids = plan_order + [
            k for k in gathered_data if k not in plan_order
        ]
        labeled: dict[str, list[dict]] = {}
        catalog: list[dict[str, Any]] = []
        used_labels: set[str] = set()
        for item_id in ordered_ids:
            rows = list(gathered_data.get(item_id) or [])
            purpose = id_to_purpose.get(item_id, "结构化查询数据")
            label = cls._unique_purpose_label(purpose, used_labels)
            row_count = call_rows.get(item_id, len(rows))
            cap = _IMG_DIAG_SYNTHESIS_ROW_CAPS.get(
                item_id, _IMG_DIAG_SYNTHESIS_DEFAULT_ROW_CAP
            )
            snapshot_rows = rows[:cap] if len(rows) > cap else rows
            labeled[label] = snapshot_rows
            time_scope = _IMG_DIAG_PLAN_TIME_SCOPE.get(item_id, "unknown")
            status, rule = cls._synthesis_catalog_rule(
                row_count=row_count,
                rows_in_snapshot=len(snapshot_rows),
                time_scope=time_scope,
            )
            catalog.append(
                {
                    "purpose": purpose,
                    "row_count": row_count,
                    "rows_in_snapshot": len(snapshot_rows),
                    "time_scope": time_scope,
                    "time_scope_label": _IMG_DIAG_TIME_SCOPE_LABELS.get(
                        time_scope, time_scope
                    ),
                    "synthesis_status": status,
                    "synthesis_rule": rule,
                }
            )
        return labeled, catalog

    @staticmethod
    def _gathered_data_for_synthesis(
        gathered_data: dict[str, list[dict]],
        plan_tasks: list[dict[str, Any]],
        *,
        analysis_type: str,
    ) -> dict[str, list[dict]]:
        """合成 snapshot（兼容旧调用；catalog 见 _prepare_img_diag_synthesis_queries）。"""
        snapshot, _ = AnalysisImgDiagGraphRunner._prepare_img_diag_synthesis_queries(
            gathered_data,
            plan_tasks,
            [],
            analysis_type=analysis_type,
        )
        return snapshot

    @staticmethod
    def _append_report_constraints(synthesis_prompt: str, report_prompt: str) -> str:
        report = (report_prompt or "").strip()
        if not report:
            return synthesis_prompt
        return f"{synthesis_prompt.rstrip()}\n\n【报告格式约束】\n{report}"

    @staticmethod
    def _vision_synthesis_log_summary(vision_data: Any) -> dict[str, Any]:
        """合成前日志：vision_findings 摘要（不含 raw 全文）。"""
        if not isinstance(vision_data, dict):
            return {"present": False}
        if vision_data.get("vision_skipped"):
            return {
                "present": True,
                "vision_skipped": True,
                "reason": vision_data.get("reason"),
            }
        highlight_keys = (
            "burst_type",
            "location_visual",
            "burst_signals",
            "damage_morphology",
            "opening_shape",
            "defect_type",
            "defect_orientation",
            "inspector_marking",
            "morphology_summary",
            "distribution_features",
            "surface_state",
            "preliminary_visual_conclusion",
            "risk_level",
            "confidence_notes",
        )
        summary: dict[str, Any] = {
            "present": True,
            "vision_skipped": False,
            "keys": [k for k in vision_data if k not in ("raw", "parse_error")][:24],
        }
        for key in highlight_keys:
            if key in vision_data and vision_data[key] is not None:
                val = vision_data[key]
                if isinstance(val, str) and len(val) > 200:
                    val = val[:200] + "…"
                summary[key] = val
        return summary

    def _log_vision_before_synthesis(self, pack: _ImgDiagPack) -> None:
        summary = self._vision_synthesis_log_summary(pack.vision_data)
        catalog = pack.merged_blob.get("structured_queries_catalog") or []
        empty_purposes = [
            c.get("purpose")
            for c in catalog
            if isinstance(c, dict) and int(c.get("row_count") or 0) <= 0
        ]
        logger.info(
            "img_diag synthesis vision_context subtype=%s request_id=%s user_id=%s session_id=%s %s",
            pack.profile.subtype,
            pack.request_id,
            pack.req.user_id,
            pack.req.session_id,
            json.dumps(summary, ensure_ascii=False, default=str),
        )
        if catalog:
            logger.info(
                "img_diag synthesis queries_catalog request_id=%s empty_purposes=%s catalog=%s",
                pack.request_id,
                empty_purposes,
                json.dumps(catalog, ensure_ascii=False, default=str),
            )

    def _build_summary_user_content(
        self,
        *,
        query: str,
        analysis_type: str,
        data_mode: str,
        data_blob: dict,
        context_snippets: list[str],
        planning_context: str | None = None,
    ) -> str:
        if analysis_type in (IMG_DIAG_DEFECT_IDENT_TYPE, IMG_DIAG_LEAKAGE_BURST_TYPE):
            return self._build_img_diag_summary_user_content(
                query=query,
                analysis_type=analysis_type,
                data_mode=data_mode,
                data_blob=data_blob,
                context_snippets=context_snippets,
                planning_context=planning_context,
            )
        return super()._build_summary_user_content(
            query=query,
            analysis_type=analysis_type,
            data_mode=data_mode,
            data_blob=data_blob,
            context_snippets=context_snippets,
            planning_context=planning_context,
        )

    def _build_img_diag_summary_user_content(
        self,
        *,
        query: str,
        analysis_type: str,
        data_mode: str,
        data_blob: dict,
        context_snippets: list[str],
        planning_context: str | None = None,
    ) -> str:
        """看图诊断 synthesis user 消息：vision_findings、catalog 完整保留，snapshot 按预算截断。"""
        max_chars = int(self._analysis_cfg.synthesis_gathered_json_max_chars)
        vision_data = data_blob.get("vision_findings")
        catalog = data_blob.get("structured_queries_catalog")
        rest_blob = {
            k: v
            for k, v in data_blob.items()
            if k not in ("vision_findings", "structured_queries_catalog")
        }
        vision_block = json.dumps(
            {"vision_findings": vision_data},
            ensure_ascii=False,
            default=self._json_fallback,
        )
        catalog_block = json.dumps(
            {"structured_queries_catalog": catalog},
            ensure_ascii=False,
            default=self._json_fallback,
        )
        vision_cap = min(6000, max(800, max_chars // 3))
        if len(vision_block) > vision_cap:
            vision_block = vision_block[:vision_cap]
        catalog_cap = min(4000, max(600, max_chars // 4))
        if len(catalog_block) > catalog_cap:
            catalog_block = catalog_block[:catalog_cap]
        rest_budget = max(500, max_chars - len(vision_block) - len(catalog_block) - 200)
        rest_preview = json.dumps(
            rest_blob,
            ensure_ascii=False,
            default=self._json_fallback,
        )[:rest_budget]
        rag_text = "\n".join(f"- {s}" for s in context_snippets[:8])
        pc = (planning_context or "").strip()
        planning_block = f"\n分阶段规划意图(结构化要点):\n{pc[:2000]}\n" if pc else ""
        vision_skipped = (
            isinstance(vision_data, dict) and bool(vision_data.get("vision_skipped"))
        )
        if vision_skipped:
            if analysis_type == IMG_DIAG_LEAKAGE_BURST_TYPE:
                vision_hint = (
                    "（未提供现场图片：须综合相关数据与知识库/历史案例撰写报告；"
                    "第一章结论摘要禁止编造爆口/泄漏形貌或图像可见特征；"
                    "正文禁止出现 RAG/库表 等技术词）"
                )
            else:
                vision_hint = "（vision_skipped=true，报告须说明未提供图片，不得编造图像证据）"
        else:
            vision_hint = (
                "（vision_skipped 不为 true 时，报告「图像可见」须引用下列字段，禁止写未提供/无图）"
            )
        catalog_hint = "（synthesis_status=empty 的主题禁止写相关数据支持性结论）"
        kb_block = (
            f"知识库参考片段（内部分析输入，正文禁止写 RAG/库表 等字样）:\n{rag_text}"
            if rag_text.strip()
            else "知识库参考片段: （无可用片段）"
        )
        return (
            f"分析类型: {analysis_type}\n"
            f"数据来源模式: {data_mode}\n"
            f"用户问题: {query}\n"
            f"{planning_block}"
            f"视觉结构化结果(JSON，合成时必须优先使用){vision_hint}:\n{vision_block}\n"
            f"相关数据查询目录(JSON，合成前必读；row_count=0 的主题正文省略，禁止用知识库冒充){catalog_hint}:\n{catalog_block}\n"
            f"相关数据明细 snapshot(JSON截断，数值/管排号仅可引用本块与目录允许范围):\n{rest_preview}\n"
            f"{kb_block}"
        ).strip()

    @staticmethod
    def _finished_meta_parsed_intent(
        pack: _ImgDiagPack,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        scope = pack.confirmed_scope_intent or pack.parsed_scope_intent or None
        time_part = pack.parsed_time_intent or None
        if scope:
            scope = {
                k: v
                for k, v in scope.items()
                if v is not None and (not isinstance(v, str) or v.strip())
            } or None
        if time_part:
            time_part = {
                k: v
                for k, v in time_part.items()
                if v is not None and (not isinstance(v, str) or str(v).strip())
            } or None
        return scope, time_part

    def _build_img_diag_finished_meta(
        self,
        pack: _ImgDiagPack,
        *,
        request_id: str,
        plan_id: str,
        start_ts: float,
        synthesis_ms: int,
        image_urls: list[str],
        stream_id: str | None = None,
        status: str = "completed",
        terminate_reason: str | None = None,
        is_partial: bool = False,
    ) -> dict[str, Any]:
        first_nl2sql_sql = next(
            (c.sql for c in pack.calls if c.status == "success" and (c.sql or "").strip()),
            None,
        )
        parsed_scope, parsed_time = self._finished_meta_parsed_intent(pack)
        return build_analysis_finished_meta(
            request_id=request_id,
            plan_id=plan_id,
            analysis_type=pack.profile.analysis_type,
            data_mode=pack.profile.data_mode,
            used_rag=pack.used_rag,
            used_plan_rag=pack.used_plan_rag,
            used_business_rag=pack.used_business_rag,
            rag_citations=pack.rag_citations,
            start_ts=start_ts,
            synthesis_ms=synthesis_ms,
            used_nl2sql=bool(pack.calls),
            nl2sql_sql=first_nl2sql_sql,
            processed_image_urls=image_urls,
            original_image_urls=image_urls,
            retrieval_attempts=int(pack.used_plan_rag) + int(pack.used_business_rag),
            rag_namespace=(
                str(pack.rag_citations[0].get("namespace") or "").strip() or None
                if pack.rag_citations
                else None
            ),
            parsed_scope_intent=parsed_scope,
            parsed_time_intent=parsed_time,
            stream_id=stream_id,
            status=status,
            terminate_reason=terminate_reason,
            is_partial=is_partial,
        )

    def _img_diag_stream_aborted_finished(
        self,
        *,
        pack: _ImgDiagPack | None,
        request_id: str,
        plan_id: str,
        analysis_type: str,
        data_mode: str,
        start_ts: float,
        stream_id: str | None,
        summary: str,
        synthesis_ms: int,
        image_urls: list[str],
    ) -> dict[str, Any]:
        if pack is not None:
            finished_meta = self._build_img_diag_finished_meta(
                pack,
                request_id=request_id,
                plan_id=plan_id,
                start_ts=start_ts,
                synthesis_ms=synthesis_ms,
                image_urls=image_urls,
                stream_id=stream_id,
                status="aborted",
                terminate_reason="user_cancelled",
                is_partial=bool(summary.strip()),
            )
        else:
            finished_meta = build_analysis_finished_meta(
                request_id=request_id,
                plan_id=plan_id or "",
                analysis_type=analysis_type,
                data_mode=data_mode,
                used_rag=False,
                used_plan_rag=False,
                used_business_rag=False,
                rag_citations=[],
                start_ts=start_ts,
                synthesis_ms=synthesis_ms,
                used_nl2sql=False,
                nl2sql_sql=None,
                processed_image_urls=image_urls,
                original_image_urls=image_urls,
                stream_id=stream_id,
                status="aborted",
                terminate_reason="user_cancelled",
                is_partial=bool(summary.strip()),
            )
        return analysis_finished_sse_event(finished_meta)

    @staticmethod
    def _merge_rag_text_lists(primary: list[str], secondary: list[str], *, limit: int = 24) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in list(primary) + list(secondary):
            text = (item or "").strip()
            if not text:
                continue
            key = text[:240]
            if key in seen:
                continue
            seen.add(key)
            out.append(text)
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def _merge_rag_source_lists(
        primary: list[dict[str, Any]],
        secondary: list[dict[str, Any]],
        *,
        limit: int = 64,
    ) -> list[dict[str, Any]]:
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for row in list(primary) + list(secondary):
            if not isinstance(row, dict):
                continue
            key = str(row.get("chunk_id") or row.get("doc_name") or json.dumps(row, ensure_ascii=False))[:240]
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
            if len(out) >= limit:
                break
        return out

    @classmethod
    def prefetch_business_rag_query(
        cls, req: AnalysisImgDiagRequest, profile: _ImgDiagSubtypeProfile | None = None
    ) -> str:
        """hybrid/parallel 预检 RAG：仅用户问题，不含视觉结论。"""
        prof = profile or cls._profile(req)
        return (
            f"{req.query.strip()}\n"
            f"场景:{prof.rag_scene_label}\n"
            f"检索意图:{prof.prefetch_rag_intent}"
        )

    async def _lane_business_rag_prefetch(
        self,
        req: AnalysisImgDiagRequest,
        profile: _ImgDiagSubtypeProfile,
    ) -> tuple[list[str], list[dict[str, Any]], list[Any], int, str, str]:
        if not req.options.enable_rag:
            return [], [], [], 0, "skipped", ""
        rag_query = self.prefetch_business_rag_query(req, profile)
        t0 = perf_counter()
        try:
            snippets, sources, chunks = await asyncio.to_thread(
                lambda: self._retrieve_business_rag(rag_query, profile.analysis_type)
            )
            ms = int((perf_counter() - t0) * 1000)
            return list(snippets), list(sources), list(chunks), ms, "success", rag_query
        except Exception as exc:  # noqa: BLE001
            logger.warning("img_diag %s prefetch rag failed: %s", profile.subtype, exc)
            return [], [], [], int((perf_counter() - t0) * 1000), "failed", rag_query

    async def _lane_vision(
        self, req: AnalysisImgDiagRequest, profile: _ImgDiagSubtypeProfile
    ) -> tuple[dict[str, Any], int]:
        url_diag = self._image_urls_diag(req.image_urls)
        urls = [u for u in (req.image_urls or []) if isinstance(u, str) and u.strip()]
        if not urls:
            logger.info(
                "img_diag vision skipped subtype=%s user_id=%s session_id=%s "
                "reason=no_image_provided url_count=0 raw_list_len=%s",
                profile.subtype,
                req.user_id,
                req.session_id,
                url_diag["raw_list_len"],
            )
            return (
                {
                    "vision_skipped": True,
                    "reason": "no_image_provided",
                    "notes": "未提供图片，泄爆分析将仅依据用户问题、库表与知识库推理。",
                },
                0,
            )
        t0 = perf_counter()
        original_urls = list(urls)
        urls = await self._get_vision_image_preprocessor().preprocess_urls(original_urls)
        image_preprocessed = urls != original_urls
        vision_model = get_app_config().llm.default_model
        instructions = self._build_vision_system_instructions(
            user_id=req.user_id,
            profile=profile,
        )
        user_text = self._vision_user_text(profile)
        messages = self._build_vision_llm_messages(
            system_instructions=instructions,
            user_text=user_text,
            image_urls=urls,
        )
        timeout = float(self._analysis_cfg.img_diag_vision_timeout_seconds)
        vision_temperature = float(self._analysis_cfg.img_diag_vision_temperature)
        logger.info(
            "img_diag vision start subtype=%s user_id=%s session_id=%s "
            "url_count=%s model=%s temperature=%s image_preprocessed=%s "
            "vision_user_query_len=%s business_query_ignored=True url_previews=%s",
            profile.subtype,
            req.user_id,
            req.session_id,
            len(urls),
            vision_model,
            vision_temperature,
            image_preprocessed,
            len(user_text),
            url_diag["url_previews"],
        )
        raw = await self._llm.chat(
            model=vision_model,  # type: ignore[arg-type]
            messages=messages,
            timeout=timeout,
            temperature=vision_temperature,
        )
        ms = int((perf_counter() - t0) * 1000)
        parsed = parse_vision_lane_llm_output(raw or "")
        parse_ok = isinstance(parsed, dict) and "parse_error" not in parsed
        logger.info(
            "img_diag vision done subtype=%s url_count=%s ms=%s parse_ok=%s "
            "has_narrative=%s result_keys=%s",
            profile.subtype,
            len(urls),
            ms,
            parse_ok,
            bool(isinstance(parsed, dict) and (parsed.get("vision_narrative") or "").strip()),
            list(parsed.keys())[:14] if isinstance(parsed, dict) else [],
        )
        return parsed, ms

    async def _img_diag_normalize_request(
        self,
        state: dict[str, Any],
        *,
        image_urls: list[str],
        request_id: str | None = None,
        persist_user_message: bool = True,
    ) -> dict[str, Any]:
        """写入 request_id/plan_id 与会话用户消息（含图片 URL 块，供 GET /chatbot/sessions/messages 解析）。"""
        req = AnalysisNL2SQLRequest.model_validate(state["nl2sql_request"])
        t0 = perf_counter()
        if persist_user_message:
            content = self._build_img_diag_user_message_content(
                AnalysisImgDiagRequest(
                    user_id=req.user_id,
                    session_id=req.session_id,
                    img_diag_subtype=(
                        "leakage_burst"
                        if req.analysis_type == IMG_DIAG_LEAKAGE_BURST_TYPE
                        else "defect_ident"
                    ),
                    query=req.query,
                    image_urls=list(image_urls or []),
                    options=req.options,
                )
            )
            self._conv.append_user_message(req.user_id, req.session_id, content)
        ms = int((perf_counter() - t0) * 1000)
        rid = (request_id or "").strip() or f"anl_{uuid4().hex[:12]}"
        return {
            "request_id": rid,
            "plan_id": f"plan_{uuid4().hex[:10]}",
            "user_id": req.user_id,
            "session_id": req.session_id,
            "analysis_type": req.analysis_type,
            "query": req.query,
            "options": req.options.model_dump(mode="json"),
            "degrade_reasons": [],
            "node_latency_ms": self._merge_latency(state, "normalize_request", ms),
            "node_status": self._merge_status(state, "normalize_request", "success"),
        }

    async def _lane_nl2sql_until_gate(
        self,
        nl_req: AnalysisNL2SQLRequest,
        *,
        image_urls: list[str] | None = None,
        request_id: str | None = None,
        cancel_checker: Callable[[], Awaitable[bool]] | None = None,
        persist_user_message: bool = True,
    ) -> dict[str, Any]:
        state: dict[str, Any] = {"nl2sql_request": nl_req.model_dump(mode="json")}
        await raise_if_stream_cancelled(cancel_checker)
        state.update(
            await self._img_diag_normalize_request(
                state,
                image_urls=list(image_urls or []),
                request_id=request_id,
                persist_user_message=persist_user_message,
            )
        )
        await raise_if_stream_cancelled(cancel_checker)
        state.update(await self._lg_nl2sql_plan_context_rag(state))
        await raise_if_stream_cancelled(cancel_checker)
        state.update(await self._lg_nl2sql_intent_llm(state))
        await raise_if_stream_cancelled(cancel_checker)
        state.update(await self._lg_nl2sql_plan_llm_merge(state))
        await raise_if_stream_cancelled(cancel_checker)
        req = AnalysisNL2SQLRequest.model_validate(state["nl2sql_request"])
        raw_tasks = list(state.get("plan_tasks") or [])
        tasks = [self._plan_task_from_dict(x) for x in raw_tasks if isinstance(x, dict)]
        analysis_request_id = str(state.get("request_id") or "").strip() or None
        plan_template_version = self._resolve_plan_template_version_label(req)
        nl2sql_calls, gathered_data, task_status, acquire_latency_ms = await self._execute_data_plan(
            req=req,
            tasks=tasks,
            analysis_request_id=analysis_request_id,
            plan_template_version=plan_template_version,
            cancel_checker=cancel_checker,
        )
        state.update(
            {
                "nl2sql_calls": [c.model_dump(mode="json") for c in nl2sql_calls],
                "gathered_data": gathered_data,
                "task_status": task_status,
                "acquire_latency_ms": acquire_latency_ms,
                "node_latency_ms": self._merge_latency(state, "acquire_data", acquire_latency_ms),
                "node_status": self._merge_status(state, "acquire_data", "success"),
            }
        )
        await raise_if_stream_cancelled(cancel_checker)
        state.update(await self._lg_nl2sql_data_quality_gate(state))
        return state

    async def _lane_business_rag(
        self,
        req: AnalysisImgDiagRequest,
        vision_data: dict[str, Any],
        profile: _ImgDiagSubtypeProfile,
        *,
        parsed_scope: dict[str, Any] | None = None,
    ) -> tuple[list[str], list[dict[str, Any]], list[Any], int, str, str]:
        if not req.options.enable_rag:
            return [], [], [], 0, "skipped", ""
        rag_query = self.business_rag_query(
            req, vision_data, parsed_scope=parsed_scope, profile=profile
        )
        t0 = perf_counter()
        try:
            snippets, sources, chunks = await asyncio.to_thread(
                lambda: self._retrieve_business_rag(rag_query, profile.analysis_type)
            )
            ms = int((perf_counter() - t0) * 1000)
            return list(snippets), list(sources), list(chunks), ms, "success", rag_query
        except Exception as exc:  # noqa: BLE001
            logger.warning("img_diag %s business rag failed: %s", profile.subtype, exc)
            return [], [], [], int((perf_counter() - t0) * 1000), "failed", rag_query

    async def _gather_img_diag_pack(
        self,
        req: AnalysisImgDiagRequest,
        *,
        confirmed_scope: dict[str, Any] | None = None,
        scope_intent_text: str | None = None,
        request_id: str | None = None,
        cancel_checker: Callable[[], Awaitable[bool]] | None = None,
        persist_user_message: bool = True,
        orchestrator_path: str = "scope_first",
        vision_prefetch: dict[str, Any] | None = None,
        vision_prefetch_ms: int = 0,
        vision_prefetch_status: str = "",
        skip_vision_lane: bool = False,
    ) -> _ImgDiagPack:
        await raise_if_stream_cancelled(cancel_checker)
        profile = self._profile(req)
        pack_url_diag = self._image_urls_diag(req.image_urls)
        logger.info(
            "img_diag gather_pack start subtype=%s request_id=%s scope_hitl_confirmed=%s "
            "orchestrator_path=%s skip_vision_lane=%s "
            "image_urls url_count=%s raw_list_len=%s url_previews=%s",
            profile.subtype,
            (request_id or "").strip() or "-",
            bool(confirmed_scope),
            orchestrator_path,
            skip_vision_lane,
            pack_url_diag["url_count"],
            pack_url_diag["raw_list_len"],
            pack_url_diag["url_previews"],
        )
        lane_timeout = float(self._analysis_cfg.img_diag_lane_timeout_seconds)
        at = profile.analysis_type
        nl_req = AnalysisNL2SQLRequest(
            user_id=req.user_id,
            session_id=req.session_id,
            analysis_type=at,
            query=req.query.strip(),
            data_requirements_hint=list(req.data_requirements_hint or []),
            options=req.options,
            confirmed_scope=confirmed_scope,
            scope_intent_text=scope_intent_text,
        )
        degrade: list[str] = []

        async def vision_safe() -> tuple[dict[str, Any], int, str]:
            try:
                data, ms = await asyncio.wait_for(
                    self._lane_vision(req, profile), timeout=lane_timeout
                )
                if data.get("vision_skipped"):
                    status = "skipped"
                else:
                    status = "success"
                logger.info(
                    "img_diag vision lane outcome subtype=%s status=%s ms=%s "
                    "skip_reason=%s lane_error=%s",
                    profile.subtype,
                    status,
                    ms,
                    data.get("reason"),
                    data.get("vision_lane_error"),
                )
                return data, ms, status
            except asyncio.TimeoutError:
                degrade.append("img_diag_vision_timeout")
                logger.warning("img_diag vision lane timeout after %ss", lane_timeout)
                return {"vision_lane_error": "timeout"}, int(lane_timeout * 1000), "timeout"
            except Exception as exc:  # noqa: BLE001
                degrade.append("img_diag_vision_failed")
                logger.warning("img_diag vision lane failed: %s", exc)
                return {"vision_lane_error": str(exc)}, 0, "failed"

        rag_mode = (self._analysis_cfg.img_diag_rag_mode or "vision_augmented").strip().lower()

        async def nl_safe() -> tuple[dict[str, Any], str]:
            try:
                st = await asyncio.wait_for(
                    self._lane_nl2sql_until_gate(
                        nl_req,
                        image_urls=req.image_urls,
                        request_id=request_id,
                        cancel_checker=cancel_checker,
                        persist_user_message=persist_user_message,
                    ),
                    timeout=lane_timeout,
                )
                return st, "success"
            except AnalysisStreamCancelled:
                raise
            except asyncio.TimeoutError:
                degrade.append("img_diag_nl2sql_timeout")
                logger.warning("img_diag nl2sql lane timeout after %ss", lane_timeout)
                return {
                    "nl2sql_calls": [],
                    "gathered_data": {},
                    "plan_tasks": [],
                    "plan_context": [],
                    "plan_rag_sources": [],
                    "quality_report": {"warnings": ["nl2sql lane timeout"]},
                    "task_status": {},
                    "node_latency_ms": {},
                    "planner_warnings": [],
                }, "timeout"
            except ValueError as exc:
                degrade.append(f"img_diag_nl2sql_blocked:{exc}")
                raise
            except Exception as exc:  # noqa: BLE001
                degrade.append("img_diag_nl2sql_failed")
                logger.exception("img_diag nl2sql lane failed")
                return {
                    "nl2sql_calls": [],
                    "gathered_data": {},
                    "plan_tasks": [],
                    "plan_context": [],
                    "plan_rag_sources": [],
                    "quality_report": {"warnings": [str(exc)]},
                    "task_status": {},
                    "node_latency_ms": {},
                    "planner_warnings": [str(exc)],
                }, "failed"

        async def prefetch_safe() -> tuple[list[str], list[dict[str, Any]], list[Any], int, str, str]:
            if not req.options.enable_rag or rag_mode not in ("hybrid", "parallel"):
                return [], [], [], 0, "skipped", ""
            try:
                return await asyncio.wait_for(
                    self._lane_business_rag_prefetch(req, profile),
                    timeout=lane_timeout,
                )
            except asyncio.TimeoutError:
                degrade.append("img_diag_business_rag_prefetch_timeout")
                return [], [], [], int(lane_timeout * 1000), "timeout", ""
            except Exception:  # noqa: BLE001
                degrade.append("img_diag_business_rag_prefetch_failed")
                return [], [], [], 0, "failed", ""

        if rag_mode in ("hybrid", "parallel"):
            if skip_vision_lane and isinstance(vision_prefetch, dict):
                nl_pack = await nl_safe()
                pf_pack = await prefetch_safe()
                v_pack = (
                    vision_prefetch,
                    int(vision_prefetch_ms or 0),
                    vision_prefetch_status or "success",
                )
            else:
                v_pack, nl_pack, pf_pack = await asyncio.gather(
                    vision_safe(), nl_safe(), prefetch_safe()
                )
        else:
            if skip_vision_lane and isinstance(vision_prefetch, dict):
                nl_pack = await nl_safe()
                pf_pack = ([], [], [], 0, "skipped", "")
                v_pack = (
                    vision_prefetch,
                    int(vision_prefetch_ms or 0),
                    vision_prefetch_status or "success",
                )
            else:
                v_pack, nl_pack = await asyncio.gather(vision_safe(), nl_safe())
                pf_pack = ([], [], [], 0, "skipped", "")

        await raise_if_stream_cancelled(cancel_checker)

        vision_data, vision_ms, vision_status = v_pack
        nl_state, nl_status = nl_pack
        pf_snippets, pf_sources, pf_chunks, pf_rag_ms, pf_rag_status, pf_rag_query = pf_pack

        calls_raw = list(nl_state.get("nl2sql_calls") or [])
        calls = [AnalysisNL2SQLCall.model_validate(x) for x in calls_raw if isinstance(x, dict)]
        parsed_intent_snapshot = self._extract_parsed_intent_snapshot(calls)
        if not parsed_intent_snapshot and confirmed_scope:
            parsed_intent_snapshot = {
                "parse_mode": "human_confirmed",
                "scope_question": scope_intent_text or req.query,
                "scope": {
                    "boiler": confirmed_scope.get("boiler"),
                    "device_name": confirmed_scope.get("device_name"),
                    "check_location_name": confirmed_scope.get("check_location_name"),
                    "row_no": confirmed_scope.get("row_no"),
                    "tube_no": confirmed_scope.get("tube_no"),
                },
                "time_window": confirmed_scope.get("time_window"),
                "time_anchor": confirmed_scope.get("time_anchor"),
            }
        parsed_time_intent, parsed_scope_intent = self.split_parsed_intent_snapshot(parsed_intent_snapshot)
        parsed_scope = parsed_scope_intent or self._scope_from_intent(parsed_intent_snapshot)
        if confirmed_scope:
            parsed_scope = dict(confirmed_scope)

        biz_snippets: list[str] = list(pf_snippets)
        biz_sources: list[dict[str, Any]] = list(pf_sources)
        biz_chunks: list[Any] = list(pf_chunks)
        rag_ms = pf_rag_ms
        rag_status = pf_rag_status
        rag_query = pf_rag_query
        aug_rag_query = ""

        if req.options.enable_rag and rag_mode in ("vision_augmented", "hybrid"):
            await raise_if_stream_cancelled(cancel_checker)
            try:
                r_pack = await asyncio.wait_for(
                    self._lane_business_rag(
                        req, vision_data, profile, parsed_scope=parsed_scope or None
                    ),
                    timeout=lane_timeout,
                )
                aug_snippets, aug_sources, aug_chunks, aug_ms, aug_status, aug_rag_query = r_pack
                rag_ms += aug_ms
                rag_status = aug_status if aug_status != "skipped" else rag_status
                biz_snippets = self._merge_rag_text_lists(biz_snippets, aug_snippets)
                biz_sources = self._merge_rag_source_lists(biz_sources, aug_sources)
                seen_chunk_ids: set[str] = set()
                merged_chunks: list[Any] = []
                for ch in list(biz_chunks) + list(aug_chunks):
                    cid = getattr(ch, "chunk_id", None) or str(ch)
                    if cid in seen_chunk_ids:
                        continue
                    seen_chunk_ids.add(str(cid))
                    merged_chunks.append(ch)
                biz_chunks = merged_chunks
                rag_query = aug_rag_query or rag_query
            except AnalysisStreamCancelled:
                raise
            except asyncio.TimeoutError:
                degrade.append("img_diag_business_rag_timeout")
                rag_status = "timeout"
                rag_ms += int(lane_timeout * 1000)
            except Exception as exc:  # noqa: BLE001
                degrade.append("img_diag_business_rag_failed")
                rag_status = "failed"
                logger.warning("img_diag business rag lane failed: %s", exc)
        elif req.options.enable_rag and rag_mode == "parallel":
            rag_query = pf_rag_query
            if pf_rag_status == "failed":
                degrade.append("img_diag_business_rag_parallel_failed")

        synthesis_query = self._effective_img_diag_synthesis_query(
            req,
            confirmed_scope=confirmed_scope,
            scope_intent_text=scope_intent_text,
        )

        parallel_trace = {
            "img_diag_subtype": profile.subtype,
            "scope_hitl_confirmed": bool(confirmed_scope),
            "scope_intent_text": (scope_intent_text[:500] if scope_intent_text else None),
            "synthesis_user_query": (synthesis_query[:500] if synthesis_query else None),
            "orchestrator_path": orchestrator_path,
            "vision_lane_reused": bool(skip_vision_lane and vision_prefetch),
            "vision_lane_ms": vision_ms,
            "vision_lane_status": vision_status,
            "nl2sql_lane_status": nl_status,
            "business_rag_prefetch_ms": pf_rag_ms,
            "business_rag_prefetch_status": pf_rag_status,
            "business_rag_lane_ms": rag_ms,
            "business_rag_lane_status": rag_status,
            "rag_depends_on_vision": rag_mode in ("vision_augmented", "hybrid"),
            "rag_query_mode": rag_mode,
            "orchestrator_topology": (
                "vision_nl_parallel_prefetch_then_serial_rag"
                if rag_mode == "hybrid"
                else (
                    "vision_nl_rag_parallel"
                    if rag_mode == "parallel"
                    else "vision_nl_parallel_then_serial_rag"
                )
            ),
            "rag_prefetch_query": (pf_rag_query[:500] if pf_rag_query else None),
            "rag_augmented_query": (aug_rag_query[:500] if aug_rag_query else None),
        }

        gathered_data = cast(dict[str, list[dict]], nl_state.get("gathered_data") or {})
        raw_tasks = list(nl_state.get("plan_tasks") or [])
        planned_calls = sum(1 for x in raw_tasks if isinstance(x, dict))
        plan_rag_sources = list(nl_state.get("plan_rag_sources") or [])
        plan_rag_chunks = list(nl_state.get("plan_rag_chunks") or [])
        quality_report = cast(dict[str, Any], nl_state.get("quality_report") or {})

        merged_rag_sources = (plan_rag_sources + biz_sources)[:64]
        used_business_rag = len(biz_snippets) > 0
        used_plan_rag = len(plan_rag_sources) > 0
        used_rag = used_business_rag or used_plan_rag
        rag_citations = self._build_analysis_rag_citations(
            plan_chunks=plan_rag_chunks if req.options.enable_rag else None,
            business_chunks=biz_chunks if req.options.enable_rag else None,
        )

        planning_ctx_parts = list(nl_state.get("plan_context") or [])
        if self._analysis_cfg.nl2sql_llm_planner_enabled:
            ir = nl_state.get("intent_llm_result")
            if isinstance(ir, dict):
                planning_ctx_parts.append(json.dumps(ir, ensure_ascii=False))
        if parsed_intent_snapshot:
            planning_ctx_parts.append(
                json.dumps({"parsed_intent": parsed_intent_snapshot}, ensure_ascii=False)
            )
        planning_ctx = "\n".join(planning_ctx_parts)[:4000] if planning_ctx_parts else None

        synthesis_prompt, synthesis_version = self._resolve_stage_template(
            stage="analysis_synthesis",
            analysis_type=at,
            user_id=req.user_id,
            default_text=profile.synthesis_default,
        )
        report_prompt, report_version = self._resolve_stage_template(
            stage="analysis_report",
            analysis_type=at,
            user_id=req.user_id,
            default_text=profile.report_default,
        )
        synthesis_prompt = self._append_report_constraints(synthesis_prompt, report_prompt)

        synthesis_data, queries_catalog = self._prepare_img_diag_synthesis_queries(
            gathered_data,
            [t for t in raw_tasks if isinstance(t, dict)],
            calls,
            analysis_type=at,
        )

        merged_blob: dict[str, Any] = {
            "vision_findings": vision_data,
            "structured_queries_catalog": queries_catalog,
            "structured_queries_snapshot": synthesis_data,
            "parsed_intent": parsed_intent_snapshot,
            "parsed_time_intent": parsed_time_intent,
            "parsed_scope_intent": parsed_scope_intent,
            "confirmed_scope_intent": confirmed_scope,
            "user_query": synthesis_query,
            "img_diag_subtype": profile.subtype,
            "quality_report": quality_report,
        }
        original_query = (req.query or "").strip()
        if confirmed_scope and synthesis_query != original_query:
            merged_blob["original_user_query"] = original_query

        rid = str(nl_state.get("request_id") or request_id or f"anl_{uuid4().hex[:12]}")
        plan_id = str(nl_state.get("plan_id") or f"plan_{uuid4().hex[:10]}")

        return _ImgDiagPack(
            req=req,
            degrade=degrade,
            parallel_trace=parallel_trace,
            vision_data=vision_data,
            vision_ms=vision_ms,
            vision_status=vision_status,
            biz_snippets=biz_snippets,
            biz_sources=biz_sources,
            rag_ms=rag_ms,
            rag_status=rag_status,
            rag_query=rag_query,
            nl_state=nl_state,
            nl_status=nl_status,
            calls=calls,
            gathered_data=gathered_data,
            planned_calls=planned_calls,
            plan_rag_sources=plan_rag_sources,
            quality_report=quality_report,
            merged_rag_sources=merged_rag_sources,
            rag_citations=rag_citations,
            used_rag=used_rag,
            used_plan_rag=used_plan_rag,
            used_business_rag=used_business_rag,
            planning_ctx=planning_ctx,
            synthesis_prompt=synthesis_prompt,
            synthesis_version=synthesis_version,
            report_version=report_version,
            merged_blob=merged_blob,
            parsed_intent_snapshot=parsed_intent_snapshot,
            parsed_time_intent=parsed_time_intent,
            parsed_scope_intent=parsed_scope_intent,
            confirmed_scope_intent=confirmed_scope,
            profile=profile,
            request_id=rid,
            plan_id=plan_id,
        )

    def _finalize_img_diag_v2(self, pack: _ImgDiagPack, summary: str, syn_ms: int) -> AnalysisV2Result:
        req = pack.req
        profile = pack.profile
        at = profile.analysis_type
        data_mode = profile.data_mode
        suggestions = self._build_suggestions(summary, at, req.options.max_suggestions)
        data_cov = {
            "mode": data_mode,
            "img_diag_subtype": profile.subtype,
            "planned_calls": pack.planned_calls,
            "success_calls": sum(1 for c in pack.calls if c.status == "success"),
            "failed_calls": sum(1 for c in pack.calls if c.status == "failed"),
            "skipped_calls": sum(1 for c in pack.calls if c.status == "skipped"),
            "records": self._extract_records_from_gathered(pack.gathered_data),
            "data_quality_report": pack.quality_report,
            "parallel_lane_trace": pack.parallel_trace,
            "parsed_intent": pack.parsed_intent_snapshot,
            "parsed_time_intent": pack.parsed_time_intent,
            "parsed_scope_intent": pack.parsed_scope_intent,
            "confirmed_scope_intent": pack.confirmed_scope_intent,
            "rag_query": pack.rag_query[:2000] if pack.rag_query else None,
        }
        structured_report = self._build_structured_report(
            summary=summary,
            suggestions=suggestions,
            analysis_type=at,
            report_style=req.options.report_style,
            report_template=req.options.report_template,
            chart_mode=req.options.chart_mode,
            data_coverage=data_cov,
        )
        structured_report["vision_findings"] = pack.vision_data
        structured_report["parsed_intent"] = pack.parsed_intent_snapshot
        structured_report["parsed_time_intent"] = pack.parsed_time_intent
        structured_report["parsed_scope_intent"] = pack.parsed_scope_intent
        structured_report["confirmed_scope_intent"] = pack.confirmed_scope_intent
        structured_report["user_query"] = self._synthesis_query_from_pack(pack)
        original_user_query = pack.merged_blob.get("original_user_query")
        if isinstance(original_user_query, str) and original_user_query.strip():
            structured_report["original_user_query"] = original_user_query.strip()
        structured_report["img_diag_subtype"] = profile.subtype

        nl_state = pack.nl_state
        node_ms = dict(nl_state.get("node_latency_ms") or {})
        node_ms["vision_understanding_parallel"] = pack.vision_ms
        node_ms["business_rag_serial"] = pack.rag_ms
        node_ms["synthesis"] = syn_ms

        node_status = dict(nl_state.get("node_status") or {})
        node_status["vision_understanding_parallel"] = pack.vision_status
        node_status["business_rag_serial"] = pack.rag_status

        self._conv.append_assistant_message(
            req.user_id,
            req.session_id,
            summary,
            rag_citations=pack.rag_citations or None,
        )

        evidence = AnalysisEvidence(
            used_rag=pack.used_rag,
            rag_sources=pack.merged_rag_sources,
            rag_citations=pack.rag_citations,
            nl2sql_calls=pack.calls,
            data_coverage=data_cov,
            vision_findings=pack.vision_data,
        )

        trace = AnalysisTrace(
            plan_id=pack.plan_id,
            node_latency_ms=node_ms,
            template_versions={
                "intent": str(nl_state.get("intent_version") or ""),
                "data_plan": str(nl_state.get("data_plan_version") or ""),
                "synthesis": pack.synthesis_version,
                "report": pack.report_version,
            },
            execution_summary={
                "analysis_type": at,
                "data_mode": data_mode,
                "img_diag_subtype": profile.subtype,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "used_rag": pack.used_rag,
                "planned_calls": pack.planned_calls,
                "orchestrator": "scope_hitl_then_vision_nl_parallel_then_serial_rag"
                if pack.confirmed_scope_intent
                else "vision_nl_parallel_then_serial_rag",
                "graph_nodes": (
                    [
                        "scope_preflight_llm",
                        "scope_human_confirm",
                        "scope_db_validate",
                        "normalize_request",
                        "parallel_vision_nl2sql",
                        "serial_business_rag",
                        "synthesis",
                        "finalize",
                    ]
                    if pack.confirmed_scope_intent
                    else [
                        "normalize_request",
                        "parallel_vision_nl2sql",
                        "serial_business_rag",
                        "synthesis",
                        "finalize",
                    ]
                ),
                "parallel_lane_trace": pack.parallel_trace,
                "parsed_intent": pack.parsed_intent_snapshot,
                "parsed_time_intent": pack.parsed_time_intent,
                "parsed_scope_intent": pack.parsed_scope_intent,
                "planner_warnings": [
                    w for w in (nl_state.get("planner_warnings") or []) if isinstance(w, str)
                ],
            },
            node_status=node_status,
            data_plan_trace=[
                {
                    "item_id": c.item_id,
                    "purpose": c.purpose,
                    "status": c.status,
                    "row_count": c.row_count,
                }
                for c in pack.calls
            ],
            degrade_reasons=sorted(set(pack.degrade)),
        )

        return AnalysisV2Result(
            request_id=pack.request_id,
            analysis_type=at,
            summary=summary,
            structured_report=structured_report,
            evidence=evidence,
            trace=trace,
        )

    async def run_with_img_diag(self, req: AnalysisImgDiagRequest) -> AnalysisV2Result:
        profile = self._profile(req)
        at = profile.analysis_type
        data_mode = profile.data_mode
        ANALYSIS_REQUEST_COUNT.labels(
            analysis_type=at,
            data_mode=data_mode,
            status="started",
        ).inc()
        try:
            self._persist_img_diag_initial_user_message(req)
            scope_result, orchestrator_path, vision_prefetch, vision_ms, vision_status = (
                await self._probe_and_run_scope_hitl_phase(req)
            )
            if scope_result.get("status") == "interrupt":
                intr = scope_result.get("interrupt_payload") or {}
                if orchestrator_path == "vision_first" and intr.get("include_vision_preview"):
                    self._persist_vision_preview_assistant_message(
                        user_id=req.user_id,
                        session_id=req.session_id,
                        vision_data=vision_prefetch,
                        img_diag_subtype=req.img_diag_subtype,
                    )
                if intr.get("include_scope_confirm_preview", True):
                    self._persist_scope_hitl_assistant_message(
                        user_id=req.user_id,
                        session_id=req.session_id,
                        interrupt_payload=scope_result.get("interrupt_payload"),
                    )
                raise ImgDiagScopeInterrupt(self._scope_interrupt_sse_event(scope_result))
            if scope_result.get("status") == "error":
                raise ValueError(scope_result.get("message") or "scope hitl failed")
            self._maybe_persist_scope_confirmed_vision_preview(
                scope_result=scope_result,
                orchestrator_path=orchestrator_path,
                vision_prefetch=vision_prefetch if isinstance(vision_prefetch, dict) else None,
                user_id=req.user_id,
                session_id=req.session_id,
                img_diag_subtype=req.img_diag_subtype,
            )
            confirmed = (
                scope_result.get("confirmed_scope_intent")
                if scope_result.get("status") == "confirmed"
                else None
            )
            scope_text = (
                scope_result.get("scope_intent_text")
                if scope_result.get("status") == "confirmed"
                else None
            )
            rid = scope_result.get("request_id")
            skip_vision = (
                orchestrator_path == "vision_first" and isinstance(vision_prefetch, dict)
            )
            pack = await self._gather_img_diag_pack(
                req,
                confirmed_scope=confirmed,
                scope_intent_text=scope_text,
                request_id=rid,
                persist_user_message=False,
                orchestrator_path=orchestrator_path,
                vision_prefetch=vision_prefetch,
                vision_prefetch_ms=vision_ms,
                vision_prefetch_status=vision_status,
                skip_vision_lane=skip_vision,
            )
            t_syn = perf_counter()
            self._log_vision_before_synthesis(pack)
            summary = await self._generate_summary(
                query=self._synthesis_query_from_pack(pack),
                analysis_type=at,
                data_mode=data_mode,
                data_blob=pack.merged_blob,
                context_snippets=pack.biz_snippets,
                system_prompt=pack.synthesis_prompt,
                planning_context=pack.planning_ctx,
            )
            syn_ms = int((perf_counter() - t_syn) * 1000)
            result = self._finalize_img_diag_v2(pack, summary, syn_ms)
            ANALYSIS_REQUEST_COUNT.labels(
                analysis_type=at,
                data_mode=data_mode,
                status="success",
            ).inc()
            return result
        except Exception:
            ANALYSIS_REQUEST_COUNT.labels(
                analysis_type=at,
                data_mode=data_mode,
                status="failed",
            ).inc()
            raise

    async def _emit_img_diag_stream_after_gather(
        self,
        *,
        req: AnalysisImgDiagRequest,
        profile: _ImgDiagSubtypeProfile,
        pack: _ImgDiagPack,
        t_pipeline: float,
        stream_id: str | None,
        cancel_checker: Callable[[], Awaitable[bool]] | None,
        on_complete: Callable[[AnalysisV2Result], Awaitable[None]] | None,
    ) -> AsyncIterator[dict[str, Any]]:
        """gather 完成后：meta → synthesis 流 → finished（或用户中断 aborted）。"""
        at = profile.analysis_type
        data_mode = profile.data_mode
        request_id = pack.request_id
        plan_id = pack.plan_id

        yield {
            "event": "meta",
            "request_id": request_id,
            "plan_id": plan_id,
            "analysis_type": at,
            "data_mode": data_mode,
            "img_diag_subtype": profile.subtype,
            "orchestrator": profile.orchestrator_stream_id,
            "stream_id": stream_id,
            "template_versions": {
                "synthesis": pack.synthesis_version,
                "report": pack.report_version,
            },
        }

        t_syn = perf_counter()
        self._log_vision_before_synthesis(pack)
        parts: list[str] = []
        user_cancelled = False
        try:
            async for chunk in self._stream_summary_text(
                query=self._synthesis_query_from_pack(pack),
                analysis_type=at,
                data_mode=data_mode,
                data_blob=pack.merged_blob,
                context_snippets=pack.biz_snippets,
                system_prompt=pack.synthesis_prompt,
                planning_context=pack.planning_ctx,
            ):
                if await is_stream_cancelled(cancel_checker):
                    user_cancelled = True
                    break
                clean_chunk = _sanitize_img_diag_report_text(chunk)
                parts.append(clean_chunk)
                yield {"event": "summary_delta", "text": clean_chunk}
        except Exception:  # noqa: BLE001
            logger.exception("analysis img_diag %s stream summary failed", profile.subtype)
            fb = profile.stream_fallback_summary
            parts = [fb]
            yield {"event": "summary_delta", "text": fb}

        summary = "".join(parts)
        synthesis_ms = int((perf_counter() - t_syn) * 1000)
        yield {
            "event": "summary_complete",
            "request_id": request_id,
            "chars": len(summary),
            "synthesis_ms": synthesis_ms,
            "partial": user_cancelled,
        }

        image_urls = [u for u in (req.image_urls or []) if isinstance(u, str) and u.strip()]

        if user_cancelled:
            # Match NL2SQL abort: persist streamed partial report; skip empty.
            if summary.strip():
                self._persist_assistant_summary(
                    req.user_id,
                    req.session_id,
                    summary,
                    pack.rag_citations,
                )
            yield self._img_diag_stream_aborted_finished(
                pack=pack,
                request_id=request_id,
                plan_id=plan_id,
                analysis_type=at,
                data_mode=data_mode,
                start_ts=t_pipeline,
                stream_id=stream_id,
                summary=summary,
                synthesis_ms=synthesis_ms,
                image_urls=image_urls,
            )
            ANALYSIS_REQUEST_COUNT.labels(
                analysis_type=at,
                data_mode=data_mode,
                status="aborted",
            ).inc()
            return

        asyncio.create_task(
            self._img_diag_stream_background_finalize(
                pack,
                summary=summary,
                synthesis_ms=synthesis_ms,
                on_complete=on_complete,
            )
        )
        yield {"event": "structured_async_enqueued", "request_id": request_id}

        finished_meta = self._build_img_diag_finished_meta(
            pack,
            request_id=request_id,
            plan_id=plan_id,
            start_ts=t_pipeline,
            synthesis_ms=synthesis_ms,
            image_urls=image_urls,
            stream_id=stream_id,
        )
        yield analysis_finished_sse_event(finished_meta)

        ANALYSIS_REQUEST_COUNT.labels(
            analysis_type=at,
            data_mode=data_mode,
            status="success",
        ).inc()

    async def iter_img_diag_stream_events(
        self,
        req: AnalysisImgDiagRequest,
        *,
        on_complete: Callable[[AnalysisV2Result], Awaitable[None]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """看图诊断：并行臂 + 串行 RAG 后 SSE 流式 synthesis（首帧 started 含 stream_id）。"""
        profile = self._profile(req)
        at = profile.analysis_type
        data_mode = profile.data_mode
        stream_id: str | None = None
        stream_request_id = f"anl_{uuid4().hex[:12]}"
        ANALYSIS_REQUEST_COUNT.labels(
            analysis_type=at,
            data_mode=data_mode,
            status="started",
        ).inc()
        try:
            if self._stream_ctrl is not None:
                stream_id = self._stream_ctrl.begin_stream(req.user_id, req.session_id)
            cancel_checker = self._build_stream_cancel_checker(
                req.user_id, req.session_id, stream_id
            )
            yield {
                "event": "started",
                "stream_id": stream_id or stream_request_id,
                "request_id": stream_request_id,
            }

            t_pipeline = perf_counter()
            image_urls = [u for u in (req.image_urls or []) if isinstance(u, str) and u.strip()]
            self._persist_img_diag_initial_user_message(req)

            try:
                await raise_if_stream_cancelled(cancel_checker)
                (
                    scope_result,
                    orchestrator_path,
                    vision_prefetch,
                    vision_ms,
                    vision_status,
                ) = await self._probe_and_run_scope_hitl_phase(
                    req,
                    request_id=stream_request_id,
                    cancel_checker=cancel_checker,
                )
            except AnalysisStreamCancelled:
                yield self._img_diag_stream_aborted_finished(
                    pack=None,
                    request_id=stream_request_id,
                    plan_id="",
                    analysis_type=at,
                    data_mode=data_mode,
                    start_ts=t_pipeline,
                    stream_id=stream_id,
                    summary="",
                    synthesis_ms=0,
                    image_urls=image_urls,
                )
                ANALYSIS_REQUEST_COUNT.labels(
                    analysis_type=at,
                    data_mode=data_mode,
                    status="aborted",
                ).inc()
                return

            if scope_result.get("status") == "interrupt":
                intr = scope_result.get("interrupt_payload") or {}
                if orchestrator_path == "vision_first" and intr.get("include_vision_preview"):
                    self._persist_vision_preview_assistant_message(
                        user_id=req.user_id,
                        session_id=req.session_id,
                        vision_data=vision_prefetch,
                        img_diag_subtype=req.img_diag_subtype,
                    )
                    yield self._vision_preview_sse_event(
                        request_id=str(scope_result.get("request_id") or stream_request_id),
                        img_diag_subtype=req.img_diag_subtype,
                        vision_data=vision_prefetch,
                        vision_ms=vision_ms,
                        vision_status=vision_status,
                        hitl_mode=str(intr.get("hitl_mode") or "") or None,
                        ui_buttons=intr.get("ui_buttons")
                        if isinstance(intr.get("ui_buttons"), list)
                        else None,
                    )
                if intr.get("include_scope_confirm_preview", True):
                    self._persist_scope_hitl_assistant_message(
                        user_id=req.user_id,
                        session_id=req.session_id,
                        interrupt_payload=scope_result.get("interrupt_payload"),
                    )
                yield self._scope_interrupt_sse_event(scope_result)
                return
            if scope_result.get("status") == "error":
                raise ValueError(scope_result.get("message") or "scope hitl failed")

            if self._maybe_persist_scope_confirmed_vision_preview(
                scope_result=scope_result,
                orchestrator_path=orchestrator_path,
                vision_prefetch=vision_prefetch if isinstance(vision_prefetch, dict) else None,
                user_id=req.user_id,
                session_id=req.session_id,
                img_diag_subtype=req.img_diag_subtype,
            ):
                yield self._vision_preview_sse_event(
                    request_id=str(scope_result.get("request_id") or stream_request_id),
                    img_diag_subtype=req.img_diag_subtype,
                    vision_data=vision_prefetch,
                    vision_ms=vision_ms,
                    vision_status=vision_status,
                )

            confirmed = (
                scope_result.get("confirmed_scope_intent")
                if scope_result.get("status") == "confirmed"
                else None
            )
            scope_text = (
                scope_result.get("scope_intent_text")
                if scope_result.get("status") == "confirmed"
                else None
            )
            rid = scope_result.get("request_id") or stream_request_id
            skip_vision = (
                orchestrator_path == "vision_first" and isinstance(vision_prefetch, dict)
            )

            try:
                await raise_if_stream_cancelled(cancel_checker)
                pack = await self._gather_img_diag_pack(
                    req,
                    confirmed_scope=confirmed,
                    scope_intent_text=scope_text,
                    request_id=rid,
                    cancel_checker=cancel_checker,
                    persist_user_message=False,
                    orchestrator_path=orchestrator_path,
                    vision_prefetch=vision_prefetch,
                    vision_prefetch_ms=vision_ms,
                    vision_prefetch_status=vision_status,
                    skip_vision_lane=skip_vision,
                )
            except AnalysisStreamCancelled:
                yield self._img_diag_stream_aborted_finished(
                    pack=None,
                    request_id=rid,
                    plan_id="",
                    analysis_type=at,
                    data_mode=data_mode,
                    start_ts=t_pipeline,
                    stream_id=stream_id,
                    summary="",
                    synthesis_ms=0,
                    image_urls=image_urls,
                )
                ANALYSIS_REQUEST_COUNT.labels(
                    analysis_type=at,
                    data_mode=data_mode,
                    status="aborted",
                ).inc()
                return

            async for ev in self._emit_img_diag_stream_after_gather(
                req=req,
                profile=profile,
                pack=pack,
                t_pipeline=t_pipeline,
                stream_id=stream_id,
                cancel_checker=cancel_checker,
                on_complete=on_complete,
            ):
                yield ev
        except Exception:
            ANALYSIS_REQUEST_COUNT.labels(
                analysis_type=at,
                data_mode=data_mode,
                status="failed",
            ).inc()
            raise
        finally:
            if stream_id and self._stream_ctrl is not None:
                await self._stream_ctrl.clear_stream(req.user_id, req.session_id, stream_id)

    async def iter_img_diag_scope_resume_stream_events(
        self,
        *,
        resume_token: str,
        user_id: str,
        session_id: str,
        action: str,
        payload: dict[str, Any] | None = None,
        on_complete: Callable[[AnalysisV2Result], Awaitable[None]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """scope HITL resume 后继续看图诊断主流（started → meta → synthesis → finished）。"""
        stream_id: str | None = None
        stream_request_id = f"anl_{uuid4().hex[:12]}"
        token_preview = (
            f"{resume_token[:20]}..." if len(resume_token) > 20 else resume_token
        )
        logger.info(
            "img_diag scope resume start user_id=%s session_id=%s action=%s resume_token=%s",
            user_id,
            session_id,
            action,
            token_preview,
        )
        try:
            if self._stream_ctrl is not None:
                stream_id = self._stream_ctrl.begin_stream(user_id, session_id)
            cancel_checker = self._build_stream_cancel_checker(user_id, session_id, stream_id)
            yield {
                "event": "started",
                "stream_id": stream_id or stream_request_id,
                "request_id": stream_request_id,
            }

            t_pipeline = perf_counter()
            self._persist_scope_hitl_user_message(
                user_id=user_id,
                session_id=session_id,
                action=action,
                payload=payload,
            )
            runner = self._get_scope_hitl_runner()
            try:
                await raise_if_stream_cancelled(cancel_checker)
                scope_result = await runner.resume_until_confirmed_or_interrupt(
                    resume_token=resume_token,
                    user_id=user_id,
                    session_id=session_id,
                    action=action,
                    payload=payload,
                    vision_refresh=self._refresh_vision_for_hitl_request,
                )
            except AnalysisStreamCancelled:
                yield self._img_diag_stream_aborted_finished(
                    pack=None,
                    request_id=stream_request_id,
                    plan_id="",
                    analysis_type="img_diag_defect_ident",
                    data_mode="img_diag_defect_ident",
                    start_ts=t_pipeline,
                    stream_id=stream_id,
                    summary="",
                    synthesis_ms=0,
                    image_urls=[],
                )
                return

            logger.info(
                "img_diag scope resume phase done status=%s request_id=%s",
                scope_result.get("status"),
                scope_result.get("request_id"),
            )
            if scope_result.get("status") == "interrupt":
                intr = scope_result.get("interrupt_payload") or {}
                req_dict = scope_result.get("img_diag_request") or {}
                subtype = str(
                    req_dict.get("img_diag_subtype")
                    if isinstance(req_dict, dict)
                    else "defect_ident"
                )
                if intr.get("include_vision_preview"):
                    vision_data = scope_result.get("vision_prefetch")
                    if isinstance(vision_data, dict) and vision_data:
                        self._persist_vision_preview_assistant_message(
                            user_id=user_id,
                            session_id=session_id,
                            vision_data=vision_data,
                            img_diag_subtype=subtype,
                        )
                        yield self._vision_preview_sse_event(
                            request_id=str(scope_result.get("request_id") or stream_request_id),
                            img_diag_subtype=subtype,
                            vision_data=vision_data,
                            vision_ms=int(scope_result.get("vision_prefetch_ms") or 0),
                            vision_status=str(scope_result.get("vision_prefetch_status") or ""),
                            hitl_mode=str(intr.get("hitl_mode") or "") or None,
                            ui_buttons=intr.get("ui_buttons")
                            if isinstance(intr.get("ui_buttons"), list)
                            else None,
                        )
                if intr.get("include_scope_confirm_preview", True):
                    self._persist_scope_hitl_assistant_message(
                        user_id=user_id,
                        session_id=session_id,
                        interrupt_payload=intr,
                    )
                yield self._scope_interrupt_sse_event(scope_result)
                return
            if scope_result.get("status") == "error":
                yield {
                    "event": "img_diag_error",
                    "message": scope_result.get("message") or "scope resume failed",
                }
                return
            req_dict = scope_result.get("img_diag_request") or {}
            dict_url_diag = self._image_urls_diag(
                req_dict.get("image_urls") if isinstance(req_dict, dict) else None
            )
            req = AnalysisImgDiagRequest.model_validate(req_dict)
            req_url_diag = self._image_urls_diag(req.image_urls)
            logger.info(
                "img_diag scope resume restored img_diag_request request_id=%s "
                "dict_url_count=%s validated_url_count=%s raw_dict_list_len=%s url_previews=%s",
                scope_result.get("request_id"),
                dict_url_diag["url_count"],
                req_url_diag["url_count"],
                dict_url_diag["raw_list_len"],
                req_url_diag["url_previews"],
            )
            profile = self._profile(req)
            at = profile.analysis_type
            data_mode = profile.data_mode
            confirmed = scope_result.get("confirmed_scope_intent")
            scope_text = scope_result.get("scope_intent_text")
            rid = scope_result.get("request_id") or stream_request_id
            image_urls = [u for u in (req.image_urls or []) if isinstance(u, str) and u.strip()]
            orchestrator_path = str(scope_result.get("orchestrator_path") or "scope_first")
            vision_prefetch = scope_result.get("vision_prefetch")
            vision_ms = int(scope_result.get("vision_prefetch_ms") or 0)
            vision_status = str(scope_result.get("vision_prefetch_status") or "")
            skip_vision = (
                orchestrator_path == "vision_first" and isinstance(vision_prefetch, dict)
            )

            if self._maybe_persist_scope_confirmed_vision_preview(
                scope_result=scope_result,
                orchestrator_path=orchestrator_path,
                vision_prefetch=vision_prefetch if isinstance(vision_prefetch, dict) else None,
                user_id=user_id,
                session_id=session_id,
                img_diag_subtype=str(req.img_diag_subtype or "defect_ident"),
            ):
                yield self._vision_preview_sse_event(
                    request_id=str(scope_result.get("request_id") or stream_request_id),
                    img_diag_subtype=str(req.img_diag_subtype or "defect_ident"),
                    vision_data=vision_prefetch,
                    vision_ms=vision_ms,
                    vision_status=vision_status,
                )

            try:
                await raise_if_stream_cancelled(cancel_checker)
                pack = await self._gather_img_diag_pack(
                    req,
                    confirmed_scope=confirmed,
                    scope_intent_text=scope_text,
                    request_id=rid,
                    cancel_checker=cancel_checker,
                    persist_user_message=False,
                    orchestrator_path=orchestrator_path,
                    vision_prefetch=vision_prefetch,
                    vision_prefetch_ms=vision_ms,
                    vision_prefetch_status=vision_status,
                    skip_vision_lane=skip_vision,
                )
            except AnalysisStreamCancelled:
                yield self._img_diag_stream_aborted_finished(
                    pack=None,
                    request_id=rid,
                    plan_id="",
                    analysis_type=at,
                    data_mode=data_mode,
                    start_ts=t_pipeline,
                    stream_id=stream_id,
                    summary="",
                    synthesis_ms=0,
                    image_urls=image_urls,
                )
                ANALYSIS_REQUEST_COUNT.labels(
                    analysis_type=at,
                    data_mode=data_mode,
                    status="aborted",
                ).inc()
                return

            logger.info(
                "img_diag scope resume gather_pack done request_id=%s vision_lane_status=%s "
                "vision_skipped=%s vision_ms=%s",
                pack.request_id,
                pack.vision_status,
                bool(pack.vision_data.get("vision_skipped")),
                pack.vision_ms,
            )

            async for ev in self._emit_img_diag_stream_after_gather(
                req=req,
                profile=profile,
                pack=pack,
                t_pipeline=t_pipeline,
                stream_id=stream_id,
                cancel_checker=cancel_checker,
                on_complete=on_complete,
            ):
                yield ev
        finally:
            if stream_id and self._stream_ctrl is not None:
                await self._stream_ctrl.clear_stream(user_id, session_id, stream_id)

    async def _img_diag_stream_background_finalize(
        self,
        pack: _ImgDiagPack,
        *,
        summary: str,
        synthesis_ms: int,
        on_complete: Callable[[AnalysisV2Result], Awaitable[None]] | None,
    ) -> None:
        try:
            result = self._finalize_img_diag_v2(pack, summary, synthesis_ms)
            payload = result.model_dump(mode="json")
            dumped = json.dumps(payload, ensure_ascii=False)
            if len(dumped) > 16000:
                dumped = dumped[:16000] + "...(truncated)"
            logger.info(
                "analysis_img_diag_%s_stream_full_json request_id=%s json=%s",
                pack.profile.subtype,
                result.request_id,
                dumped,
            )
            await dispatch_analysis_nl2sql_stream_structured(payload)
            if on_complete is not None:
                await on_complete(result)
        except Exception:  # noqa: BLE001
            logger.exception(
                "analysis_img_diag_stream_background_finalize failed request_id=%s",
                pack.request_id,
            )
