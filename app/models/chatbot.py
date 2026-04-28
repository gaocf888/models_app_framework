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
    prompt_version: str | None = Field(
        None,
        description=(
            "客服 system 模板版本，对应 configs/prompts.yaml 中 chatbot 条目的 version。"
            "为空时使用服务端 CHATBOT_PROMPT_DEFAULT_VERSION（默认 boiler_v1）。"
        ),
    )

    @field_validator("prompt_version", mode="before")
    @classmethod
    def _normalize_prompt_version(cls, v: Any) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s if s else None
    enable_nl2sql_route: bool = Field(
        True,
        description=(
            "是否允许将「台账/检修/统计类」问句路由到 NL2SQL（意图 data_query）。"
            "关闭后此类问题也走向量 RAG。"
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
    image_urls: list[str] = Field(
        default_factory=list,
        description="该消息关联图片链接（默认展示用，优先 original_image_urls，兼容字段）",
    )
    original_image_urls: list[str] = Field(
        default_factory=list,
        description="用户原始传入图片链接（仅 user 消息可能有值）",
    )
    processed_image_urls: list[str] = Field(
        default_factory=list,
        description="预处理后用于模型推理的图片链接（仅 user 消息可能有值）",
    )
    ts: float | None = Field(None, description="写入时时间戳（秒，可能为空）")


class SessionMessagesResponse(BaseModel):
    ok: bool = Field(True, description="是否成功")
    user_id: str = Field(..., description="用户 ID")
    session_id: str = Field(..., description="会话 ID")
    title: str = Field(
        ...,
        description="会话展示标题，与 GET /chatbot/sessions 列表项 title 同源（CHATBOT_SESSION_TITLE_MODE 等）",
    )
    title_source: str = Field(
        ...,
        description="标题来源：truncated | off | user，与列表项 title_source 一致",
    )
    count: int = Field(..., description="返回条数")
    messages: list[SessionMessageItem] = Field(default_factory=list, description="按时间顺序的消息列表")


class SessionDeleteResponse(BaseModel):
    ok: bool = Field(True, description="是否执行成功")
    user_id: str = Field(..., description="用户 ID")
    session_id: str = Field(..., description="会话 ID")


class SessionTitlePatchRequest(BaseModel):
    """PATCH /sessions/title 请求体。"""

    title: str = Field(..., description="新展示标题（非空；超长按 CHATBOT_SESSION_TITLE_EDIT_MAX_RUNES 截断）")

    @field_validator("title", mode="before")
    @classmethod
    def _validate_title(cls, v: Any) -> str:
        from app.conversation.session_catalog import normalize_edited_title

        s = v if isinstance(v, str) else (str(v) if v is not None else "")
        try:
            return normalize_edited_title(s)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class SessionTitlePatchResponse(BaseModel):
    ok: bool = Field(True, description="是否成功")
    user_id: str = Field(..., description="用户 ID")
    session_id: str = Field(..., description="会话 ID")
    title: str = Field(..., description="写入后的展示标题（与 GET /sessions 列表一致）")
    title_source: str = Field(
        ...,
        description="修改后为 user（用户自定义）；与自动 truncated/off 区分",
    )


class SessionListItem(BaseModel):
    """会话列表单行（方案 B：索引 + 元数据）。"""

    session_id: str = Field(..., description="会话 ID")
    title: str = Field(..., description="展示用标题")
    title_source: str = Field(..., description="truncated | off | user（用户 PATCH 标题）；llm 预留")
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
    used_nl2sql: bool = Field(False, description="本轮是否走了 NL2SQL（结构化查库）分支")
    intent_label: str | None = Field(None, description="规则/图编排判定的意图标签（如 kb_qa、data_query、clarify）")
    suggested_questions: list[str] = Field(
        default_factory=list,
        description="回答后推荐的关联追问（流式结束 meta 中同名字段对齐）",
    )
    context_snippets: list[str] = Field(
        default_factory=list,
        description="注入到提示词前的检索片段文本列表（与 /rag/query 的 snippets 同源业务数据，封装形态不同）",
    )


class ChatStreamStopRequest(BaseModel):
    user_id: str = Field(..., description="用户 ID（与 stream 请求一致）")
    session_id: str = Field(..., description="会话 ID（与 stream 请求一致）")
    stream_id: str = Field(..., description="需要停止的流式请求标识（由 /chat/stream started 事件返回）")

    @field_validator("user_id")
    @classmethod
    def _validate_stop_user_id(cls, v: str) -> str:
        return validate_user_id(v)

    @field_validator("session_id")
    @classmethod
    def _validate_stop_session_id(cls, v: str) -> str:
        return validate_session_id(v)

    @field_validator("stream_id")
    @classmethod
    def _validate_stream_id(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            raise ValueError("stream_id is required")
        return s


class ChatStreamStopResponse(BaseModel):
    ok: bool = Field(True, description="是否已接受停止请求")
    user_id: str = Field(..., description="用户 ID")
    session_id: str = Field(..., description="会话 ID")
    stream_id: str = Field(..., description="被停止的流式请求 ID")

