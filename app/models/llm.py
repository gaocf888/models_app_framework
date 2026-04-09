from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from app.conversation.ids import validate_session_id, validate_user_id


class ChatMessage(BaseModel):
    """
    通用聊天消息模型，兼容 OpenAI Chat 格式的最小子集。
    """

    role: str = Field(..., description="消息角色：user/assistant/system")
    content: str = Field(..., description="消息内容")


class LLMInferenceRequest(BaseModel):
    """
    通用大模型推理请求。

    支持两种调用方式（二选一）：
    - prompt：单轮纯文本提示；
    - messages：多轮对话格式（兼容 ChatCompletion）。
    """

    user_id: str = Field(
        ...,
        description="用户 ID（由调用方后台传入，用于会话与上下文）",
    )
    session_id: str = Field(..., description="会话 ID，用于上下文管理")

    @field_validator("user_id")
    @classmethod
    def _v_uid(cls, v: str) -> str:
        return validate_user_id(v)

    @field_validator("session_id")
    @classmethod
    def _v_sid(cls, v: str) -> str:
        return validate_session_id(v)

    prompt: Optional[str] = Field(
        None,
        description="单轮纯文本提示；若与 messages 同时提供，则以 messages 为准",
    )
    messages: Optional[List[ChatMessage]] = Field(
        None,
        description="多轮对话消息列表，兼容 OpenAI Chat 格式的最小子集",
    )

    model: Optional[str] = Field(
        None,
        description="模型名称，对应配置中的模型 key；为空则使用默认模型",
    )
    prompt_version: Optional[str] = Field(
        None,
        description="指定使用的 Prompt 模板版本；为空则按 A/B 策略自动分流",
    )

    enable_rag: bool = Field(
        True,
        description="是否启用 RAG 检索并拼接上下文",
    )
    enable_context: bool = Field(
        True,
        description="是否启用会话上下文（历史消息）",
    )

    rag_mode: Optional[str] = Field(
        None,
        description="RAG 模式：basic / agentic；为空表示使用服务端默认策略（当前等价于 basic，仅预留扩展能力）",
    )


class LLMInferenceResponse(BaseModel):
    """
    通用大模型推理响应。
    """

    answer: str = Field(..., description="模型生成的回复文本")
    model: str = Field(..., description="实际使用的模型标识")
    prompt_version: Optional[str] = Field(
        None,
        description="实际使用的 Prompt 模板版本（若有）",
    )
    used_rag: bool = Field(
        False,
        description="是否实际使用了 RAG 上下文",
    )
    context_snippets: List[str] = Field(
        default_factory=list,
        description="用于回答的问题上下文片段摘要",
    )

