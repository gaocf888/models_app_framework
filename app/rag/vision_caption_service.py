from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.llm.client import VLLMHttpClient
from app.llm.prompt_registry import PromptTemplateRegistry

logger = get_logger(__name__)

_DEFAULT_SYSTEM = """你是工业知识库图像理解助手。请用中文描述用户提供的图片/图纸，输出结构化纯文本（不要 Markdown 表格）。
须包含：1) 图类型（架构图/流程图/工程图/照片等）；2) 主要组件或实体列表；3) 关系或数据流向；4) 图例、标注与关键数值（若有）；5) 一行 50 字以内摘要。
若提供了邻近正文上下文，描述用语应与上下文业务术语一致。"""


class VisionCaptionService:
    def __init__(
        self,
        llm_client: VLLMHttpClient | None = None,
        prompt_registry: PromptTemplateRegistry | None = None,
    ) -> None:
        self._llm = llm_client or VLLMHttpClient(timeout=120.0)
        self._prompts = prompt_registry or PromptTemplateRegistry()
        self._cfg = get_app_config().rag.ingestion

    def _system_prompt(self) -> str:
        tpl = self._prompts.get_template(
            scene="rag_figure_caption",
            version=self._cfg.figure_caption_prompt_version,
        )
        if tpl and tpl.content.strip():
            return tpl.content.strip()
        return _DEFAULT_SYSTEM

    def caption_figure(self, image_url: str, *, context: str | None = None) -> str:
        blocks: list[dict[str, Any]] = []
        ctx = (context or "").strip()
        if ctx:
            blocks.append({"type": "text", "text": f"邻近正文上下文：\n{ctx}\n\n请描述下方图片。"})
        else:
            blocks.append({"type": "text", "text": "请描述下方图片。"})
        blocks.append({"type": "image_url", "image_url": {"url": image_url}})

        messages = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": blocks},
        ]
        try:
            return asyncio.run(
                self._llm.chat(
                    model="",
                    messages=messages,
                    max_tokens=self._cfg.figure_caption_max_tokens,
                    temperature=self._cfg.figure_caption_temperature,
                    timeout=120.0,
                )
            ).strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("VLM caption failed url=%s err=%s", image_url[:120], exc)
            raise
