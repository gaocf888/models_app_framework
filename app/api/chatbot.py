from __future__ import annotations

"""
智能客服 HTTP 接口模块。

职责概览：
    - 提供非流式 `/chat`（已弃用，保留兼容）与流式 `/chat/stream`（SSE）对话入口；
    - 提供多模态图片上传：`POST /upload`（MinIO 预签名 URL，填入 `image_urls`）；
    - 提供会话目录：`GET /sessions`（按 `user_id` 分页列举会话；方案 B：Redis `conv:index:` + `conv:meta:`，内存模式对齐）；
    - 提供会话运维：`GET/DELETE /sessions/messages`、`DELETE /sessions/message`（单条/批量消息）、`PATCH /sessions/title`（修改展示标题）。

部署前置条件（运维/开发）：
    1) LLM 服务可用：正确配置模型名称、服务地址与对 vLLM/OpenAI 兼容端的访问参数。
    2) 可选 RAG：`enable_rag=true` 时需完成 RAG 与嵌入配置（RAG_ES_*、EMBEDDING_MODEL_* 等）。
    3) 可选会话上下文：`enable_context=true` 时建议配置 REDIS_URL；否则仅为进程内内存，且多 worker 不共享。
    4) 业务路由鉴权：请求头 `Authorization: Bearer <SERVICE_API_KEY>`（环境变量 SERVICE_API_KEYS 或 SERVICE_API_KEY）。
       密钥由运维使用 `app.auth.keygen.generate_service_api_key` 生成后写入配置，见 `app/app-deploy/README.md`「Service API Key」。

会话维度：
    - 与业务层一致，使用 `(user_id, session_id)` 唯一确定一条对话线；`session_id` 由调用方生成并维护。
"""

from typing import Annotated, Any

import io
import uuid
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
import json

try:
    from minio import Minio
except Exception:  # noqa: BLE001
    Minio = None  # type: ignore[assignment,misc]

from app.core.config import get_app_config

from app.conversation.manager import ConversationManager
from app.conversation.message_id import build_conversation_message_id, is_valid_message_id_hex
from app.conversation.session_catalog import session_list_limit_cap
from app.models.inspection_extract import InspectionUploadResponse
from app.models.chatbot import (
    ChatRequest,
    ChatResponse,
    ChatbotHitlResumeRequest,
    ChatStreamStopRequest,
    ChatStreamStopResponse,
    SessionDeleteResponse,
    SessionListItem,
    SessionListResponse,
    SessionMessageDeleteResponse,
    SessionMessagesDeleteRequest,
    SessionMessageItem,
    SessionMessagesResponse,
    SessionTitlePatchRequest,
    SessionTitlePatchResponse,
)
from app.llm.graphs.chatbot_rag_citations import filter_rag_citation_dicts
from app.services.chatbot_service import ChatbotService
from app.services.chatbot_image_utils import split_message_content_and_images

router = APIRouter()
# 与 ChatbotService 共用同一 ConversationManager，保证对话写入与 GET /sessions、GET/DELETE .../messages 读写一致。
_shared_conv = ConversationManager()
service = ChatbotService(conv_manager=_shared_conv)
_conv_admin = _shared_conv


def _session_message_rag_citations(raw: Any) -> list[dict[str, Any]]:
    """将存储中的 rag_citations 规范为 dict 列表（与 SSE finished.meta 一致，并剔除 NL2SQL 库表知识库）。"""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for it in raw:
        if isinstance(it, dict):
            out.append(dict(it))
    return filter_rag_citation_dicts(out)
def _delete_messages_by_ids(user_id: str, session_id: str, message_ids: list[str]) -> SessionMessageDeleteResponse:
    mids = [str(x or "").strip().lower() for x in (message_ids or []) if str(x or "").strip()]
    if not mids:
        raise HTTPException(status_code=422, detail="message_id is required")
    # 去重并保持首现顺序，避免重复删除同一 id。
    uniq_mids = list(dict.fromkeys(mids))
    invalid = [x for x in uniq_mids if not is_valid_message_id_hex(x)]
    if invalid:
        raise HTTPException(status_code=422, detail=f"invalid message_id: {', '.join(invalid)}")
    deleted_ids: list[str] = []
    not_found_ids: list[str] = []
    for mid in uniq_mids:
        ok = _conv_admin.delete_message(user_id, session_id, mid)
        if ok:
            deleted_ids.append(mid)
        else:
            not_found_ids.append(mid)
    if not deleted_ids:
        raise HTTPException(status_code=404, detail="message not found")
    return SessionMessageDeleteResponse(
        user_id=user_id,
        session_id=session_id,
        message_ids=uniq_mids,
        deleted_ids=deleted_ids,
        not_found_ids=not_found_ids,
        deleted_count=len(deleted_ids),
    )


@router.post(
    "/upload",
    response_model=InspectionUploadResponse,
    summary="上传智能客服多模态图片",
)
async def upload_chatbot_image_endpoint(file: UploadFile = File(...)) -> InspectionUploadResponse:
    """
    上传本轮对话关联的图片，返回 MinIO 预签名 URL。

    将响应中的 `url` 填入 `POST /chatbot/chat/stream`（或 `/chat`）请求体的 `image_urls` 数组即可。
    对象前缀为 `chatbot/`（与综合分析看图诊断 `analysis_img_diag/` 区分）。

    **注意**：预签名 URL 有效期由 `CHATBOT_IMAGE_MINIO_PRESIGN_TTL_SECONDS` 控制（默认 900 秒），
    请在有效期内发起对话；过期后需重新上传。
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file upload")
    try:
        chat_cfg = get_app_config().chatbot
        if Minio is None:
            raise RuntimeError("MinIO client unavailable; install minio package")
        endpoint = (chat_cfg.image_minio_endpoint or "").strip()
        if not endpoint:
            raise RuntimeError("CHATBOT_IMAGE_MINIO_ENDPOINT is not configured")
        client = Minio(
            endpoint,
            access_key=(chat_cfg.image_minio_access_key or "").strip(),
            secret_key=(chat_cfg.image_minio_secret_key or "").strip(),
            secure=bool(chat_cfg.image_minio_secure),
        )
        bucket = (chat_cfg.image_minio_bucket or "chatbot-images").strip()
        ttl = max(300, int(chat_cfg.image_minio_presign_ttl_seconds))
        safe_name = Path(file.filename or "image.bin").name
        suf = Path(safe_name).suffix.lower()
        ct = (file.content_type or "").strip().lower()
        if not (ct.startswith("image/") or suf in {".jpg", ".jpeg", ".png", ".webp", ".gif"}):
            raise ValueError("only image files are allowed")
        if chat_cfg.image_minio_auto_create_bucket and not client.bucket_exists(bucket):
            client.make_bucket(bucket)
        object_name = f"chatbot/{uuid.uuid4().hex}_{safe_name}"
        put_ct = ct if ct.startswith("image/") else "application/octet-stream"
        client.put_object(
            bucket_name=bucket,
            object_name=object_name,
            data=io.BytesIO(data),
            length=len(data),
            content_type=put_ct,
        )
        url = client.presigned_get_object(
            bucket_name=bucket,
            object_name=object_name,
            expires=timedelta(seconds=ttl),
        )
        return InspectionUploadResponse(
            ok=True,
            file_name=safe_name,
            object_name=object_name,
            source_type="image",
            url=url,
            bucket=bucket,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"chatbot upload failed: {e}") from e


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
            - `{"started": true, "stream_id": "..."}`：流式已建立，可用于 `/chat/stop` 中断；
            - `{"delta": "...", "finished": false}`：增量正文（不含完整 `[n]` 引用标记；引用由独立事件下发）；
            - `{"citation_ref": n, "finished": false}`：知识引用标注（`n` 与 `meta.rag_citations[].ref_index` 对齐）；
            - `{"finished": true, "meta": {...}}`：结束帧，可含 `used_rag`、`used_nl2sql`、`nl2sql_sql`（仅 NL2SQL 路径有值，否则为 null）、`nl2sql_analysis`（查数旁路结构化：列/样本行等，正文仍为 Markdown delta）、`intent_label`、`suggested_questions`、`rag_citations` 等；
            - `{"error": "...", "finished": true}`：异常时错误事件。

    **RAG 内联引用（前端用法）**

    1. **结束帧** `meta.rag_citations`：结构化来源列表（与 `GET /sessions/messages` 中 assistant 消息的 `rag_citations` 同形）。
       每项常见字段：
       - `ref_index`（int，从 1 起）：与正文中 `[n]` 标记一一对应；
       - `doc_name`、`section_path`、`namespace`、`doc_version`、`chunk_id`；
       - `text_preview`：片段摘要；
       - `original_content_url`（可选）：摄入时的原始文档 URL，有则可渲染为外链。

    2. **流式引用事件 `citation_ref`**：RAG 路径下后端从模型输出中识别完整 `[n]` 后单独下发
       `{"citation_ref": n, "finished": false}`；正文 `delta` 不再包含该 `[n]` 字面量。（正文中渲染链接时，需要通过n与结束帧rag_citations中的ref_index进行匹配，来获取知识文档名称和链接）
       会话落库与历史消息的 `answer` 仍保留 `[n]` 纯文本。

    3. **前端渲染建议**：
       - 流式：收到 `citation_ref` 即渲染角标/链接，用 `ref_index === n` 在 `finished.meta.rag_citations` 中查找；
       - 历史/非流式：解析 `answer` 中的 `\\[(\d+)\\]` 或与 `rag_citations` 对齐；
       - 保留回答下方 `rag_citations` 来源列表；
       - FAQ 软直通时以 `ref_index` 为准，且 `n` 不超过本轮注入 prompt 的编号上限。

    4. **NL2SQL 路径**（`intent_label=data_query`）：`rag_citations` 为空，无需解析内联引用。

    Raises:
        HTTPException: 本函数不直接抛出；校验失败时 422。
    """

    async def event_generator():
        # SSE：每条消息一行 data，以空行结束；JSON 使用 ensure_ascii=False 以便中文直出。
        try:
            async for ev in service.stream_chat_events(req):
                if await request.is_disconnected():
                    return
                if ev.get("type") == "started":
                    payload = json.dumps({"started": True, "stream_id": ev.get("stream_id")}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                elif ev.get("type") == "delta":
                    payload = json.dumps({"delta": ev.get("delta", ""), "finished": False}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                elif ev.get("type") == "citation":
                    payload = json.dumps(
                        {"citation_ref": int(ev.get("ref_index") or 0), "finished": False},
                        ensure_ascii=False,
                    )
                    yield f"data: {payload}\n\n"
                elif ev.get("type") == "chatbot_hitl_required":
                    body = {k: v for k, v in ev.items() if k != "type"}
                    payload = json.dumps(
                        {"chatbot_hitl_required": True, "finished": False, **body},
                        ensure_ascii=False,
                    )
                    yield f"data: {payload}\n\n"
                elif ev.get("type") == "finished":
                    payload = json.dumps(
                        {"finished": True, "meta": ev.get("meta", {})},
                        ensure_ascii=False,
                        default=str,
                    )
                    yield f"data: {payload}\n\n"
        except Exception as exc:  # noqa: BLE001
            err = json.dumps({"error": str(exc), "finished": True}, ensure_ascii=False)
            yield f"data: {err}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream; charset=utf-8",
    )


@router.post("/chat/resume-stream", summary="智能客服 HITL 续跑（流式 SSE）")
async def chat_resume_stream(req: ChatbotHitlResumeRequest, request: Request):
    """
    智能客服人机协同（HITL）续跑接口（Server-Sent Events）。

    当 ``/chat/stream`` 返回 ``chatbot_hitl_required`` 事件后，用户在前端点击确认按钮，
    本接口携带 ``resume_token`` 与 ``action`` 恢复图编排并继续流式输出。
    需部署侧开启 ``CHATBOT_HITL_ENABLED=true``（见 ``.env.example``）。

    业务逻辑在 ``ChatbotService.stream_chat_resume_events`` →
    ``ChatbotLangGraphRunner.run_resume_stream_events``；本路由仅负责 SSE 编码。

    Args:
        req (ChatbotHitlResumeRequest): 续跑请求体，字段说明见下。
            - ``user_id`` (str, 必填)：须与触发 HITL 的 ``/chat/stream`` 请求一致。
            - ``session_id`` (str, 必填)：须与触发 HITL 的会话一致。
            - ``resume_token`` (str, 必填)：来自 ``chatbot_hitl_required`` 事件或
              结束帧 ``meta.resume_token``；单次有效，过期后返回错误帧。
            - ``action`` (str, 必填)：用户选择的按钮 id，取值见下方 **action 枚举**。
            - ``payload`` (dict, 可选)：补充参数，常用键：
              - ``refined_query`` (str)：改写/补充后的问句。``route_clarify`` 时**必填**；
                其它 action 可选，若提供则覆盖原 ``query``。
        request (Request): Starlette 请求对象，用于检测客户端断开。

    Returns:
        StreamingResponse: ``Content-Type: text/event-stream; charset=utf-8``。
            每条事件为 ``data: `` + JSON + 换行 + 空行，JSON 形态包括：

            - ``{"started": true, "stream_id": "..."}``：续跑流已建立，可用于 ``/chat/stop`` 中断；
            - ``{"delta": "...", "finished": false}``：增量正文（与 ``/chat/stream`` 相同）；
            - ``{"citation_ref": n, "finished": false}``：RAG 路径知识引用（与 ``/chat/stream`` 相同）；
            - ``{"chatbot_hitl_required": true, "finished": false, ...}``：续跑过程中再次触发 HITL
              （如 NL2SQL 重试仍失败且未达最大重试次数）；字段同首轮 HITL 事件；
            - ``{"finished": true, "meta": {...}}``：续跑结束；``meta`` 字段语义与 ``/chat/stream`` 一致，
              另含 ``pending_hitl``、``hitl_kind``、``resume_token``（若仍处于待确认状态）；
            - ``{"error": "...", "finished": true}``：``resume_token`` 无效/过期、会话不匹配或内部异常。

    **chatbot_hitl_required 事件字段（首轮由 /chat/stream 下发，续跑亦可能再次出现）**

    - ``hitl_id``：本轮 HITL 标识；
    - ``resume_token``：续跑凭证（调用本接口时回传）；
    - ``hitl_kind``：``intent_route_confirm``（意图路由确认）、
      ``intent_disambiguation_suggest``（补充问句后仍模糊时的 LLM 消歧选项）、
      或 ``nl2sql_gen_failed``（NL2SQL 生成失败）；
    - ``prompt``：展示给用户的确认话术（已随 ``delta`` 写入会话）；
    - ``ui_buttons``：按钮列表，每项 ``{"id": "<action>", "label": "<展示文案>"}``；
    - ``context``：辅助上下文（``intent_label``、``intent_confidence``、``original_query``、
      ``nl2sql_fail_reason``、``disambiguation_options`` 等），供前端展示详情，非必填回传。

    **action 枚举（须与 ui_buttons[].id 一致）**

    意图路由确认（``hitl_kind=intent_route_confirm``）：

    - ``route_data_query``：确认为结构化查数，走 NL2SQL；
    - ``route_kb_qa``：确认为知识库问答，走 RAG；
    - ``route_clarify``：用户在问句框补充完整问题（``payload.refined_query`` **必填**），
      系统更新 ``query`` 后**重新意图分类**并按新意图路由；若仍模糊则进入消歧 HITL
      （``intent_disambiguation_suggest``），不再重复三路由按钮。

    意图消歧（``hitl_kind=intent_disambiguation_suggest``）：

    - ``pick_disambiguation_0`` / ``pick_disambiguation_1`` / ``pick_disambiguation_2``：
      选择对应候选问句；或统一 ``pick_disambiguation_option`` + ``payload.option_index``；
      系统用选项的 ``query`` + ``route_hint`` **直接路由**（不再跑意图分类）。

    NL2SQL 生成失败（``hitl_kind=nl2sql_gen_failed``）：

    - ``nl2sql_retry``：重试查数（跳过 SQL 缓存，并将上轮失败原因注入生成 prompt）；
    - ``fallback_kb_qa``：放弃查数，改用知识库 RAG 回答原问句。

    **前端使用说明**
    目前 意图识别失败/数据查询生成sql失败 会触发人机协同 处理逻辑
    1. **监听 HITL**：在 ``/chat/stream`` 的 SSE 回调中识别
       ``ev.chatbot_hitl_required === true && ev.finished === false``；
       保存 ``resume_token``、``ui_buttons``、``prompt``、``hitl_kind``
       （``intent_route_confirm`` / ``intent_disambiguation_suggest`` / ``nl2sql_gen_failed``）。

        （意图识别失败 返回的ui_buttons：[{"id": "route_data_query", "label": "查实时/台账数据"}, {"id": "route_kb_qa", "label": "基于知识库分析"}, {"id": "route_clarify", "label": "我先补充问题"}]）
		（意图消歧 返回动态 ui_buttons，如 pick_disambiguation_0/1/2，label 为短标题）
		（数据查询失败 返回的ui_buttons: [{"id": "nl2sql_retry", "label": "重试查数"}, {"id": "fallback_kb_qa", "label": "基于知识库分析"}]）

    2. **渲染按钮**：按 ``ui_buttons`` 渲染操作区（按钮/链接）；``prompt`` 通常已通过 ``delta`` 出现在正文中。
       勿对 ``intent_disambiguation_suggest`` 回落到默认三路由按钮。
    3. **发起续跑**：用户点击后 ``POST /chatbot/chat/resume-stream``，Body 示例::

           {
             "user_id": "u1",
             "session_id": "s1",
             "resume_token": "cb_rt_xxx",
             "action": "route_data_query",
             "payload": {}
           }

       选择「我先补充问题」（``route_clarify``）时，须在 ``payload.refined_query`` 传入补充后的完整问句
       （测试页 ``tests/web/chatbot-stream.html`` 复用上方 ``#query`` 问句框，无需新增控件）::

           {
             "user_id": "u1",
             "session_id": "s1",
             "resume_token": "cb_rt_xxx",
             "action": "route_clarify",
             "payload": { "refined_query": "1号炉当前负荷是多少？" }
           }

       消歧选项示例::

           {
             "user_id": "u1",
             "session_id": "s1",
             "resume_token": "cb_rt_xxx",
             "action": "pick_disambiguation_0",
             "payload": {}
           }
        **说明：**
        1）resume_token -- 上述保存得resume_token；action -- 上述保存的ui_buttons中的id
		- （意图识别失败：route_data_query / route_kb_qa / route_clarify）
		- （意图消歧：pick_disambiguation_0/1/2 或 pick_disambiguation_option）
		- （查询数据失败：nl2sql_retry / fallback_kb_qa）
       2）``route_clarify`` 时 ``payload.refined_query`` **必填**；其它 action 可选 ``refined_query``。
    4. **处理续跑 SSE**：与 ``/chat/stream`` 相同拼接 ``delta`` / ``citation_ref``；
       若再次收到 ``chatbot_hitl_required``，重复步骤 2–3（使用新的 ``resume_token``）。
    5. **结束**：收到 ``finished: true`` 后读取 ``meta``（``used_nl2sql``、``rag_citations`` 等）；
       ``resume_token`` 使用后即失效，勿重复提交同一 token。
    6. **错误**：``error`` 帧常见 ``invalid or expired resume_token``、
       ``resume_token session mismatch``；应提示用户重新提问或刷新会话。

    Raises:
        HTTPException: 请求体校验失败时 422；业务错误以 SSE ``error`` 帧返回，不抛 HTTP 异常。
    """

    async def event_generator():
        try:
            async for ev in service.stream_chat_resume_events(req):
                if await request.is_disconnected():
                    return
                if ev.get("type") == "started":
                    payload = json.dumps({"started": True, "stream_id": ev.get("stream_id")}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                elif ev.get("type") == "delta":
                    payload = json.dumps({"delta": ev.get("delta", ""), "finished": False}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                elif ev.get("type") == "citation":
                    payload = json.dumps(
                        {"citation_ref": int(ev.get("ref_index") or 0), "finished": False},
                        ensure_ascii=False,
                    )
                    yield f"data: {payload}\n\n"
                elif ev.get("type") == "chatbot_hitl_required":
                    body = {k: v for k, v in ev.items() if k != "type"}
                    payload = json.dumps(
                        {"chatbot_hitl_required": True, "finished": False, **body},
                        ensure_ascii=False,
                    )
                    yield f"data: {payload}\n\n"
                elif ev.get("type") == "finished":
                    payload = json.dumps(
                        {"finished": True, "meta": ev.get("meta", {})},
                        ensure_ascii=False,
                        default=str,
                    )
                    yield f"data: {payload}\n\n"
                elif ev.get("type") == "error":
                    payload = json.dumps({"error": ev.get("error", ""), "finished": True}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
        except Exception as exc:  # noqa: BLE001
            err = json.dumps({"error": str(exc), "finished": True}, ensure_ascii=False)
            yield f"data: {err}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream; charset=utf-8",
    )


@router.post("/chat/stop", response_model=ChatStreamStopResponse, summary="中断指定流式对话")
async def stop_chat_stream(req: ChatStreamStopRequest) -> ChatStreamStopResponse:
    """
    显式中断某次流式输出。

    使用方式：
    - 先从 `/chat/stream` 首帧 `{"started": true, "stream_id": "..."}` 获取 `stream_id`；
    - 再调用本接口发送停止信号。
    """
    await service.stop_stream(req.user_id, req.session_id, req.stream_id)
    return ChatStreamStopResponse(
        user_id=req.user_id,
        session_id=req.session_id,
        stream_id=req.stream_id,
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
        SessionMessagesResponse: `title`/`title_source` 与 `GET /sessions` 列表同源；`messages` 按时间顺序；
        助手消息在 RAG 路径下可含 `rag_citations`（含 `ref_index` 与 `original_content_url` 等，与流式 `finished.meta.rag_citations` 同形）。
        正文 `content` 中可能出现 `[n]` 内联引用，解析方式同 `/chat/stream` 文档「RAG 内联引用」一节。

    Raises:
        HTTPException: 本函数不直接抛出；参数校验失败时 422。
    """
    raw = _conv_admin.get_session_messages(user_id, session_id, limit=limit)
    snap = _conv_admin.get_session_title_snapshot(user_id, session_id)
    items: list[SessionMessageItem] = []
    for m in raw:
        role = str(m.get("role", ""))
        raw_content = str(m.get("content", ""))
        msg_id = build_conversation_message_id(user_id, session_id, role, raw_content, m.get("ts"))
        content_text, original_image_urls, processed_image_urls = split_message_content_and_images(raw_content)
        # assistant/system 历史通常不携带图片块；保持输出干净。
        if role != "user":
            original_image_urls = []
            processed_image_urls = []
        image_urls = list(original_image_urls or processed_image_urls)
        items.append(
            SessionMessageItem(
                message_id=msg_id,
                role=role,
                content=content_text,
                image_urls=image_urls,
                original_image_urls=original_image_urls,
                processed_image_urls=processed_image_urls,
                rag_citations=_session_message_rag_citations(m.get("rag_citations")),
                ts=m.get("ts"),
            )
        )
    return SessionMessagesResponse(
        user_id=user_id,
        session_id=session_id,
        title=str(snap.get("title") or ""),
        title_source=str(snap.get("title_source") or "off"),
        count=len(items),
        messages=items,
    )


@router.delete(
    "/sessions/message",
    response_model=SessionMessageDeleteResponse,
    summary="删除会话中的单条或多条消息",
)
async def delete_session_message(
    user_id: Annotated[str, Query(description="调用方用户 ID")],
    session_id: Annotated[str, Query(description="会话 ID")],
    message_id: Annotated[list[str], Query(description="消息 id 列表（可重复传参，与 GET /sessions/messages 中 message_id 一致）")],
) -> SessionMessageDeleteResponse:
    """
    按 ``message_id`` 删除一条或多条消息（热层 Redis/内存 + 冷层 ES 中同 id 文档）。

    - ``message_id`` 支持重复 query 参数（如 ``?message_id=a&message_id=b``）；
    - 每个 id 须为 64 位十六进制小写字符串；
    - 全部均不存在时返回 404；部分成功返回 200 并在 ``not_found_ids`` 中给出未命中项。
    """
    return _delete_messages_by_ids(user_id, session_id, message_id)


@router.post(
    "/sessions/messages/delete",
    response_model=SessionMessageDeleteResponse,
    summary="批量删除会话消息（Body 传 message_ids）",
)
async def delete_session_messages_batch(body: SessionMessagesDeleteRequest) -> SessionMessageDeleteResponse:
    """
    批量删除会话消息（推荐）：通过 JSON Body 传 ``message_ids`` 列表。

    说明：
    - 与 ``DELETE /sessions/message`` 逻辑一致；
    - 适合网关/SDK 对 DELETE body 支持不稳定的场景。
    """
    return _delete_messages_by_ids(body.user_id, body.session_id, body.message_ids)


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
