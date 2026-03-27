from __future__ import annotations

"""
Agentic RAG 基座实现。

设计目标：
- 在传统 RAGService 之上抽象出“RAG 模式”和统一入口，预留 Agentic RAG 能力；
- 当前版本以单步 RAG 为主，Agentic 模式先作为结构性骨架，后续在具体业务场景中扩展多步检索与工具调用；
- 对上层（Chatbot/Analysis/NL2SQL 等）暴露统一的 `retrieve` 接口，便于通过配置切换 basic/agentic。
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
import re
from typing import List, Optional

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.rag.models import RetrievedChunk
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
    chunks: Optional[List[RetrievedChunk]] = None
    plan_steps: Optional[List[str]] = None


@dataclass
class _QueryStep:
    query: str
    weight: float = 1.0


class AgenticRAGService:
    """
    Agentic RAG 服务基座。

    企业可用多步策略（轻量）：
    - BASIC：单步检索，直接返回；
    - AGENTIC：对子问题规划 -> 并行检索 -> 去重融合 -> 预算裁剪。
    """

    def __init__(self, rag_service: RAGService | None = None, default_mode: RAGMode = RAGMode.BASIC) -> None:
        self._rag = rag_service or RAGService()
        self._default_mode = default_mode
        # 从统一配置读取 Agentic 策略参数，便于线上灰度调优。
        self._cfg = get_app_config().rag.agentic
        self._max_subqueries = max(1, self._cfg.max_subqueries)

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
        # 全局开关关闭时，强制回退 BASIC，避免线上异常扩散。
        if not self._cfg.enabled:
            effective_mode = RAGMode.BASIC

        if effective_mode == RAGMode.BASIC:
            chunks = self._rag.retrieve_chunks(
                query,
                top_k=top_k,
                namespace=namespace,
                scene=(ctx.scene if ctx else None),
            )
            snippets = [c.text for c in chunks if c.text]
            return RAGResult(query=query, context_snippets=snippets, chunks=chunks, used_agentic=False)

        scene = ctx.scene if ctx else None
        steps = self._plan_subqueries(query=query, scene=scene)
        logger.info(
            "AgenticRAGService: planned %s subqueries (scene=%s, user_id=%s, session_id=%s)",
            len(steps),
            scene,
            ctx.user_id if ctx else None,
            ctx.session_id if ctx else None,
        )
        merged = self._execute_plan(
            steps=steps,
            top_k=top_k,
            namespace=namespace,
            scene=scene,
        )
        snippets = [c.text for c in merged if c.text]
        return RAGResult(
            query=query,
            context_snippets=snippets,
            chunks=merged,
            used_agentic=True,
            plan_steps=[s.query for s in steps],
        )

    def _plan_subqueries(self, query: str, scene: str | None) -> List[_QueryStep]:
        """
        规则化子问题规划（不依赖外部 LLM，保证稳定可用）。
        """
        base = query.strip()
        if not base:
            return [_QueryStep(query="")]
        # 主问题作为第一优先级查询。
        steps: List[_QueryStep] = [_QueryStep(query=base, weight=self._cfg.main_query_weight)]

        # 按常见连接词拆分复合问题，得到子问题
        parts = [p.strip(" ，。；;") for p in re.split(r"[，。；;]|以及|并且|同时|然后|并|且", base) if p.strip()]
        for p in parts[: self._max_subqueries - 1]:
            if p and p != base:
                steps.append(_QueryStep(query=p, weight=self._cfg.split_query_weight))

        # 分场景补一条“关键词强化检索”（可配置关闭）。
        if self._cfg.enable_scene_boost:
            if scene == "nl2sql":
                steps.append(_QueryStep(query=f"{base} 表 字段 口径", weight=self._cfg.scene_boost_weight))
            elif scene == "analysis":
                steps.append(_QueryStep(query=f"{base} 背景 影响 原因", weight=self._cfg.scene_boost_weight))
            elif scene == "chatbot":
                steps.append(_QueryStep(query=f"{base} FAQ 说明", weight=self._cfg.scene_boost_weight))

        # 去重并裁剪
        uniq: List[_QueryStep] = []
        seen = set()
        for s in steps:
            key = s.query.lower()
            if key in seen:
                continue
            seen.add(key)
            uniq.append(s)
            if len(uniq) >= self._max_subqueries:
                break
        return uniq

    def _execute_plan(
        self,
        steps: List[_QueryStep],
        top_k: Optional[int],
        namespace: Optional[str],
        scene: Optional[str],
    ) -> List[RetrievedChunk]:
        """
        并行执行各子问题召回，按 score 与 step 权重融合后去重。
        """
        if not steps:
            return []
        # 给每个子问题一个小预算，最终统一按总预算裁剪
        per_step_k = max(self._cfg.per_step_k_floor, (top_k or 6))
        merged_candidates: List[tuple[RetrievedChunk, float]] = []

        max_workers = min(len(steps), max(1, self._cfg.max_parallel_workers))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_map = {
                pool.submit(
                    self._rag.retrieve_chunks,
                    s.query,
                    per_step_k,
                    namespace,
                    None,
                    scene,
                ): s
                for s in steps
            }
            for f in as_completed(future_map):
                step = future_map[f]
                try:
                    chunks = f.result() or []
                except Exception:  # noqa: BLE001
                    logger.exception("AgenticRAGService: subquery retrieval failed, query=%s", step.query)
                    chunks = []
                for idx, c in enumerate(chunks):
                    base_score = c.score if c.score is not None else 0.0
                    # rank 越靠前加分越高，叠加 step 权重
                    rank_bonus = 1.0 / float(idx + 1)
                    fused = (base_score + rank_bonus) * step.weight
                    merged_candidates.append((c, fused))

        # 按 chunk_id/text 去重并按融合分排序
        merged_candidates.sort(key=lambda x: x[1], reverse=True)
        uniq: List[RetrievedChunk] = []
        seen = set()
        budget = top_k or 6
        for c, fused_score in merged_candidates:
            key = c.chunk_id or c.text
            if not key or key in seen:
                continue
            seen.add(key)
            c.score = fused_score
            uniq.append(c)
            if len(uniq) >= budget:
                break
        return uniq

