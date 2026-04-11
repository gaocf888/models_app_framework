from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.conversation.ids import validate_session_id, validate_user_id


class ChatMessage(BaseModel):
    role: str = Field(..., description="消息角色，例如 user/assistant/system")
    content: str = Field(..., description="消息内容")


class ChatRequest(BaseModel):
    user_id: str = Field(
        ...,
        description="用户唯一标识（由调用方后台管理，与 Service API Key 配合使用）",
    )
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
    enable_fault_vision: bool | None = Field(
        None,
        description=(
            "是否允许在「故障域判定」中使用本轮图片（需 CHATBOT_SIMILAR_CASE_ENABLED 等总开关）。"
            "None：跟随服务端 CHATBOT_FAULT_VISION_ENABLED；False：本轮即使有 image_urls 也不送图判定；"
            "True：有图则多模态判定。"
        ),
    )

    @field_validator("user_id")
    @classmethod
    def _validate_user_id(cls, v: str) -> str:
        return validate_user_id(v)

    @field_validator("session_id")
    @classmethod
    def _validate_session_id(cls, v: str) -> str:
        return validate_session_id(v)

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


class SessionMessageItem(BaseModel):
    """会话单条消息（与 Redis/内存存储字段一致）。"""

    role: str = Field(..., description="user / assistant / system")
    content: str = Field(..., description="消息正文")
    ts: float | None = Field(None, description="写入时时间戳（秒，可能为空）")


class SessionMessagesResponse(BaseModel):
    ok: bool = Field(True, description="是否成功")
    user_id: str = Field(..., description="用户 ID")
    session_id: str = Field(..., description="会话 ID")
    count: int = Field(..., description="返回条数")
    messages: list[SessionMessageItem] = Field(default_factory=list, description="按时间顺序的消息列表")


class SessionDeleteResponse(BaseModel):
    ok: bool = Field(True, description="是否执行成功")
    user_id: str = Field(..., description="用户 ID")
    session_id: str = Field(..., description="会话 ID")


class SessionListItem(BaseModel):
    """会话列表单行（方案 B：索引 + 元数据）。"""

    session_id: str = Field(..., description="会话 ID")
    title: str = Field(..., description="展示用标题")
    title_source: str = Field(..., description="truncated | off；后续可扩展 llm")
    last_activity_at: int = Field(..., description="最近活跃时间（毫秒，与 Redis ZSET score 一致）")
    message_count: int = Field(0, description="会话内消息条数")


class SessionListResponse(BaseModel):
    ok: bool = Field(True, description="是否成功")
    user_id: str = Field(..., description="用户 ID")
    total: int = Field(..., description="总会话数（过滤幽灵键后，用于分页）")
    limit: int = Field(..., description="本页 limit")
    offset: int = Field(..., description="本页 offset")
    items: list[SessionListItem] = Field(default_factory=list, description="本会话列表")


class ChatResponse(BaseModel):
    answer: str = Field(..., description="助手回答全文")
    used_rag: bool = Field(..., description="本轮是否实际走了检索（无命中时也可能为 false）")
    context_snippets: list[str] = Field(
        default_factory=list,
        description="注入到提示词前的检索片段文本列表（与 /rag/query 的 snippets 同源业务数据，封装形态不同）",
    )

