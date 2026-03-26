from __future__ import annotations

"""
Agentic RAG 基座实现。

设计目标：
- 在传统 RAGService 之上抽象出“RAG 模式”和统一入口，预留 Agentic RAG 能力；
- 当前版本以单步 RAG 为主，Agentic 模式先作为结构性骨架，后续在具体业务场景中扩展多步检索与工具调用；
- 对上层（Chatbot/Analysis/NL2SQL 等）暴露统一的 `retrieve` 接口，便于通过配置切换 basic/agentic。
"""

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from app.core.logging import get_logger
from app.rag.rag_service import RAGService

logger = get_logger(__name__)


class RAGMode(str, Enum):
    """
    RAG 模式枚举：
    - BASIC：传统单步检索 + 单次生成；
    - AGENTIC：多步检索/规划/工具调用（当前仅为骨架，占位实现）。
    """

    BASIC = "basic"
    AGENTIC = "agentic"


@dataclass
class RAGContext:
    """
    RAG 调用上下文。

    说明：
    - 为后续 Agentic RAG 预留结构，例如可以携带用户 ID、会话 ID、业务场景标识、已有检索结果等。
    """

    user_id: Optional[str] = None
    session_id: Optional[str] = None
    scene: Optional[str] = None  # 例如 "chatbot" / "analysis" / "nl2sql"


@dataclass
class RAGResult:
    """
    RAG 检索结果统一视图。
    """

    query: str
    context_snippets: List[str]
    used_agentic: bool = False


class AgenticRAGService:
    """
    Agentic RAG 服务基座。

    当前实现：
    - 支持 BASIC/AGENTIC 两种模式参数；
    - BASIC 模式直接委托给现有 RAGService；
    - AGENTIC 模式暂时复用 BASIC 实现，仅在返回结果中标记 used_agentic=True，为后续扩展多步逻辑预留接口。
    """

    def __init__(self, rag_service: RAGService | None = None, default_mode: RAGMode = RAGMode.BASIC) -> None:
        self._rag = rag_service or RAGService()
        self._default_mode = default_mode

    async def retrieve(
        self,
        query: str,
        ctx: Optional[RAGContext] = None,
        mode: Optional[RAGMode] = None,
        top_k: Optional[int] = None,
        namespace: Optional[str] = None,
    ) -> RAGResult:
        """
        统一的 RAG 检索入口。

        参数：
        - query：用户问题或检索查询；
        - ctx：可选上下文信息（user_id/session_id/scene 等）；
        - mode：可选 RAG 模式，未指定时使用默认模式；
        - top_k：可选检索数量（覆盖全局配置）。
        """
        effective_mode = mode or self._default_mode

        if effective_mode == RAGMode.BASIC:
            snippets = self._rag.retrieve_context(
                query,
                top_k=top_k,
                namespace=namespace,
                scene=(ctx.scene if ctx else None),
            )
            return RAGResult(query=query, context_snippets=snippets, used_agentic=False)

        # Agentic 模式骨架：当前先复用 BASIC 实现，
        # 后续在这里增加多步规划、多个命名空间/工具联合检索等逻辑。
        logger.info(
            "AgenticRAGService: agentic mode requested (scene=%s, user_id=%s, session_id=%s), "
            "current version falls back to basic RAG.",
            ctx.scene if ctx else None,
            ctx.user_id if ctx else None,
            ctx.session_id if ctx else None,
        )
        snippets = self._rag.retrieve_context(
            query,
            top_k=top_k,
            namespace=namespace,
            scene=(ctx.scene if ctx else None),
        )
        return RAGResult(query=query, context_snippets=snippets, used_agentic=True)

