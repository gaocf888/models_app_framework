from __future__ import annotations

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

    支持：
    - 单轮 prompt；
    - 多轮 messages（兼容 ChatCompletion）。
    """

    return await svc.infer(req)

