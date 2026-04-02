from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class ChatMessage(BaseModel):
    role: str = Field(..., description="消息角色，例如 user/assistant/system")
    content: str = Field(..., description="消息内容")


class ChatRequest(BaseModel):
    user_id: str = Field(..., description="用户唯一标识")
    session_id: str = Field(..., description="会话唯一标识（由前端或调用方管理）")
    query: str = Field(..., description="用户本轮输入内容")
    image_urls: list[str] = Field(
        default_factory=list,
        description="本轮关联的图片 URL 列表（可选）。空字符串、仅空白、null 项会被丢弃；全空时等价于不传图，走纯文本。",
    )
    enable_rag: bool = Field(
        True,
        description=(
            "是否启用 RAG：为 true 时由 HybridRAGService 检索知识库（默认语义+关键词+RRF+重排，"
            "场景参数见 RAG_SCENE_CHATBOT_*）；为 false 则不调向量库"
        ),
    )
    enable_context: bool = Field(
        True,
        description=(
            "是否把历史对话拼进大模型请求。关闭则每轮独立、无法记住上文。"
            "多实例部署须配置 REDIS_URL，否则仅进程内内存、且多 worker 互不共享。"
        ),
    )

    @field_validator("image_urls", mode="before")
    @classmethod
    def normalize_image_urls(cls, v: Any) -> list[str]:
        """允许客户端传 [\"\"]、[null] 等；过滤后为空则不走 vLLM 多模态，避免 empty image 400。"""
        if v is None:
            return []
        if not isinstance(v, list):
            return []
        out: list[str] = []
        for item in v:
            if item is None:
                continue
            s = str(item).strip()
            if s:
                out.append(s)
        return out


class ChatResponse(BaseModel):
    answer: str = Field(..., description="助手回答全文")
    used_rag: bool = Field(..., description="本轮是否实际走了检索（无命中时也可能为 false）")
    context_snippets: list[str] = Field(
        default_factory=list,
        description="注入到提示词前的检索片段文本列表（与 /rag/query 的 snippets 同源业务数据，封装形态不同）",
    )

