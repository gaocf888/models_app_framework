from __future__ import annotations

"""
通用大模型推理接口。

服务配置前置条件（运维/开发）：
1) LLM 服务可用：
   - 需正确配置模型调用参数（模型名称、服务地址、鉴权）。
2) 可选 RAG：
   - 若请求开启 RAG，需配置 RAG 向量库与嵌入模型（RAG_ES_* / EMBEDDING_MODEL_*）。
3) 可选 Agentic 模式：
   - 若 rag_mode=agentic，建议确认 Agentic 参数配置（RAG_AGENTIC_*）。
"""

from fastapi import APIRouter, Depends

from app.models.llm import LLMInferenceRequest, LLMInferenceResponse
from app.services.llm_inference_service import LLMInferenceService

router = APIRouter()


def get_service() -> LLMInferenceService:
    # 简单工厂函数，后续可接入依赖注入或生命周期管理
    return LLMInferenceService()


@router.post("/infer", response_model=LLMInferenceResponse)
async def infer(
    req: LLMInferenceRequest,
    svc: LLMInferenceService = Depends(get_service),
) -> LLMInferenceResponse:
    """
    通用大模型推理接口。

    参数说明（见 LLMInferenceRequest）：
    - 必传：user_id、session_id，且 prompt/messages 至少提供其一
    - 可选：model、prompt_version、enable_rag、enable_context、rag_mode
    - 默认行为：enable_rag / enable_context 默认均为 true；rag_mode 为空时使用服务端默认策略
    """

    return await svc.infer(req)

