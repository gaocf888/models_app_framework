from __future__ import annotations

"""
通用大模型推理 HTTP 接口（`/llm/infer`）。

职责：
    - 将 `LLMInferenceRequest` 交给 `LLMInferenceService`：可选 RAG（basic / agentic）、
      可选多轮会话上下文、Prompt 版本与模型选择等。

服务配置前置条件（运维/开发）：
    1) LLM 服务可用：模型名称、vLLM/OpenAI 兼容端点与密钥（见 LLM 相关环境变量）。
    2) 可选 RAG：需配置 ES/嵌入等（RAG_ES_*、EMBEDDING_MODEL_*）。
    3) 可选 Agentic RAG：`rag_mode=agentic` 且 LangChain 可用时走计划检索链路（RAG_AGENTIC_* 等）。
    4) 鉴权：请求头 `Authorization: Bearer <SERVICE_API_KEY>`（密钥由 `app.auth.keygen.generate_service_api_key` 生成并写入
       SERVICE_API_KEYS，见 `app/app-deploy/README.md`「Service API Key」）。
"""

from fastapi import APIRouter, Depends

from app.models.llm import LLMInferenceRequest, LLMInferenceResponse
from app.services.llm_inference_service import LLMInferenceService

router = APIRouter()


def get_service() -> LLMInferenceService:
    return LLMInferenceService()


@router.post("/infer", response_model=LLMInferenceResponse)
async def infer(
    req: LLMInferenceRequest,
    svc: LLMInferenceService = Depends(get_service),
) -> LLMInferenceResponse:
    """
    执行一次通用大模型推理（非流式）。

    Args:
        req (LLMInferenceRequest): 必填 `user_id`、`session_id`；
            `prompt` 与 `messages` 二选一（同时提供时以 `messages` 为准，见模型说明）；
            可选 `model`、`prompt_version`、`enable_rag`、`enable_context`、`rag_mode`。
        svc (LLMInferenceService): 由依赖注入提供。

    Returns:
        LLMInferenceResponse: 含 `answer`、`model`、`used_rag`、`context_snippets`、`prompt_version` 等。

    Raises:
        HTTPException: 路由层不直接抛出；校验失败 422。
        ValueError: 服务层在缺少 `user_id` 等时可能抛出。
    """
    return await svc.infer(req)
