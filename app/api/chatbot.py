from __future__ import annotations

"""
智能客服 HTTP 接口模块。

职责概览：
    - 提供非流式 `/chat`（已弃用，保留兼容）与流式 `/chat/stream`（SSE）对话入口；
    - 提供会话目录：`GET /sessions`（按 `user_id` 分页列举会话；方案 B：Redis `conv:index:` + `conv:meta:`，内存模式对齐）；
    - 提供会话运维：`GET/DELETE /sessions/messages`、`PATCH /sessions/title`（修改展示标题）。

部署前置条件（运维/开发）：
    1) LLM 服务可用：正确配置模型名称、服务地址与对 vLLM/OpenAI 兼容端的访问参数。
    2) 可选 RAG：`enable_rag=true` 时需完成 RAG 与嵌入配置（RAG_ES_*、EMBEDDING_MODEL_* 等）。
    3) 可选会话上下文：`enable_context=true` 时建议配置 REDIS_URL；否则仅为进程内内存，且多 worker 不共享。
    4) 业务路由鉴权：请求头 `Authorization: Bearer <SERVICE_API_KEY>`（环境变量 SERVICE_API_KEYS 或 SERVICE_API_KEY）。
       密钥由运维使用 `app.auth.keygen.generate_service_api_key` 生成后写入配置，见 `app/app-deploy/README.md`「Service API Key」。

会话维度：
    - 与业务层一致，使用 `(user_id, session_id)` 唯一确定一条对话线；`session_id` 由调用方生成并维护。
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
import json

from app.conversation.manager import ConversationManager
from app.conversation.session_catalog import session_list_limit_cap
from app.models.chatbot import (
    ChatRequest,
    ChatResponse,
    SessionDeleteResponse,
    SessionListItem,
    SessionListResponse,
    SessionMessageItem,
    SessionMessagesResponse,
    SessionTitlePatchRequest,
    SessionTitlePatchResponse,
)
from app.services.chatbot_service import ChatbotService

router = APIRouter()
# 与 ChatbotService 共用同一 ConversationManager，保证对话写入与 GET /sessions、GET/DELETE .../messages 读写一致。
_shared_conv = ConversationManager()
service = ChatbotService(conv_manager=_shared_conv)
_conv_admin = _shared_conv


@router.post("/chat", response_model=ChatResponse, summary="智能客服对话（基础版）", deprecated=True, include_in_schema=False)
async def chat(req: ChatRequest) -> ChatResponse:
    """
    智能客服对话接口（基础版，已弃用）

    一次性返回完整回答；生产环境建议优先使用 `/chat/stream`（SSE，首字节更快、体验更好）。

    Args:
        req (ChatRequest): 对话请求。必填 `user_id`、`session_id`、`query`；
            可选 `image_urls`（多模态，空项会被过滤）、`enable_rag`、`enable_context` 等，详见模型 Field 说明。

    Returns:
        ChatResponse: 包含 `answer`、`used_rag`、`used_nl2sql`、`intent_label`、`suggested_questions`、
            `context_snippets` 等字段。

    Raises:
        HTTPException: 本函数不直接抛出；Pydantic 校验失败时由框架返回 422。
        ValueError: 服务层在 `user_id` 为空时可能抛出（正常请求不应出现）。
    """
    return await service.chat(req)


@router.post("/chat/stream", summary="智能客服对话（流式 SSE）")
async def chat_stream(req: ChatRequest, request: Request):
    """
    智能客服流式对话（Server-Sent Events）。

    业务逻辑在 `ChatbotService.stream_chat_events`：可选 RAG、可选历史上下文、LangGraph 与 legacy 链路由配置决定；
    本路由仅负责将事件编码为 SSE 帧写出。

    Args:
        req (ChatRequest): 同 `/chat`，须含 `user_id`、`session_id`、`query` 等。
        request (Request): Starlette 请求对象，用于检测客户端断开（`is_disconnected`）以便停止生成。

    Returns:
        StreamingResponse: `Content-Type: text/event-stream; charset=utf-8`。
            每条事件为 `data: ` + JSON + 换行 + 空行（符合 SSE 事件分隔约定），JSON 形态包括：
            - `{"delta": "...", "finished": false}`：增量文本；
            - `{"finished": true, "meta": {...}}`：结束帧，可含 `used_rag`、`used_nl2sql`、`intent_label`、`suggested_questions`、`nl2sql_sql` 等；
            - `{"error": "...", "finished": true}`：异常时错误事件。

    Raises:
        HTTPException: 本函数不直接抛出；校验失败时 422。
    """

    async def event_generator():
        # SSE：每条消息一行 data，以空行结束；JSON 使用 ensure_ascii=False 以便中文直出。
        try:
            async for ev in service.stream_chat_events(req):
                if await request.is_disconnected():
                    return
                if ev.get("type") == "delta":
                    payload = json.dumps({"delta": ev.get("delta", ""), "finished": False}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                elif ev.get("type") == "finished":
                    payload = json.dumps({"finished": True, "meta": ev.get("meta", {})}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
        except Exception as exc:  # noqa: BLE001
            err = json.dumps({"error": str(exc), "finished": True}, ensure_ascii=False)
            yield f"data: {err}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream; charset=utf-8",
    )


@router.get(
    "/sessions",
    response_model=SessionListResponse,
    summary="列举用户会话目录（左侧栏列表）",
)
async def list_chat_sessions(
    user_id: Annotated[str, Query(description="调用方用户 ID")],
    limit: Annotated[int | None, Query(description="每页条数，默认受 CONV_SESSION_LIST_MAX 限制", ge=1)] = None,
    offset: Annotated[int, Query(description="偏移（分页）", ge=0)] = 0,
    order: Annotated[str, Query(description="排序：desc=最近活跃在前，asc=相反")] = "desc",
) -> SessionListResponse:
    """
    按用户维度返回算法侧已索引的会话列表（与 ``GET .../sessions/messages`` 使用同一存储）。

    Redis：`conv:index:{user_id}`（ZSET）+ ``conv:meta:{user_id}:{session_id}``（Hash）；内存模式结构对齐。
    标题策略见环境变量 ``CHATBOT_SESSION_TITLE_MODE``（truncate/off；llm 预留）。
    """
    cap = session_list_limit_cap()
    eff_limit = min(limit if limit is not None else cap, cap)
    order_desc = str(order or "desc").lower().strip() != "asc"
    rows, total = _conv_admin.list_sessions(
        user_id, limit=eff_limit, offset=offset, order_desc=order_desc
    )
    items = [
        SessionListItem(
            session_id=str(r["session_id"]),
            title=str(r["title"]),
            title_source=str(r["title_source"]),
            last_activity_at=int(r["last_activity_at"]),
            message_count=int(r.get("message_count") or 0),
        )
        for r in rows
    ]
    return SessionListResponse(
        user_id=user_id,
        total=total,
        limit=eff_limit,
        offset=offset,
        items=items,
    )


@router.get(
    "/sessions/messages",
    response_model=SessionMessagesResponse,
    summary="查询会话消息列表（历史/导出）",
)
async def get_session_messages(
    user_id: Annotated[str, Query(description="调用方用户 ID")],
    session_id: Annotated[str, Query(description="会话 ID")],
    limit: Annotated[int | None, Query(description="最多返回条数，默认受 CONV_EXPORT_MAX_MESSAGES 限制", ge=1)] = None,
) -> SessionMessagesResponse:
    """
    查询指定会话下已持久化的消息列表（历史展示、导出、对账）。

    数据来自与 `/chat/stream` 相同的会话存储；单条条数上限受环境变量 `CONV_EXPORT_MAX_MESSAGES` 约束。

    Args:
        user_id (str): 调用方用户标识（须与写入会话时一致）。
        session_id (str): 会话标识。
        limit (int | None): 可选，限制返回条数上限（仍不超过服务端配置的全局上限）。

    Returns:
        SessionMessagesResponse: `title`/`title_source` 与 `GET /sessions` 列表同源；`messages` 按时间顺序。

    Raises:
        HTTPException: 本函数不直接抛出；参数校验失败时 422。
    """
    raw = _conv_admin.get_session_messages(user_id, session_id, limit=limit)
    snap = _conv_admin.get_session_title_snapshot(user_id, session_id)
    items = [
        SessionMessageItem(role=str(m.get("role", "")), content=str(m.get("content", "")), ts=m.get("ts"))
        for m in raw
    ]
    return SessionMessagesResponse(
        user_id=user_id,
        session_id=session_id,
        title=str(snap.get("title") or ""),
        title_source=str(snap.get("title_source") or "off"),
        count=len(items),
        messages=items,
    )


@router.patch(
    "/sessions/title",
    response_model=SessionTitlePatchResponse,
    summary="修改会话展示标题",
)
async def patch_session_title(
    user_id: Annotated[str, Query(description="调用方用户 ID")],
    session_id: Annotated[str, Query(description="会话 ID")],
    body: SessionTitlePatchRequest,
) -> SessionTitlePatchResponse:
    """
    将目录中的展示标题更新为用户指定文案，并标记 `title_source=user`（与首句自动 `truncated` 区分）。

    会话须已存在（至少有一条消息写入过）；否则返回 404。
    """
    ok = _conv_admin.update_session_title(user_id, session_id, body.title)
    if not ok:
        raise HTTPException(status_code=404, detail="session not found")
    snap = _conv_admin.get_session_title_snapshot(user_id, session_id)
    return SessionTitlePatchResponse(
        user_id=user_id,
        session_id=session_id,
        title=str(snap.get("title") or ""),
        title_source=str(snap.get("title_source") or "user"),
    )


@router.delete(
    "/sessions/messages",
    response_model=SessionDeleteResponse,
    summary="删除会话（清除存储中的对话）",
)
async def delete_session_messages(
    user_id: Annotated[str, Query(description="调用方用户 ID")],
    session_id: Annotated[str, Query(description="会话 ID")],
) -> SessionDeleteResponse:
    """
    删除算法侧会话数据（清空热层 Redis/内存会话，并同步删除冷层归档索引记录）。

    不修改调用方业务库中的用户、订单等数据；仅释放本服务侧的上下文缓存。

    Args:
        user_id (str): 用户标识。
        session_id (str): 会话标识。

    Returns:
        SessionDeleteResponse: 确认已执行删除操作的结构化响应。

    Raises:
        HTTPException: 本函数不直接抛出；参数校验失败时 422。
    """
    _conv_admin.clear_session(user_id, session_id)
    return SessionDeleteResponse(user_id=user_id, session_id=session_id)
