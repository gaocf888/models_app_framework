# AI 应用开发接口文档 — 地降所（djs）版本

> **依据**：`地降所项目前端UI及对应接口调用说明.md`  
> **版式**：对齐 `docs/地降所需求及数据相关/AI应用开发接口文档-示例.docx`  
> **范围**：智能问答、智能报告生成、数据查询、知识库（前端主路径接口；运维/排查类从略）  
> **入参**：仅列必要参数（必填 + 页面主路径常用选填）

---

## 文档信息

| 项目 | 内容 |
|------|------|
| 名称 | AI 应用开发接口文档（地降所） |
| 编制日期 | 2026-10-14 |
| 作者 | — |
| 服务基址 | `http://[ip]:8083`（以现场为准） |
| 业务域 | `NL2SQL_BUSINESS_DOMAIN=subsidence`；`CHATBOT_DOMAIN=subsidence` |
| 鉴权 | 除特别说明外，均需 `Authorization: Bearer <SERVICE_API_KEY>` |

## 修订记录

| 日期 | 描述 | 作者 | 版本号 |
|------|------|------|--------|
| 2026-10-14 | 按前端 UI 说明整理四模块接口（仅必要入参） | — | V1.0 |
| 2026-10-14 | 浏览路径改本仓：`GET /tree`、`GET /browse`（分页）；Java 转发 | — | V1.1 |
| 2026-10-14 | 报告生成接口补充「前端调用渲染说明」（按到达顺序画正文/表/图） | — | V1.2 |
| 2026-10-14 | 数据查询解析流与人机协同恢复接口补充衔接说明 | — | V1.3 |
| 2026-10-16 | 感知树改与报告/NL2SQL 同一套：yaml 出站、`t_station` 出区，不再读洗数表补最新值 | — | V1.4 |
| 2026-10-16 | 3.2 及后续接口补充「调用方使用说明」（返回字段与页面加载对应） | — | V1.5 |
| 2026-10-16 | 浏览列表：点站出设备×时间矩阵；点库/区出代表标列表 | — | V1.6 |
| 2026-10-16 | 点库/区改为代表标全部观测（`data_time` 倒序），不再只取最新一行 | — | V1.7 |
| 2026-10-16 | 4.6 摄入任务查询补充 `step` / `status` 中文对照，便于进度页渲染 | — | V1.8 |
| 2026-10-19 | 问答尾帧/历史 `rag_citations` 补充 `original_preview_url`（短时预览）；`original_content_url` 为稳定引用 | — | V1.9 |
| 2026-10-19 | `original_preview_url` 改为 `/chatbot/documents/original/preview` 代理路径，避免外网直连 `models-app-minio` | — | V1.10 |
| 2026-10-19 | 问答尾帧/历史返回字段下补充 `original_preview_url` 调用方使用说明 | — | V1.11 |
| 2026-10-19 | 3.3 点库/区列表去掉年沉降量，累计沉降表头改为沉降量 | — | V1.12 |
| 2026-10-23 | 3.4 / 3.5 解析 SQL 与人机协同恢复改为一次性 JSON（不再 SSE） | — | V1.13 |
| 2026-10-26 | 3.4 补充 SQL 生成/查询失败时的 JSON 字段、常见 `error` 码与前端渲染说明 | — | V1.14 |
| 2026-10-26 | 3.4 明确可用 `ok=false` 统一友好提示；补充推荐文案 | — | V1.15 |

## 通用约定

1. **鉴权**：请求头携带 `Authorization: Bearer <service-api-key>`（密钥由算法侧提供）。  
2. **SSE**：智能问答、自动报告等流式接口响应 `Content-Type: text/event-stream`，每帧 `data: {JSON}\n\n`；消息种类看 JSON 的 `event` / 约定字段。数据查询 **解析 SQL / 人机协同恢复**（3.4、3.5）为一次性 JSON，不是 SSE。  
3. **会话 ID**：`user_id`、`session_id` 由调用方生成并维持；`session_id` 建议 UUID，禁止含 `:`。  
4. **禁止展示**：智能问答 / 报告 / 数据查询路径均**不得**把 SQL 渲染给终端用户（联调字段可忽略）。  
5. **Java 侧**：数据查询感知树、进页默认表、点树刷新列表由算法端 `GET /data-query-agent/tree`、`GET /data-query-agent/browse` 提供；Java 转发调用，不再自建监测表读库。点树 **不要** 打 `run-stream`。

---

## 目录

1. [智能问答](#1-智能问答)  
2. [智能报告生成](#2-智能报告生成)  
3. [数据查询](#3-数据查询)  
4. [知识库](#4-知识库)

---

# 1. 智能问答

侧栏「智能问答」。主路径：`POST /chatbot/chat/stream`（SSE）。  
进程建议：`CHATBOT_DOMAIN=subsidence`。

---

## 1.1 AI 问答流式接口

### 接口功能

用户发送自然语言问题，服务端流式返回回答增量、引用角标与结束元数据（来源列表、推荐问等）。

### URL

`http://[ip]:8083/chatbot/chat/stream`

### 鉴权方式

`Authorization: Bearer <service-api-key>`

### 支持格式

JSON

### HTTP 请求方式

POST

### 请求头

| 请求头 | 必填 | 说明 |
|--------|------|------|
| Authorization | true | `Bearer <service-api-key>` |
| Content-Type | true | `application/json` |

### 请求参数

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| user_id | true | string | 用户标识 |
| session_id | true | string | 会话标识（前端生成并维持） |
| query | true | string | 用户问题 |

### 返回字段（SSE）

| 字段 / 事件 | 类型 | 说明                                                |
|-------------|------|---------------------------------------------------|
| started + stream_id | — | 首帧；保存 `stream_id` 供停止                             |
| delta | string | 返回正文增量（Markdown）                                  |
| citation / citation_ref | — | 知识引用角标，与尾帧中来源列表meta.rag_citations的 `ref_index` 对齐 |
| finished | boolean | 尾帧；携带 `meta`                                      |
| meta.rag_citations | array | 知识来源列表（空则不展示）。项内常用：`ref_index`、`doc_name`、`text_preview`；`original_content_url`（稳定对象引用，常为 `minio://…`）；`original_preview_url`（预览用相对路径，由 BFF/网关打开；前端勿直连 MinIO，亦无需单独对接预览接口） |
| meta.suggested_questions | array | 推荐问；纯查数路径通常为空(前端不渲染)                              |
| meta.nl2sql_sql | string | **禁止渲染(前端不渲染)**                                   |
| error | — | 错误提示                                              |

### 调用方使用说明

来源列表有 `rag_citations` 时：角标 `[n]` 用 `ref_index` 对齐；点开预览用项内 `original_preview_url`（相对路径），**不要**用 `original_content_url`（`minio://…`）当浏览器地址，也**不要**直连集群内 MinIO 主机。

| 字段 | 页面怎么用 |
|------|------------|
| `original_content_url` | 稳定对象引用，仅展示/落库用；浏览器不可直接打开 |
| `original_preview_url` | 预览入口。形态为 `/chatbot/documents/original/preview?ref=…`。前端把该相对路径交给 **BFF/网关** 同源打开（iframe / 新窗口 / `fetch` 后转 blob）；由 BFF 转发并注入 `Authorization: Bearer <service-api-key>`。前端**无需**单独对接预览接口文档或自拼 `ref` |

### 接口示例

请求：

```http
POST /chatbot/chat/stream
Authorization: Bearer <service-api-key>
Content-Type: application/json

{
  "user_id": "u1",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "query": "什么是地面沉降"
}
```

流式响应:

```text
data: {"started": true, "stream_id": "2b119baa9a3c4a56b847ccd3e28fe7b5"}

data: {"delta": "地面", "finished": false}

data: {"delta": "沉", "finished": false}

data: {"delta": "降", "finished": false}

data: {"delta": "是指", "finished": false}

data: {"delta": "由", "finished": false}

data: {"delta": "自然", "finished": false}

data: {"delta": "因素", "finished": false}

data: {"delta": "和", "finished": false}

data: {"delta": "人为", "finished": false}

data: {"delta": "活动", "finished": false}

data: {"delta": "引发", "finished": false}

data: {"delta": "的", "finished": false}

data: {"delta": "松", "finished": false}

data: {"delta": "散", "finished": false}

data: {"delta": "地", "finished": false}

data: {"delta": "层", "finished": false}

data: {"delta": "压缩", "finished": false}

data: {"delta": "、", "finished": false}

data: {"delta": "地面", "finished": false}

data: {"delta": "高", "finished": false}

data: {"delta": "程", "finished": false}

data: {"delta": "降低", "finished": false}

data: {"delta": "的", "finished": false}

data: {"delta": "地质", "finished": false}

data: {"delta": "现象", "finished": false}

data: {"delta": " ", "finished": false}

data: {"citation_ref": 1, "finished": false}

data: {"delta": "。", "finished": false}

data: {"finished": true, "meta": {"used_rag": true, "intent_label": "kb_qa", "retrieval_attempts": 1, "rag_engine": "agentic", "rag_namespace": null, "rag_scope_reason": "locale_kb_disabled", "rag_scope_fallback": false, "faq_soft_direct": true, "faq_soft_direct_reason": "active:rerank_score=0.9987", "history_trim_dropped": 0, "status": "answered", "duration_ms": 4626, "terminate_reason": null, "is_partial": false, "similar_cases_appended": false, "similar_case_namespace": null, "fault_detect_sources": [], "fault_detect_confidence": 0.0, "need_similar_cases": false, "used_nl2sql": false, "nl2sql_failed": null, "nl2sql_error_code": null, "nl2sql_sql": null, "nl2sql_analysis": null, "suggested_questions": ["累计沉降与年沉降速率如何区分解读？", "哪些行政区近期沉降相对偏大？", "地面沉降成因与防控要点有哪些？", "地面沉降的主要成因有哪些？", "哪些地区容易发生地面沉降？"], "rag_citations": [{"ref_index": 1, "namespace": "参考规范", "doc_name": "14-地面沉降和地裂缝光纤监测规程", "doc_version": "v1.0.1", "chunk_id": "549b16a4-6f16-4e79-a92e-18f78618c097", "section_path": "3.1", "source": "vector_store", "score": 1.998711, "rerank_score": 0.998711, "pipeline_version": "1.1.0", "text_preview": "## 3.1\n\n地面沉降 land subsidence\n\n由自然因素和人为活动引发的松散地层压缩、地面高程降低的地质现象。", "original_content_url": "minio://rag-assets/rag-docs/参考规范/14-地面沉降和地裂缝光纤监测规程/v1.0.1/original.pdf", "original_preview_url": "/chatbot/documents/original/preview?ref=minio%3A%2F%2Frag-assets%2Frag-docs%2F%E5%8F%82%E8%80%83%E8%A7%84%E8%8C%83%2F14-%E5%9C%B0%E9%9D%A2%E6%B2%89%E9%99%8D%E5%92%8C%E5%9C%B0%E8%A3%82%E7%BC%9D%E5%85%89%E7%BA%A4%E7%9B%91%E6%B5%8B%E8%A7%84%E7%A8%8B%2Fv1.0.1%2Foriginal.pdf"}, {"ref_index": 2, "namespace": "参考规范", "doc_name": "14-地面沉降和地裂缝光纤监测规程", "doc_version": "v1.0.1", "chunk_id": "5cce59f8-12b0-4a29-ba1d-37f4bf9ebc5d", "section_path": "4.2.1 地面沉降和地裂缝监测主要采用的光纤感测技术包括：", "source": "vector_store", "score": 1.358244, "rerank_score": 0.858244, "pipeline_version": "1.1.0", "text_preview": "4.2.1 地面沉降和地裂缝监测主要采用的光纤感测技术包括：\n\na） 基于布里渊散射原理的全分布式光纤感测技术，如BOTDR、BOFDA、BOTDA等，可用于地层变形大小及其分布的监测；\n\nb） 基于光纤布拉格光栅原理的准分布式光纤感测技术，如基于时分复用的FBG一TDM和基于波分复用的FBG一WDM等，可用于变形、温度、水位等的监测，其特点是单点精度高、测试时间短、组网能力强、可实现远程实时监测。", "original_content_url": "minio://rag-assets/rag-docs/参考规范/14-地面沉降和地裂缝光纤监测规程/v1.0.1/original.pdf", "original_preview_url": "/chatbot/documents/original/preview?ref=minio%3A%2F%2Frag-assets%2Frag-docs%2F%E5%8F%82%E8%80%83%E8%A7%84%E8%8C%83%2F14-%E5%9C%B0%E9%9D%A2%E6%B2%89%E9%99%8D%E5%92%8C%E5%9C%B0%E8%A3%82%E7%BC%9D%E5%85%89%E7%BA%A4%E7%9B%91%E6%B5%8B%E8%A7%84%E7%A8%8B%2Fv1.0.1%2Foriginal.pdf"}, {"ref_index": 3, "namespace": "参考规范", "doc_name": "14-地面沉降和地裂缝光纤监测规程", "doc_version": "v1.0.1", "chunk_id": "da64f1d3-01ad-4184-9eb5-25c4134887a8", "section_path": "4.1.1 地面沉降光纤监测内容主要包括：", "source": "vector_store", "score": 1.147386, "rerank_score": 0.814053, "pipeline_version": "1.1.0", "text_preview": "4.1.1 地面沉降光纤监测内容主要包括：\n\na） 岩土体整体竖向变形及其分布；\n\nb） 岩土体局部竖向变形及其分布；\n\nc) 孔隙水压力和地下水位(包括潜水位和承压水位)。", "original_content_url": "minio://rag-assets/rag-docs/参考规范/14-地面沉降和地裂缝光纤监测规程/v1.0.1/original.pdf", "original_preview_url": "/chatbot/documents/original/preview?ref=minio%3A%2F%2Frag-assets%2Frag-docs%2F%E5%8F%82%E8%80%83%E8%A7%84%E8%8C%83%2F14-%E5%9C%B0%E9%9D%A2%E6%B2%89%E9%99%8D%E5%92%8C%E5%9C%B0%E8%A3%82%E7%BC%9D%E5%85%89%E7%BA%A4%E7%9B%91%E6%B5%8B%E8%A7%84%E7%A8%8B%2Fv1.0.1%2Foriginal.pdf"}, {"ref_index": 4, "namespace": "参考规范", "doc_name": "14-地面沉降和地裂缝光纤监测规程", "doc_version": "v1.0.1", "chunk_id": "94d5e889-5bc0-4ecc-abe8-2533552db1fe", "section_path": "引 言", "source": "vector_store", "score": 1.037114, "rerank_score": 0.787114, "pipeline_version": "1.1.0", "text_preview": "## 引 言\n\n监测是地面沉降和地裂缝灾害防治的前提。采用分布式光纤感测技术监测地面沉降和地裂缝灾害具备可行性和技术优势，可对地面形变、地层变形和地裂缝等进行分布式或准分布式监测。 多年实践表明，分布式光纤感测技术在地面沉降和地裂缝监测中成效显著，能够获得高空间分辨率监测结果，可以成为一种常规的地面沉降和地裂缝监测手段。目前，我国没有地面沉降和地裂缝光纤监测相关的技术规程。为了规范光纤感测技术在地面沉降和地裂缝监测领域的应用，支撑地面沉降和地裂缝灾害防治决策，制定本文件。", "original_content_url": "minio://rag-assets/rag-docs/参考规范/14-地面沉降和地裂缝光纤监测规程/v1.0.1/original.pdf", "original_preview_url": "/chatbot/documents/original/preview?ref=minio%3A%2F%2Frag-assets%2Frag-docs%2F%E5%8F%82%E8%80%83%E8%A7%84%E8%8C%83%2F14-%E5%9C%B0%E9%9D%A2%E6%B2%89%E9%99%8D%E5%92%8C%E5%9C%B0%E8%A3%82%E7%BC%9D%E5%85%89%E7%BA%A4%E7%9B%91%E6%B5%8B%E8%A7%84%E7%A8%8B%2Fv1.0.1%2Foriginal.pdf"}, {"ref_index": 5, "namespace": "参考规范", "doc_name": "14-地面沉降和地裂缝光纤监测规程", "doc_version": "v1.0.1", "chunk_id": "8b880434-0488-4480-b1f3-604f48d480b9", "section_path": "6.2.2 地面沉降和地裂缝光纤监测采用的传感器宜具有温度补偿功能。", "source": "vector_store", "score": 0.885318, "rerank_score": 0.685318, "pipeline_version": "1.1.0", "text_preview": "6.2.2 地面沉降和地裂缝光纤监测采用的传感器宜具有温度补偿功能。", "original_content_url": "minio://rag-assets/rag-docs/参考规范/14-地面沉降和地裂缝光纤监测规程/v1.0.1/original.pdf", "original_preview_url": "/chatbot/documents/original/preview?ref=minio%3A%2F%2Frag-assets%2Frag-docs%2F%E5%8F%82%E8%80%83%E8%A7%84%E8%8C%83%2F14-%E5%9C%B0%E9%9D%A2%E6%B2%89%E9%99%8D%E5%92%8C%E5%9C%B0%E8%A3%82%E7%BC%9D%E5%85%89%E7%BA%A4%E7%9B%91%E6%B5%8B%E8%A7%84%E7%A8%8B%2Fv1.0.1%2Foriginal.pdf"}, {"ref_index": 6, "namespace": "参考规范", "doc_name": "14-地面沉降和地裂缝光纤监测规程", "doc_version": "v1.0.1", "chunk_id": "8fb32014-d125-43f4-8476-8a4e4bd3e93e", "section_path": "传感光缆和传感器布设方法", "source": "vector_store", "score": 0.7975, "rerank_score": 0.639286, "pipeline_version": "1.1.0", "text_preview": "## 传感光缆和传感器布设方法\n\nD.1 地面沉降传感光缆和传感器布设应按如下要求进行。 a）传感光缆可在钻孔中构成“U”形回路布设，也可单根布设；一个钻孔中，传感光缆数量不应多于4根。 b）传感光缆的布设深度应与钻孔深度一致，应根据布设方案，准备足够长度的传感光缆。 c）钻孔20m以浅，应布设温度传感光缆，以便对应变监测结果进行温度补偿。 d）监测孔口应预留长度不小于10m的传感光缆引线供监测使用。 e)用于地面沉降监测的传感光缆应进行预拉，预拉量宜在8000με以上，且应在定点传感光缆生产过程中完成。 f) 根据监测要求，可在特定深度布设 FB...", "original_content_url": "minio://rag-assets/rag-docs/参考规范/14-地面沉降和地裂缝光纤监测规程/v1.0.1/original.pdf", "original_preview_url": "/chatbot/documents/original/preview?ref=minio%3A%2F%2Frag-assets%2Frag-docs%2F%E5%8F%82%E8%80%83%E8%A7%84%E8%8C%83%2F14-%E5%9C%B0%E9%9D%A2%E6%B2%89%E9%99%8D%E5%92%8C%E5%9C%B0%E8%A3%82%E7%BC%9D%E5%85%89%E7%BA%A4%E7%9B%91%E6%B5%8B%E8%A7%84%E7%A8%8B%2Fv1.0.1%2Foriginal.pdf"}], "hybrid_degraded": null, "processed_image_urls": [], "original_image_urls": [], "stream_id": "2b119baa9a3c4a56b847ccd3e28fe7b5", "request_id": "6f8777b0-fbfa-4fdd-b57f-a9dfa913f982"}}

```

---

## 1.2 AI 问答停止接口

### 接口功能

中断进行中的流式问答。

### URL

`http://[ip]:8083/chatbot/chat/stop`

### 鉴权方式

`Authorization: Bearer <service-api-key>`

### 支持格式

JSON

### HTTP 请求方式

POST

### 请求头

| 请求头 | 必填 | 说明 |
|--------|------|------|
| Authorization | true | `Bearer <service-api-key>` |
| Content-Type | true | `application/json` |

### 请求参数

| 参数 | 必填 | 类型 | 说明                               |
|------|------|------|----------------------------------|
| user_id | true | string | 用户标识                             |
| session_id | true | string | 会话标识                             |
| stream_id | true | string | 来自 AI问答流式接口首帧`started.stream_id` |

### 返回字段

| 返回字段 | 字段类型 | 说明 |
|----------|----------|------|
| ok | boolean | 是否受理停止 |

### 接口示例

```json
{
  "user_id": "u1",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "stream_id": "<started.stream_id>"
}
```

---

## 1.3 查询用户会话列表接口

### 接口功能

按用户拉取会话目录（标题、更新时间等）。

### URL

`http://[ip]:8083/chatbot/sessions`

### 鉴权方式

`Authorization: Bearer <service-api-key>`

### 支持格式

JSON

### HTTP 请求方式

GET

### 请求头

| 请求头 | 必填 | 说明 |
|--------|------|------|
| Authorization | true | `Bearer <service-api-key>` |

### 请求参数（Query）

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| user_id | true | string | 用户标识 |

### 返回字段

| 返回字段 | 字段类型 | 说明 |
|----------|----------|------|
| sessions | array | 会话列表（含 session_id、title、更新时间等） |

### 接口示例

```http
GET /chatbot/sessions?user_id=u1
Authorization: Bearer <service-api-key>
```

---

## 1.4 查询用户会话消息接口

### 接口功能

拉取指定会话历史消息；助手消息可带 `rag_citations`（与流式尾帧同形；读取时会按稳定引用重新签发 `original_preview_url`）。

### URL

`http://[ip]:8083/chatbot/sessions/messages`

### 鉴权方式

`Authorization: Bearer <service-api-key>`

### 支持格式

JSON

### HTTP 请求方式

GET

### 请求头

| 请求头 | 必填 | 说明 |
|--------|------|------|
| Authorization | true | `Bearer <service-api-key>` |

### 请求参数（Query）

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| user_id | true | string | 用户标识 |
| session_id | true | string | 会话标识 |

### 返回字段

| 返回字段 | 字段类型 | 说明 |
|----------|----------|------|
| messages | array | 历史消息（role / content / 可选 `rag_citations`） |
| messages[].rag_citations | array | 与流式 `meta.rag_citations` 同形；含 `original_content_url`（稳定引用）、`original_preview_url`（预览相对路径，由 BFF 打开；前端无需单独对接预览接口） |

### 调用方使用说明

助手消息若带 `rag_citations`，预览用法与 **1.1** 尾帧相同：用 `original_preview_url` 交 BFF 同源打开并带鉴权；历史读取时服务端会按稳定引用重新签发该字段。勿打开 `original_content_url`，勿直连 MinIO，勿单独对接预览接口。

### 接口示例

```http
GET /chatbot/sessions/messages?user_id=u1&session_id=550e8400-e29b-41d4-a716-446655440000
Authorization: Bearer <service-api-key>
```

---

## 1.5 修改用户会话标题接口

### 接口功能

用户重命名会话标题。

### URL

`http://[ip]:8083/chatbot/sessions/title`

### 鉴权方式

`Authorization: Bearer <service-api-key>`

### 支持格式

JSON

### HTTP 请求方式

PATCH

### 请求头

| 请求头 | 必填 | 说明 |
|--------|------|------|
| Authorization | true | `Bearer <service-api-key>` |
| Content-Type | true | `application/json` |

### 请求参数

Query：

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| user_id | true | string | 用户标识 |
| session_id | true | string | 会话标识 |

Body：

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| title | true | string | 新标题 |

### 返回字段

| 返回字段 | 字段类型 | 说明 |
|----------|----------|------|
| ok | boolean | 是否成功 |

### 接口示例

```http
PATCH /chatbot/sessions/title?user_id=u1&session_id=550e8400-e29b-41d4-a716-446655440000
Content-Type: application/json

{ "title": "朝阳区沉降问询" }
```

---

## 1.6 清空用户会话消息接口

### 接口功能

清空指定会话下全部消息（二次确认后调用）。

### URL

`http://[ip]:8083/chatbot/sessions/messages`

### 鉴权方式

`Authorization: Bearer <service-api-key>`

### 支持格式

JSON

### HTTP 请求方式

DELETE

### 请求头

| 请求头 | 必填 | 说明 |
|--------|------|------|
| Authorization | true | `Bearer <service-api-key>` |

### 请求参数（Query）

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| user_id | true | string | 用户标识 |
| session_id | true | string | 会话标识 |

### 返回字段

| 返回字段 | 字段类型 | 说明 |
|----------|----------|------|
| ok | boolean | 是否成功 |

### 接口示例

```http
DELETE /chatbot/sessions/messages?user_id=u1&session_id=550e8400-e29b-41d4-a716-446655440000
Authorization: Bearer <service-api-key>
```

---

# 2. 智能报告生成

侧栏「自动报告生成」。主路径：`POST /analysis-agent/run-stream`（SSE）。  
进程须(算法端)：`ANALYSIS_AGENT_ENABLED=true`、`NL2SQL_BUSINESS_DOMAIN=subsidence`。

**地降报告模版 `analysis_type`：**
目前地面沉降支持以下报告类型：

| 界面 | analysis_type |
|------|---------------|
| 日报 | `subsidence_daily` |
| 周报 | `subsidence_weekly` |
| 月报 | `subsidence_monthly` |
| 季报 | `subsidence_quarterly` |
| 年报 | `subsidence_yearly` |

---

## 2.1 自动报告生成流式接口

### 接口功能

按模版生成地面沉降监测报告：边取数边推送章节正文、表、图。

### URL

`http://[ip]:8083/analysis-agent/run-stream`

### 鉴权方式

`Authorization: Bearer <service-api-key>`

### 支持格式

JSON

### HTTP 请求方式

POST

### 请求头

| 请求头 | 必填 | 说明 |
|--------|------|------|
| Authorization | true | `Bearer <service-api-key>` |
| Content-Type | true | `application/json` |

### 请求参数

| 参数 | 必填 | 类型 | 说明                                       |
|------|------|------|------------------------------------------|
| user_id | true | string | 用户标识                                     |
| session_id | true | string | 会话标识（前端生成，禁止含 `:`）                       |
| analysis_type | true | string | 上述地降报告模版，五种 `subsidence_*` 之一            |
| options.start_time | false | string | 与 end_time **成对**；`YYYY-MM-DD`；都空=上一完整周期 |
| options.end_time | false | string | 与 start_time 成对；半开区间上界                   |
| options.area | false | string | 标准行政区名（如 `朝阳区`）；空/`全市`/`北京市`=不按区过滤       |
| options.issue_no | false | string | 封面期号；空则封面显示 `XX`                         |
| options.stream_cover | false | boolean | 是否把 `chapter_id=cover` 的封面以 SSE 真流式推出。**默认 `false`（不传即不流式输出封面）**：不推 cover 的 `chapter_start` / `summary_delta` / `chapter_complete`。终稿 `result.summary` 与 `structured_report` **仍含封面**；`chapter_index` / `slot_total` **编号不变**（封面仍占第 0 槽，正文第一章仍为 `1`）。仅匹配 `slot.id==cover`，锅炉红头 `oh_hdr` 不受影响。官方地降页应按日/周/月红头、季/年封面+目录本地绘制，或显式传 `true` |

### 返回字段（SSE，按出现顺序）

| 事件 | 说明 |
|------|------|
| started | `stream_id`、`request_id`；地降可含 `period.period_label` |
| analysis_agent_meta | 章节总数等 |
| analysis_agent_nl2sql_done | 取数进度；**忽略 sql** |
| analysis_agent_chapter_start | 新章节开始 |
| analysis_agent_table_payload | 表：`table.columns` + `table.rows` |
| analysis_agent_chart_payload | 图：`chart.chart_type`（bar/pie/line/dual_axis/map_placeholder） |
| analysis_agent_summary_delta | 章节正文增量 |
| analysis_agent_chapter_complete | 章节完成 |
| analysis_agent_report_complete / analysis_agent_finished | 终稿；`result.summary` 为全文 Markdown |
| analysis_agent_cancelled / analysis_agent_error | 取消或失败 |

### 前端调用渲染说明
调用方接收加载说明：
    1）监听 analysis_agent_chapter_start 和 analysis_agent_chapter_complete event（这是报告中一个章节的开始和结束），按照analysis_agent_chapter_start 中的 chapter_index（章节序号）数值从小到大即为章节顺序。默认 `options.stream_cover=false` 时 **不会** 收到 `chapter_id=cover` 的起止事件，首个正文事件的 `chapter_index` 仍从 `1` 起（封面占第 0 槽但不推流）；不要因此重排章号
	2）analysis_agent_chapter_start 和 analysis_agent_chapter_complete event 中间按照SSE **到达顺序**渲染章节的文本信息
	3）报告中除了文本信息，还包括表格(即表格数据，对应流的event为 analysis_agent_table_payload)和图表(即折线/柱状图和图片，对应流的event为 analysis_agent_chart_payload)，表格和图表使用流中slot_id来分辨所属章节(与章节开始event analysis_agent_chapter_start中的slot_id进行匹配)
	4）封面：默认需前端按模版本地画红头/目录（日/周/月=内部材料+标题+第 {issue_no} 期+单位+落款日；季/年=标题+（period_label）+目录+单位+落款日，目录可用后续 `chapter_start.title` 累积）。年报套季报封面。若传 `options.stream_cover=true` 则封面与其它章一样走 SSE。终稿 `result.summary` 始终含封面 Markdown，导出可直接用，不要用它回写已画正文


正文、表、图渲染说明。

- **正文**：`analysis_agent_summary_delta`。每来一帧把 `text` **追加**到当前章（Markdown）。可多次。
- **表**：`analysis_agent_table_payload`。用 `table.columns` + `table.rows` 画表；不要从正文 Markdown 里再拆表。
- **图**：`analysis_agent_chart_payload`。按 `chart.chart_type` 画：`bar`(柱状图) / `pie`(饼图) / `line`(折线图) / `dual_axis`(双轴图) / `map_image`(地图图片)； `map_image`为静态图片(沉降数据的地图分布图片)，通过image_base64 + image_mime 当 PNG加载图片。

表/图也是 **来一帧画一帧**。收到 `chapter_start` 时用 `slot_id`（= `chapter_id`）新开一节；之后该章的表、图、正文都挂到这个 `slot_id`。同一章里表/图 **常常比正文先到**，常见顺序：

```text
chapter_start
  → table_payload / chart_payload   （可能多张）
  → summary_delta …                 （正文按序追加）
  → chapter_complete
```

其它：`started` 保存 `stream_id`；`nl2sql_done` 只做取数进度、**不展示 sql**；终帧 `result.summary` 给导出/校对（含封面），不必用它替换已画正文。默认不流式输出封面时，报告区封面须本地绘制，不要等 cover 的 `summary_delta`。

### 接口示例

```json
{
  "user_id": "u1",
  "session_id": "s1",
  "analysis_type": "subsidence_quarterly",
  "query": "",
  "options": {
    "start_time": "",
    "end_time": "",
    "area": "",
    "issue_no": "03"
  }
}
```

不传 `options.stream_cover`（或传 `false`）时，上述请求 **不会** 收到 `chapter_id=cover` 的 SSE；终稿 `result.summary` 仍以封面 Markdown 开头。需要后端推封面时在 `options` 加 `"stream_cover": true`。

---

## 2.2 自动报告停止接口

### 接口功能

停止进行中的报告生成。

### URL

`http://[ip]:8083/analysis-agent/stream/stop`

### 鉴权方式

`Authorization: Bearer <service-api-key>`

### 支持格式

JSON

### HTTP 请求方式

POST

### 请求头

| 请求头 | 必填 | 说明 |
|--------|------|------|
| Authorization | true | `Bearer <service-api-key>` |
| Content-Type | true | `application/json` |

### 请求参数

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| user_id | true | string | 用户标识 |
| session_id | true | string | 会话标识 |
| stream_id | true | string | 来自 `started.stream_id` |

### 返回字段

| 返回字段 | 字段类型 | 说明 |
|----------|----------|------|
| ok | boolean | 是否受理停止 |

### 接口示例

```json
{
  "user_id": "u1",
  "session_id": "s1",
  "stream_id": "<started.stream_id>"
}
```

---

# 3. 数据查询

侧栏「数据查询」。算法端前缀：`/data-query-agent/*`。  
进程须：`DATA_QUERY_AGENT_ENABLED=true`、`NL2SQL_BUSINESS_DOMAIN=subsidence`。  
浏览路径（进页树 / 默认表 / 点树刷新）走 `GET /tree`、`GET /browse`；解析 SQL 走 `POST /run-stream`（一次性 JSON）。开关关闭时本组接口 **503**（不是 404）。

**监测库 `library_id`（请求用英文代号）：** `fcb` / `jyb` / `dxswj` / `kxsylj` / `gnss` / `qxz` / `gq`。  
站点稳定键 `station_id` = 场地 `project_name`（如 `F28(金盏)`），与 `t_station.name`、`GET /hud` 的 `entity_id` 同源。进页默认表不传 `library_id` 即分层标 `fcb`（一库一表，不做 fcb+jyb UNION）。

---

## 3.1 查询监测库注册表接口(本项目不需要)

### 接口功能

拉取七种监测类型名单（选库弹窗 / 与树展示名校验）。

### URL

`http://[ip]:8083/data-query-agent/libraries`

### 鉴权方式

`Authorization: Bearer <service-api-key>`

### 支持格式

JSON

### HTTP 请求方式

GET

### 请求头

| 请求头 | 必填 | 说明 |
|--------|------|------|
| Authorization | true | `Bearer <service-api-key>` |

### 请求参数

无（无必要 Query）。

### 返回字段

| 返回字段 | 字段类型 | 说明 |
|----------|----------|------|
| libraries / groups | array | 库列表与分组（含 id、display_name 等） |

### 接口示例

```http
GET /data-query-agent/libraries
Authorization: Bearer <service-api-key>
```

---

## 3.2 查询感知树(业务左侧监方式站点树)接口

### 接口功能

拉取左侧感知树：监测库 → 行政区 → 站点（含站点数）。站点名单来自 `device_station_map.yaml`（与报告/NL2SQL 同一套官方覆盖），行政区来自 `t_station.area`（全角括号/F 码对齐后再匹配）。**不读洗数表**，树节点 **不带** 每站最新观测值。GNSS 一期覆盖为空则该枝 0 站。进页画树；可选只拉某一库子树。不走 `run-stream`。树是站点粒度，不做时间序列分页。右侧表的最新值仍走 `GET /browse`。

### URL

`http://[ip]:8083/data-query-agent/tree`

### 鉴权方式

`Authorization: Bearer <service-api-key>`

### 支持格式

JSON

### HTTP 请求方式

GET

### 请求头

| 请求头 | 必填 | 说明 |
|--------|------|------|
| Authorization | true | `Bearer <service-api-key>` |

### 请求参数（Query）

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| library_id | false | string | 只返回该库子树（如 `jyb`）；空=七库整棵树 |

### 返回字段

| 返回字段 | 字段类型 | 说明 |
|----------|----------|------|
| ok | boolean | 成功 |
| default_library_id | string | 默认库，固定 `fcb` |
| libraries | array | 库节点：`library_id`、`display_name`、`group`、`station_count`、`districts` |
| libraries[].districts | array | 行政区：`district`、`station_count`、`stations` |
| libraries[].districts[].stations | array | 站点：`station_id`、`station_name`、`district` 来自 yaml 权威名 + `t_station.area`；`latest_value` / `data_time` 固定 `null`（树不读洗数表）；`unit` 为库配置单位 |

### 调用方使用说明

左侧感知树按 **分组 → 监测库 → 行政区 → 站点** 四层加载。进页一次拉整棵树（不传 `library_id`）；树上只展示覆盖名单与区划，**不要**用 `latest_value` 画观测值（最新值在点树后的右侧表）。

| 返回字段 | 树上怎么加载 |
|----------|--------------|
| `ok` | `false` 时不要用空树覆盖上次成功的树，按错误提示 |
| `default_library_id` | 进页默认高亮/选中的库，固定 `fcb`（分层标） |
| `libraries[]` | 第二层：一种监测类型一个库节点，按数组顺序画 |
| `libraries[].group` | 最外层分组标题。相同 `group` 的库画在同一组下（岩土本底层压实 / 流体赋存及应力 / 星地综合观测） |
| `libraries[].display_name` | 库节点上写的中文（分层标、基岩标、地下水…） |
| `libraries[].library_id` | 节点稳定 ID。点库刷新表、解析 SQL 都传这个（`fcb`），**不要传中文** |
| `libraries[].station_count` | 该库下站点数，可显示在库节点旁 |
| `libraries[].districts[]` | 第三层：该库下的行政区节点 |
| `districts[].district` | 区节点展示名（如 `朝阳区`）。点区时原样传给 `GET /browse` 的 `district`。`未分区` 表示 yaml 有站但未对上 `t_station.area` |
| `districts[].station_count` | 该区站点数，可显示在区节点旁 |
| `districts[].stations[]` | 第四层：该区下的站点 |
| `stations[].station_name` | 站节点上写的字（如 `F28(金盏)`） |
| `stations[].station_id` | 点站刷新表、详参 `entity_id` 用这个，与树上展示名同源 |
| `stations[].district` | 与父区节点相同，一般不用再画 |
| `stations[].latest_value` / `data_time` | 固定 `null`，树上不要当观测值渲染 |
| `stations[].unit` | 库配置单位，树上可不展示 |

点击行为（**不要**打 `run-stream`）：

| 用户点 | 树上 | 再调 |
|--------|------|------|
| 库节点 | 高亮该 `library_id` | `GET /browse?library_id=` |
| 区节点 | 高亮该区 | `GET /browse?library_id=&district=` |
| 站节点 | 高亮该站 | `GET /browse?library_id=&station_id=` |

`station_count=0`（如 GNSS 一期）只画库节点，不要造空区/空站。只传 `library_id=jyb` 时只渲染基岩标这一枝。

### 接口示例

```http
GET /data-query-agent/tree
Authorization: Bearer <service-api-key>

GET /data-query-agent/tree?library_id=jyb
Authorization: Bearer <service-api-key>
```

---

## 3.3 浏览列表接口

### 接口功能

进页默认表、点树刷新右侧表。**两种视图**（看有没有传 `station_id`）：

1. **点树中监测方式 / 行政区**（不传 `station_id`）：查询范围内 **代表标**（分层标=层位 0）的 **全部观测行**，按 `data_time` **倒序**；表头是测位/站名/行政区/沉降量/时间（**无年沉降量**），**不要**用设备 `station_name` 当列。  
2. **点树中站点**（传 `station_id`）：该场地 **全部设备** 的监测序列。行= `data_time`（新→旧），列=设备 `station_name`（如 `F9-1`、`F9-1T`），单元格=`total_settle`（其它库用该库核心指标）。

强制分页。不走 `run-stream`。代表标表上点「详参」再调 `GET /hud`；设备矩阵不带详参。

### URL

`http://[ip]:8083/data-query-agent/browse`

### 鉴权方式

`Authorization: Bearer <service-api-key>`

### 支持格式

JSON

### HTTP 请求方式

GET

### 请求头

| 请求头 | 必填 | 说明 |
|--------|------|------|
| Authorization | true | `Bearer <service-api-key>` |

### 请求参数（Query）

| 参数 | 必填 | 类型 | 说明                                                |
|------|------|------|---------------------------------------------------|
| library_id | false | string | 监测库；空=默认分层标 `fcb`。点库节点时必传该节点 id                   |
| district | false | string | 点行政区时传标准名，如 `朝阳区`。与 `station_id` 同时传时忽略（以点站为准） |
| station_id | false | string | 点站点时传树上的 `station_id`（`project_name`，如 `F9(后芮营)`） |
| page | false | int | 页码，从 1；默认 1                                       |
| page_size | false | int | 每页行数，默认 20，最大 100。点站时按 **监测时间** 条数分页 |

### 返回字段

| 返回字段 | 字段类型 | 说明 |
|----------|----------|------|
| ok | boolean | 成功 |
| library_id | string | 实际锁定的库 |
| display_name | string | 库中文名 |
| district / station_id | string | 回显过滤条件；未传为 null |
| result_grain | string | `station`=代表标列表；`device_time`=设备×时间矩阵 |
| page / page_size / total | int | 分页。`station`：`total`=代表标观测行数（不是站点数）；`device_time`：`total`=该场地不重复 `data_time` 数 |
| has_more | boolean | 是否还有下一页 |
| columns | array | `{key, title}`。两种视图表头不同，见下方说明 |
| list | array | 当前页行；单元格取 `list[i][columns[j].key]` |
| list[].hud_entity_id | string | 仅 `result_grain=station`：点「详参」作 `GET /hud` 的 `entity_id` |
| hud_enabled | boolean | 仅代表标列表为 true；设备矩阵固定 false |

### 调用方使用说明

用 `result_grain` 切换右侧表布局，**不要写死列**。点树 **不要** 打 `run-stream`。

**A. 点库 / 点行政区**（`result_grain=station`，进页默认也是这种）

范围内代表标的全部观测，按 `data_time` 从新到旧。分层标只取层位 0 代表标，不是该站所有设备。表头按返回的 `columns` 渲染：测位 ID / 监测站名称 / 行政区 / **沉降量** / 数据时间。**不要**再画年沉降量列（当前只有实测沉降量，没有年沉降量）。同一站会有多行。

| 返回字段 | 表上怎么加载 |
|----------|--------------|
| `columns[]` | `title` 当表头，`key` 取格。不要用设备编号当列；`total_settle_mm` 的 `title` 为「沉降量(mm)」 |
| `list[].station_id` / `station_name` / `area` | 测位、站名（场地名如 `F8(周村)`）、行政区 |
| `list[].total_settle_mm` | 该行代表标的沉降量（即洗数表 `total_settle`）。**无** `annual_settle_mm` |
| `list[].data_time` | 该行观测时间（整表已按此倒序） |
| `list[].hud_entity_id` | 点「详参」→ `GET /hud` |
| `hud_enabled` | `false` 时整表不显示「详参」 |
| `page` / `total` | 按 **观测行数** 翻页（同一站多行） |

**B. 点站点**（`result_grain=device_time`）

该场地全部设备（含辅助标）的时间序列，转成矩阵：行是时间，列是设备名。

| 返回字段 | 表上怎么加载 |
|----------|--------------|
| `columns[0]` | 固定 `data_time` / 监测时间 |
| `columns[1…]` | 每个设备一列，`key`=`title`=`station_name`（`F9-1`、`F9-1T`…已按编号排序） |
| `list[]` | 一行一个 `data_time`（新→旧）。`list[i]["F9-1"]` 即该时刻该设备的 `total_settle`；缺测为空 |
| `hud_enabled` | 固定 `false`，不要在矩阵行上画「详参」 |
| `page` / `total` | 按 **监测时间条数** 翻页，不是按站点数 |

进页：`GET /browse?page=1&page_size=20`（全北京分层标代表标全部观测，时间倒序）。点「基岩标」带 `library_id=jyb`。点「朝阳区」再加 `district=朝阳区`。点某个站只传 `library_id` + `station_id`。翻页只改 `page` / `page_size`。

### 接口示例

```http
# 默认fcb全部
GET /data-query-agent/browse?page=1&page_size=20
Authorization: Bearer <service-api-key>

# 指定库点击行政区
GET /data-query-agent/browse?library_id=fcb&district=朝阳区&page=1&page_size=20
Authorization: Bearer <service-api-key>

# 指定库点击站点
GET /data-query-agent/browse?library_id=fcb&station_id=F28(金盏)&page=1&page_size=20
Authorization: Bearer <service-api-key>
```

进页不传 `library_id` = 全北京分层标代表标全部观测（时间倒序）。点「基岩标」传 `library_id=jyb`。点站时 `result_grain=device_time`。翻页只改 `page` / `page_size`。

---

## 3.4 数据查询解析接口

### 接口功能

自然语言「解析 SQL」：锁定监测库、生成并执行查询，**一次性 JSON** 返回结果表（含行详情 HUD）或选库人机协同。耗时主要在服务端生成 SQL，不再边算边推 SSE。

### URL

`http://[ip]:8083/data-query-agent/run-stream`

路径名保留 `run-stream`（Java / 网关不用改地址）。响应是普通 JSON，`Content-Type: application/json`，**不是** `text/event-stream`。

### 鉴权方式

`Authorization: Bearer <service-api-key>`

### 支持格式

JSON

### HTTP 请求方式

POST

### 请求头

| 请求头 | 必填 | 说明 |
|--------|------|------|
| Authorization | true | `Bearer <service-api-key>` |
| Content-Type | true | `application/json` |

### 请求参数

| 参数 | 必填 | 类型 | 说明                                |
|------|------|------|-----------------------------------|
| user_id | true | string | 用户标识                              |
| session_id | true | string | 会话标识；禁止含 `:`                      |
| query | true | string | 顶栏自然语言；空串会 **422**                |
| library_id | false | string | 左侧树当前选中的库（如 `fcb`）；未选库可省略(默认 fcb) |
| district | false | string | 树点到行政区且同时解析时传入；以树为准               |
| station_id | false | string | 树点到站点且同时解析时传入；以树为准                |

入参说明：
library_id(监测类型库) -- 传入左侧树当前选中监测类型节点(一级)的 library_id
district(行政区) -- 传入左侧树当前选中行政区的 district
station_id(监测站点) -- 传入左侧树当前选中站点的 station_id

上述三级均未选中时，监测类型库、行政区、监测站点由算法端自动解析，library_id(监测类型库)算法端解析失败时会进入 HITL 模式。
HITL 人机交互说明：
1. 触发 HITL 后，本接口 **同一份 JSON** 里 `status=interrupted`，`event=data_query_library_input_required`
2. 前端弹出监测方式库列表（`library_options`）供用户选择
3. 用户选择确定后，调用 3.5 数据查询人机协同恢复接口继续

业务结果（成功 / 需选库 / 失败 / 取消）HTTP 均为 **200**，用 `status` / `event` 区分；参数不合法仍 **422**，开关关闭 **503**。

### 返回字段

公共字段：

| 返回字段 | 字段类型 | 说明 |
|----------|----------|------|
| ok | boolean | `success` / `interrupted` / `cancelled` 为 true；`error` 为 false |
| status | string | `success` / `interrupted` / `error` / `cancelled` |
| event | string | 见下表，便于按一种结果分支渲染 |
| request_id | string | 本次解析编号；选库后续仍用同一编号 |

按 `status` / `event`：

| status | event | 页面怎么加载 |
|--------|-------|--------------|
| `success` | `data_query_result` | 换右侧表：`columns` 当表头，`list` 当行，单元格取 `list[i][columns[j].key]`。用 `library_id` 高亮左侧树。点「详参」读 `hud_by_entity[该行 hud_entity_id]`，站点找不到再用 `hud_by_station`。**不要**再调 `GET /hud` |
| `interrupted` | `data_query_library_input_required` | 弹出选库窗；右侧 **还没有新表**。见下方衔接 |
| `error` | `data_query_error` | **SQL 生成或查询失败**等同此分支。按钮恢复；提示 `message` / `error`；字段与渲染见下文「失败返回」。不要清空上一张表除非产品要求 |
| `cancelled` | `data_query_cancelled` | 按钮恢复；不要出表 |

`success` 时还有：`library_id`、`display_name`、`table`、`source`（`request` / `parsed` / `default` / `llm` / `hitl`）、`scope`、`time`、`parse_mode`、`result_grain`、`hud_enabled`、`warnings`。`scope` / `time` 可做「朝阳区 / 近一年」芯片（本页不做过滤器也可忽略）。**禁止展示 sql**。

`interrupted` 时还有：`prompt`、`library_options`、`resume_token`、`interrupt_reason`、`candidates`、`query`、`suggested_actions`。

`hud_enabled` / 行上 `hud_available` 控制「详参」按钮显隐。

`warnings` 常见值做成表下小字即可：`library_defaulted`（已默认分层标）、`library_conflict_nl_ignored`（问句里的库被树覆盖）、`hud_series_truncated`（后面的行没有详参）、`hud_series_failed`（表有了但曲线没有）。

### 失败返回（SQL 生成失败 / 查询执行失败）— 前端必读

业务失败（含 **NL2SQL 生成失败**、**SQL 执行失败**、库目录异常、运维问句拒答等）HTTP 仍为 **200**，体为：

| 返回字段 | 字段类型 | 说明 |
|----------|----------|------|
| ok | boolean | 固定 `false` |
| status | string | 固定 `error` |
| event | string | 固定 `data_query_error` |
| request_id | string | 本次解析编号（联调/日志） |
| error | string | **机读错误码**（分支、埋点用；见下表） |
| message | string | **给人看的说明**（优先展示给用户；可能与 `error` 同文或更具体） |
| gen_fail_reason | string \| null | 生成阶段机读原因（如 `link_failed:…`）；执行阶段失败时常为 `null` |
| sql | string \| null | 失败时可能带上已生成/已尝试的 SQL；**禁止展示给终端用户**（仅联调/网关日志） |

**不要**用 HTTP 4xx/5xx 判断「解析没出表」：参数问题才是 422，开关关闭才是 503；其余解析失败都看 `ok=false`（等价于 `status=error`）。

**`ok` 怎么用（推荐前端主路径）**：

| ok | status | 前端怎么做 |
|----|--------|------------|
| `true` | `success` | 出表 |
| `true` | `interrupted` | **不是失败**，弹选库（见 HITL） |
| `true` | `cancelled` | 用户取消，可静默或轻提示「已取消」 |
| `false` | `error` | **生成或执行失败**（含拒答、配置异常等），出统一友好提示即可 |

主路径只需判断 **`ok === false`**，不必解析 `error` / `gen_fail_reason`（那些留给埋点、联调）。推荐 **统一用户提示**（文案可原样使用）：

> 暂时无法完成这次数据查询。请换个说法，或先在左侧选择监测类型、行政区或站点后再试。

前端渲染建议：

1. 按钮从「解析中…」恢复可点。  
2. Toast / 顶栏展示上列统一文案（或产品定稿的等价句）；**不要**把 `message` / `error` 原文直接给用户（多为机读串）。  
3. **不要清空**上一张浏览/解析表（除非产品明确要求）。  
4. **不要**打开选库窗（那是 `ok=true` 且 `interrupted`）；也不要调 3.5。  
5. **不要**渲染 `sql`。  
6. 详参 HUD、芯片、warnings 本轮都没有，忽略即可。  
7. `error` / `message` / `gen_fail_reason` 可打到控制台或上报，便于排查。

可选：若产品要区分运维拒答，可在 `ok=false` 且 `error=ops_use_chatbot` 时改提示为「运维类问题请改用智能问答」；其余失败仍用统一文案。

说明：

- **生成失败**：`gen_fail_reason` 往往有值；`sql` 可能为空串或半成品。  
- **执行失败**：`gen_fail_reason` 多为 `null`；`sql` 常有完整语句（仍勿展示）。  
- 列表失败即整轮 `ok=false`；HUD 时序单独失败不会把整轮打成失败（可能只在成功表上出 `hud_series_failed` 等 warning）。

### 调用方使用说明

仅顶栏点「解析 SQL」时调用。空 `query` 会 422，应拦在按钮上。请求发出后按钮保持「解析中…」直到 JSON 返回（可能要等 LLM 生成 SQL）。不要按 SSE 拆帧，也不再保存 `stream_id`。离开页可中断本次 HTTP 请求。收到 `status=error` 按上一节渲染，不要当成网络错误重试死循环（可提示用户改问句后再点一次）。

### 与人机协同恢复接口的衔接

多数问句会直接 `status=success`（`event=data_query_result`），**不必**再调恢复接口。

只有返回 **`status=interrupted`**（`event=data_query_library_input_required`，问不清查哪类监测、要用户选库）时：

1. 本请求已经结束，不要再等结果表。  
2. 用返回里的 `prompt`、`library_options` 弹出选库（选项不要写死）。  
3. 用户选定后，再 `POST /data-query-agent/resume-stream`，带上：

| 恢复接口入参 | 从哪来 |
|--------------|--------|
| `user_id` / `session_id` | 与本次 `run-stream` **相同** |
| `resume_token` | 本响应返回的 `resume_token`（必传） |
| `library_id` | 用户从 `library_options[].id` 里点的那一项（如 `dxswj`） |
| `abort` | 用户取消选库时传 `true`（可不传 `library_id`） |

恢复接口同样是一次性 JSON，不是接着原连接推。选完后看 `status=success` 出表。树已经点了库、请求里带了合法 `library_id` 时，一般不会出选库结果。

### 接口示例

请求：

```json
{
  "user_id": "u1",
  "session_id": "s1",
  "query": "朝阳区年沉降比较大的监测点",
  "library_id": "fcb"
}
```

成功响应（节选）：

```json
{
  "ok": true,
  "status": "success",
  "event": "data_query_result",
  "request_id": "<id>",
  "library_id": "fcb",
  "source": "request",
  "result_grain": "station",
  "hud_enabled": true,
  "columns": [{ "key": "station_id", "title": "测位 ID" }],
  "list": [{ "station_id": "JZGZ", "hud_entity_id": "JZGZ", "hud_available": true }],
  "hud_by_entity": {}
}
```

需选库响应（节选）：

```json
{
  "ok": true,
  "status": "interrupted",
  "event": "data_query_library_input_required",
  "request_id": "<id>",
  "resume_token": "<token>",
  "prompt": "请选择要查询的监测类型",
  "library_options": [{ "id": "fcb", "display_name": "分层标" }]
}
```

SQL 生成失败响应（节选）：

```json
{
  "ok": false,
  "status": "error",
  "event": "data_query_error",
  "request_id": "<id>",
  "error": "link_failed:forced_table_not_in_allowlist",
  "message": "link_failed:forced_table_not_in_allowlist",
  "gen_fail_reason": "link_failed:forced_table_not_in_allowlist",
  "sql": null
}
```

SQL 查询执行失败响应（节选）：

```json
{
  "ok": false,
  "status": "error",
  "event": "data_query_error",
  "request_id": "<id>",
  "error": "column \"foo\" does not exist",
  "message": "column \"foo\" does not exist",
  "gen_fail_reason": null,
  "sql": "SELECT ..."
}
```

> 上两例中的 `sql` 仅供联调；正式前端**不得**展示。用户提示用 `message`（或按上表对 `error` 做兜底文案）。

---

## 3.5 数据查询人机协同恢复接口

### 接口功能

选库弹窗确认（或放弃）后继续出表。仅在 3.4 返回 `status=interrupted`（`event=data_query_library_input_required`）之后调用；未出现该结果不要打本接口。

### URL

`http://[ip]:8083/data-query-agent/resume-stream`

路径名保留 `resume-stream`。响应同样是一次性 JSON，不是 SSE。

### 鉴权方式

`Authorization: Bearer <service-api-key>`

### 支持格式

JSON

### HTTP 请求方式

POST

### 请求头

| 请求头 | 必填 | 说明 |
|--------|------|------|
| Authorization | true | `Bearer <service-api-key>` |
| Content-Type | true | `application/json` |

### 请求参数

| 参数 | 必填 | 类型 | 说明                                                 |
|------|------|------|----------------------------------------------------|
| user_id | true | string | 须与 run-stream 相同                                   |
| session_id | true | string | 须与 run-stream 相同                                   |
| resume_token | true | string | 来自 3.4 选库响应的 `resume_token` |
| library_id | true* | string | 用户选中的库；`abort=true` 时可省略                           |
| abort | false | boolean | `true` 表示放弃选库                                      |

\*确认选库时必填。`resume_token`、`user_id`、`session_id` 必须来自上一轮 `run-stream` 的选库结果，不能新造。

### 返回字段

与 3.4 同一套 JSON：`ok` / `status` / `event` / `request_id`，再按 `status` 带表或带选库字段。恢复后 NL2SQL 生成/执行失败时同样 `status=error`、`event=data_query_error`，字段与渲染同 3.4「失败返回」（含 `error` / `message` / `gen_fail_reason` / `sql`）。

若 `library_id` 非法，可能再次 `status=interrupted`（同一 `resume_token`），继续弹窗即可。`abort=true` 时 `status=cancelled`。选库次数用尽可能 `error=hitl_retry_exhausted`；`resume_token` 无效为 `invalid_resume_token`（均 HTTP 200 + `data_query_error`）。

### 调用方使用说明

本接口是 **另一次 HTTP JSON 请求**，不是接着 3.4 的连接推。未收到选库结果不要调用。选库窗选项必须用上一轮 `library_options`，不要写死七个类型。

| status / event | 页面怎么加载 |
|----------------|--------------|
| `success` / `data_query_result` | 与 3.4 相同：高亮刚选的库、换表 + `hud_by_entity` 点详参，勿再调 `GET /hud` |
| `interrupted` / `data_query_library_input_required` | `library_id` 非法；同一 `resume_token` 继续弹窗 |
| `cancelled` / `data_query_cancelled` | 用户 `abort=true`；关弹窗、按钮恢复，不要出表 |
| `error` / `data_query_error` | 提示失败，按钮恢复 |

### 与解析接口的衔接

| 步骤 | 做什么 |
|------|--------|
| 1 | 3.4 返回 `status=interrupted`，保存 `resume_token`，用 `library_options` 画下拉 |
| 2 | 用户点确定：本接口 Body 传同一 `user_id`、`session_id`、`resume_token`，以及所选 `library_id` |
| 3 | 用户点取消：同上，但 `abort=true`，本轮 `status=cancelled` |

### 接口示例

```json
{
  "user_id": "u1",
  "session_id": "s1",
  "resume_token": "<token>",
  "library_id": "dxswj"
}
```

---

## 3.6 数据查询停止接口（解析主路径不再使用）

### 接口功能

兼容保留。3.4 / 3.5 已改为一次性 JSON，响应里 **不再下发 `stream_id`**，前端主路径 **不要再调本接口**。离开页或用户点取消：中断进行中的 HTTP 请求即可。选库放弃走 3.5 `abort=true`。

### URL

`http://[ip]:8083/data-query-agent/stream/stop`

### 鉴权方式

`Authorization: Bearer <service-api-key>`

### 支持格式

JSON

### HTTP 请求方式

POST

### 请求头

| 请求头 | 必填 | 说明 |
|--------|------|------|
| Authorization | true | `Bearer <service-api-key>` |
| Content-Type | true | `application/json` |

### 请求参数

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| user_id | true | string | 用户标识 |
| session_id | true | string | 会话标识 |
| stream_id | true | string | 旧版 SSE `started.stream_id`；现行解析 JSON 无此字段 |

### 返回字段

| 返回字段 | 字段类型 | 说明 |
|----------|----------|------|
| ok | boolean | 是否受理停止 |

### 调用方使用说明

新接入不要依赖本接口。解析按钮在 JSON 返回前保持「解析中…」；用户取消选库用 3.5 `abort=true`。

### 接口示例

```json
{
  "user_id": "u1",
  "session_id": "s1",
  "stream_id": "<legacy stream_id>"
}
```

---

## 3.7 行详情 HUD 接口（浏览路径） -- 暂时不用，后续需要扩展HUD页面再考虑

### 接口功能

仅用于 `GET /browse` 点树列表上点「详参」时补拉曲线/指标。解析 SQL 出表后**不要**再调本接口（详情已在 `data_query_result.hud_by_entity`）。

### URL

`http://[ip]:8083/data-query-agent/hud`

### 鉴权方式

`Authorization: Bearer <service-api-key>`

### 支持格式

JSON

### HTTP 请求方式

GET

### 请求头

| 请求头 | 必填 | 说明 |
|--------|------|------|
| Authorization | true | `Bearer <service-api-key>` |

### 请求参数（Query）

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| library_id | true | string | 该行监测类型（如 `fcb`） |
| entity_type | true | string | `station` / `district` / `city` |
| entity_id | true | string | 站=`GET /browse` 行上的 `station_id`（`project_name`，如 `F28(金盏)`）；区=`朝阳区`；市=`beijing` |

### 返回字段

| 返回字段 | 字段类型 | 说明 |
|----------|----------|------|
| hud | object | 与解析结果中单行详情同结构（title / core_metrics / series 等） |

### 调用方使用说明

仅 `GET /browse` 出的表上点「详参」时调用。把返回的 `hud` 交给和解析路径同一套详情浮层（不跳走）。解析 SQL 出表后详情已在 `data_query_result.hud_by_entity`，**不要**再调本接口。

| 返回字段 | 详情面板怎么加载 |
|----------|------------------|
| `found=false` | 提示暂无详情，不要画空折线冒充 |
| `hud.title` | 浮层标题（如「金盏 · 分层标」） |
| `hud.core_metrics[]` | 顶部指标卡（累计沉降/埋深等，随库变） |
| `hud.series.points` | 主折线：`t` 横轴、`v` 纵轴；区/市是日平均（`series.agg=avg`），不要拿某一个站的曲线画成朝阳区或全市 |
| `hud.series_list[]` | 气象等多条曲线时按此画（气温/降水） |
| `hud.identity` | 站名、行政区等辅助信息 |
| `hud.blocks.geology` / `strategy` | `unavailable` 时不画该块（库无岩性/第 N 层） |
| `warnings` 含 `hud_series_failed` | 表还在，详情里折线为空，提示「时序暂不可用」 |
| `sql` | **禁止渲染** |

入参：`library_id` = 当前表所在库；`entity_type` 用该行 `hud_entity_type`（浏览表站点行为 `station`）；`entity_id` = 行上 `hud_entity_id`（站=`station_id`，区=`朝阳区`，市=`beijing`）。

### 接口示例

```http
GET /data-query-agent/hud?library_id=fcb&entity_type=station&entity_id=F28(金盏)
Authorization: Bearer <service-api-key>
```

---

# 4. 知识库

侧栏「知识库」。分类为扁平 `namespace`（如 `法规`）；侧栏排除 NL2SQL 三库。  
地降所：`RAG_REQUIRE_NAMESPACE=true`，上传与摄入 **namespace 必填**。  
文档主键为 `tenant_id::namespace::doc_name::doc_version`：上传与摄入的 **`doc_name`、`doc_version` 均必填**（无版本概念时显式传 `v1`）；省略 Form/JSON 字段会 **422**，不会自动补默认值。  
上传与摄入分离：先 `upload`，再 `jobs/ingest`，两边身份字段必须一致。

---

## 4.1 查询知识分类列表接口

### 接口功能

拉取左侧分类（含文档数、启用、优先级）。

### URL

`http://[ip]:8083/rag/namespaces`

### 鉴权方式

`Authorization: Bearer <service-api-key>`

### 支持格式

JSON

### HTTP 请求方式

GET

### 请求头

| 请求头 | 必填 | 说明 |
|--------|------|------|
| Authorization | true | `Bearer <service-api-key>` |

### 请求参数（Query）

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| exclude_nl2sql | false | boolean | 建议 `true`，隐藏 NL2SQL 资产库 |

### 返回字段

| 返回字段 | 字段类型 | 说明 |
|----------|----------|------|
| namespaces | array | 含 namespace、document_count、namespace_kb_enabled、namespace_kb_priority |

### 调用方使用说明

进知识库页先调本接口画 **左侧分类**。建议 `exclude_nl2sql=true`，不要用 `/rag/kb-tree`。无文档的分类不出现。默认选中「所有文献」，再拉右侧文档表（4.3）。

| 返回字段 | 侧栏怎么加载 |
|----------|--------------|
| `namespaces[].namespace` | 分类稳定键（`法规` / `规程` 等）。请求过滤、上传、摄入都传这个键，不要传展示名 |
| `namespaces[].document_count` | 分类旁文档数。「所有文献」的 count = 各业务分类之和 |
| `namespaces[].namespace_kb_enabled` | 启用开关回显（关闭=不参与召回，列表仍在） |
| `namespaces[].namespace_kb_priority` | 优先级数字回显（越小越优先） |

展示名可用前端映射（政策法规台账←`法规` 等）；未映射的键直接显示键名。点某分类：右侧 `GET /rag/documents/overview?namespace=`；点「所有文献」不传 `namespace`。

### 接口示例

```http
GET /rag/namespaces?exclude_nl2sql=true
Authorization: Bearer <service-api-key>
```

---

## 4.2 更新知识分类启用与优先级接口（地降所项目暂无该功能）

### 接口功能

当前地降所项目无该功能，跳过。
配置某 namespace 是否参与召回及召回优先级（数值越小越优先）。

### URL

`http://[ip]:8083/rag/namespaces/{namespace}/kb-config`

### 鉴权方式

`Authorization: Bearer <service-api-key>`

### 支持格式

JSON

### HTTP 请求方式

PATCH

### 请求头

| 请求头 | 必填 | 说明 |
|--------|------|------|
| Authorization | true | `Bearer <service-api-key>` |
| Content-Type | true | `application/json` |

### 请求参数

Path：

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| namespace | true | string | 分类键（如 `法规`） |

Body：

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| namespace_kb_enabled | false | boolean | 是否启用召回；默认 true |
| namespace_kb_priority | false | int | 优先级，≥1，越小越优先 |

### 返回字段

| 返回字段 | 字段类型 | 说明 |
|----------|----------|------|
| ok | boolean | 是否成功 |

### 调用方使用说明

当前地降所项目无该功能，**不调用、不渲染**。若后续要做分类启用/优先级：成功后用请求体回写该分类行的开关与数字即可，不必整页重拉；失败保持原值。

### 接口示例

```json
{
  "namespace_kb_enabled": true,
  "namespace_kb_priority": 1
}
```

---

## 4.3 分页查询知识文档概况接口

### 接口功能

右侧文档表主数据源：分页、按分类过滤、按文档名包含搜索、流水线状态芯片计数与点击过滤。  
默认排除 NL2SQL 内置三库：`nl2sql_schema`、`nl2sql_biz_knowledge`、`nl2sql_qa_examples`（与侧栏 `exclude_nl2sql=true` 一致）。完整数据（含三库）用 `GET /rag/documents/overview_all`，知识管理台前端不要调。

### URL

`http://[ip]:8083/rag/documents/overview`

### 鉴权方式

`Authorization: Bearer <service-api-key>`

### 支持格式

JSON

### HTTP 请求方式

GET

### 请求头

| 请求头 | 必填 | 说明 |
|--------|------|------|
| Authorization | true | `Bearer <service-api-key>` |

### 请求参数（Query）

| 参数 | 必填 | 类型 | 说明                                                                                                        |
|------|------|------|-----------------------------------------------------------------------------------------------------------|
| limit | false | int | 页大小，如 20                                                                                                  |
| offset | false | int | 偏移                                                                                                        |
| namespace | false | string | 分类；「所有文献」不传                                                                                               |
| doc_name_contains | false | string | 文档名包含（顶栏搜索，非语义检索）                                                                                         |
| pipeline_status | false | string | 流水线标签过滤：`all`（或不传）/ `uploaded` / `ingesting` / `ingested` / `failed`。只过滤列表与分页总数，**不**改变 `pipeline_counts` |

### 返回字段

| 返回字段 | 字段类型 | 说明 |
|----------|----------|------|
| documents | array | 文档行（doc_name、status、chunk_count、namespace、last_job_* 等） |
| total_documents | int | 当前列表过滤下的总数（含 `pipeline_status`），用于分页 |
| pipeline_counts | object | 流水线芯片数量：`all` / `uploaded` / `ingesting` / `ingested` / `failed`（互斥；仅真正进行中的任务算 ingesting，`status=SUCCESS` 且残留 `last_job_status=RUNNING` 算已摄入） |
| pipeline_status | string \| null | 回显当前列表过滤；`all`/省略时为 null |

### 调用方使用说明

本接口是右侧文档表的 **唯一主数据源**，也是顶栏「流水线状态」芯片的数据源。顶栏搜索是文档名包含模糊检索。列表、分页总数、`pipeline_counts`、`by_namespace` 都不含上述 NL2SQL 三库。

点芯片：同一接口加 `pipeline_status=`，用返回的 `pipeline_counts` 画五个数字（不要自己扫分页列表去数）。`pipeline_counts` 只跟当前分类、`doc_name_contains` 走，点某个芯片时其它芯片数字保持不变。分页用 `total_documents`（点芯片后等于该态数量）。

| 返回字段 | 表上怎么加载 |
|----------|--------------|
| `total_documents` | 分页总数；配合请求的 `limit` / `offset` |
| `pipeline_counts.all` | 「全部文献」数量 |
| `pipeline_counts.uploaded` | 「已上传」数量 |
| `pipeline_counts.ingesting` | 「摄入中」数量 |
| `pipeline_counts.ingested` | 「已摄入」数量 |
| `pipeline_counts.failed` | 「摄入失败」数量 |
| `documents[].doc_name` | 文档名称列 |
| `documents[].metadata.file_size` | 文件大小（字节，前端格式化；无则 —） |
| `documents[].source_type` | 格式（pdf/docx…） |
| `documents[].chunk_count` | 切块数（未摄入为 0） |
| `documents[].namespace` | 分类列（在「所有文献」下显示） |
| `documents[].created_at` / `updated_at` | 创建 / 更新 |
| `documents[].error` | 仅失败行显示失败原因 |
| `documents[].status` + `last_job_status` | 文档状态，见下表 |
| `documents[].last_job_id` | 「查看进度」跳转 `/kb/ingest-jobs/{jobId}` |
| `documents[].metadata.object_key` / `source_uri` | 点「摄入」时作为 `content`；没有则禁用摄入、提示重新上传 |

**状态判定（不要用原型「已录入 / 已向量化」）：** `last_job_status=PENDING`，或 `RUNNING` 且 `status` 尚非 SUCCESS/FAILED → 摄入中；`status=UPLOADED` → 已上传；`SUCCESS` → 已摄入（含历史残留 `last_job_status=RUNNING`）；`FAILED` → 摄入失败。行按钮：已上传「摄入」、摄入中「查看进度」、失败「重新摄入」、非进行中可删、已上传/已摄入可迁移。

### 接口示例

```http
GET /rag/documents/overview?limit=20&offset=0&namespace=法规&doc_name_contains=规范
Authorization: Bearer <service-api-key>

GET /rag/documents/overview?limit=20&offset=0&namespace=法规&pipeline_status=uploaded
Authorization: Bearer <service-api-key>
```

运维看含内置库的全量(当前地降所无需，内置排查用)：

```http
GET /rag/documents/overview_all?limit=20&offset=0
Authorization: Bearer <service-api-key>
```

---

## 4.4 知识文档上传接口

### 接口功能

仅上传到对象存储并登记文档状态为「已上传」，**不触发摄入**。

### URL

`http://[ip]:8083/rag/documents/upload`

### 鉴权方式

`Authorization: Bearer <service-api-key>`

### 支持格式

multipart/form-data

### HTTP 请求方式

POST

### 请求头

| 请求头 | 必填 | 说明 |
|--------|------|------|
| Authorization | true | `Bearer <service-api-key>` |
| Content-Type | true | `multipart/form-data` |

### 请求参数

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| file | true | file | 知识文档文件 |
| namespace | true | string | 知识分类（地降必填） |
| doc_name | true | string | 文档逻辑名（主键之一）；须与后续 `jobs/ingest` 的 `doc_name` 一致 |
| doc_version | true | string | 文档版本（主键之一）；无版本概念时传 `v1`。不传该字段会 **422**，不会默认补 `v1` |


### 返回字段

| 返回字段 | 字段类型 | 说明 |
|----------|----------|------|
| ok | boolean | 是否成功 |
| document | object | 含 status=UPLOADED、object_key / source_uri 等 |

### 调用方使用说明

「+ 上传」只落对象存储，**不触发摄入、不跳转进度页**。成功后关掉对话框，刷新右侧列表（再调 4.3）。记住 `document` 里的对象键，供稍后点「摄入」使用。

multipart 必填：`file`、`namespace`、`doc_name`、`doc_version`。缺任一字段（含未出现在 Form 里）由 FastAPI 返回 **422**，不会进入上传逻辑。不要用 JSON body。`doc_version` 不要省略，没有版本就传 `v1`。

| 返回字段 | 页面怎么用 |
|----------|------------|
| `ok=false` / 业务错误 | 留在对话框提示，不要当已上传 |
| `document.status=UPLOADED` | 列表应出现「已上传」，可点「摄入」 |
| `document.object_key` / `source_uri` | 摄入请求的 `content`（`minio://…`），禁止预签名 URL |
| 同名进行中再传 | 一般 409：提示等待或查看进度 |

分类：对话框必选；默认=当前左侧分类；在「所有文献」则必选一项。`dataset_id` 用环境默认，界面不展示。

### 接口示例

```http
POST /rag/documents/upload
Authorization: Bearer <service-api-key>
Content-Type: multipart/form-data

file=<文件>
namespace=法规
doc_name=堤防工程设计规范
doc_version=v1
```

---

## 4.5 知识摄入异步任务接口

### 接口功能

对已上传对象发起异步摄入（解析、切块、索引）。失败后「重新摄入」仍调本接口（不要用 `jobs/retry`）。

### URL

`http://[ip]:8083/rag/jobs/ingest`

### 鉴权方式

`Authorization: Bearer <service-api-key>`

### 支持格式

JSON

### HTTP 请求方式

POST

### 请求头

| 请求头 | 必填 | 说明 |
|--------|------|------|
| Authorization | true | `Bearer <service-api-key>` |
| Content-Type | true | `application/json` |

### 请求参数

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| documents | true | array | 待摄入文档列表 |
| documents[].namespace | true | string | 分类 |
| documents[].doc_name | true | string | 文档名（须与上传时一致） |
| documents[].doc_version | true | string | 文档版本（须与上传时一致；无版本概念时传 `v1`） |
| documents[].source_type | true | string | pdf/docx/doc/text/txt/markdown/html/xlsx 等 |
| documents[].content | true | string | 上传返回的 object_key（如 `minio://bucket/key`）；禁止预签名 URL |

### 返回字段

| 返回字段 | 字段类型 | 说明 |
|----------|----------|------|
| ok | boolean | 是否受理 |
| job_id | string | 任务 ID；前端跳转进度页 |

### 调用方使用说明

行内「摄入 / 重新摄入」都调本接口（失败重来也走这里，**不要**调 `jobs/retry`）。`content` = 列表行上的 `object_key` / `source_uri`。`doc_name` / `doc_version` / `namespace` 必须与上传登记一致，否则 overview 会裂成两条文档。受理后 **立即跳转进度页**（`/kb/ingest-jobs/{job_id}`），本页不要转圈。

| 返回字段 | 页面怎么用 |
|----------|------------|
| `ok=false` | 留在文档表提示，不跳转 |
| `job_id` | 进度页路由参数；之后轮询 4.6 |

无对象键：禁用按钮，提示重新上传。摄入中的行禁用再摄入，只提供「查看进度」。

### 接口示例

```json
{
  "documents": [{
    "namespace": "法规",
    "doc_name": "堤防工程设计规范",
    "doc_version": "v1",
    "source_type": "pdf",
    "content": "minio://<bucket>/<key>",
    "replace_if_exists": true
  }]
}
```

---

## 4.6 摄入任务查询接口

### 接口功能

进度页轮询任务状态（建议 1～2s）。

### URL

`http://[ip]:8083/rag/jobs/{job_id}`

### 鉴权方式

`Authorization: Bearer <service-api-key>`

### 支持格式

JSON

### HTTP 请求方式

GET

### 请求头

| 请求头 | 必填 | 说明 |
|--------|------|------|
| Authorization | true | `Bearer <service-api-key>` |

### 请求参数（Path）

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| job_id | true | string | 摄入任务 ID |

### 返回字段

| 返回字段 | 字段类型 | 说明 |
|----------|----------|------|
| status | string | PENDING / RUNNING / SUCCESS / FAILED 等 |
| step | string | 当前步骤 |
| chunks_total | int | 切块总数 |
| error_message | string | 失败原因 |

### 调用方使用说明

进度页主数据。`PENDING` / `RUNNING` 时建议 1～2s 轮询本接口；进入终态后停止轮询，显示「返回知识库」。失败后回文档表再点「重新摄入」（走 4.5 接口）。

| 返回字段 | 进度页怎么加载 |
|----------|----------------|
| `status` | 总状态，用下方中文对照，不要直接展示英文 |
| `step` | 当前步骤码，用下方中文对照做进度文案；未列入的值原样显示，不要留空 |
| `chunks_total` | 切块总数 |
| `error_message` | 仅失败时展示原因 |

`status` 中文对照：

| `status` | 界面文案 |
|----------|----------|
| `PENDING` | 等待中 |
| `RUNNING` | 进行中 |
| `SUCCESS` | 成功 |
| `FAILED` | 失败 |
| `PARTIAL` | 部分成功 |

`step` 中文对照（按常见出现顺序；单次任务不会走全，如非 PDF 无 MinerU、非图片无视觉描述）：

| `step` | 界面文案 |
|--------|----------|
| `created` | 已创建 |
| `validate_input` | 校验入参 |
| `content_fetch` | 拉取原文 |
| `mineru_route` | PDF 解析准备 |
| `mineru_parse` | PDF 解析 |
| `vision_caption` | 图片描述 |
| `parse` | 解析正文 |
| `clean` | 清洗文本 |
| `chunk` | 切块 |
| `enrich` | 补全元数据 |
| `index` | 写入索引 |
| `quality_check` | 质量检查 |
| `finalize_alias_version` | 版本收尾 |
| `finalize` | 完成 |
| `requeued_after_restart` | 服务重启后重试 |
| `recovered_stuck` | 卡住任务已恢复 |
| `error` | 处理失败 |

文档级明细可选再调 4.7。

### 接口示例

```http
GET /rag/jobs/<job_id>
Authorization: Bearer <service-api-key>
```

---

## 4.7 摄入任务文档明细接口

### 接口功能

进度页可选：查看本任务下各文档处理情况。

### URL

`http://[ip]:8083/rag/jobs/{job_id}/documents`

### 鉴权方式

`Authorization: Bearer <service-api-key>`

### 支持格式

JSON

### HTTP 请求方式

GET

### 请求头

| 请求头 | 必填 | 说明 |
|--------|------|------|
| Authorization | true | `Bearer <service-api-key>` |

### 请求参数（Path）

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| job_id | true | string | 摄入任务 ID |

### 返回字段

| 返回字段 | 字段类型 | 说明 |
|----------|----------|------|
| documents | array | 本任务文档清单及状态 |

### 调用方使用说明

进度页 **可选** 区块：本任务下各文档一行。没有明细也可只靠 4.6 的总状态。按每条的状态/错误展示成功或失败，不要用本接口替代任务级 `status`。

### 接口示例

```http
GET /rag/jobs/<job_id>/documents
Authorization: Bearer <service-api-key>
```

---

## 4.8 知识文档迁移分类接口

### 接口功能

将已上传/已摄入文档迁移到另一 namespace(修改文档所属分类)。

### URL

`http://[ip]:8083/rag/documents/namespace/move`

### 鉴权方式

`Authorization: Bearer <service-api-key>`

### 支持格式

JSON

### HTTP 请求方式

POST

### 请求头

| 请求头 | 必填 | 说明 |
|--------|------|------|
| Authorization | true | `Bearer <service-api-key>` |
| Content-Type | true | `application/json` |

### 请求参数

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| doc_name | true | string | 文档名（与登记一致） |
| to_namespace | true | string | 目标分类（地降所勿传空） |
| from_namespace | false | string | 原分类；省略表示默认分区 |

### 返回字段

| 返回字段 | 字段类型 | 说明 |
|----------|----------|------|
| ok | boolean | 是否成功 |
| chunks_updated | int | 更新的向量 chunk 数 |

### 调用方使用说明

行内「迁移」：目标分类必选且非空（地降所勿迁到空分区）。成功后刷新文档表和左侧分类文档数（再调 4.1、4.3）。`chunks_updated` 给联调/提示即可，界面不必单独画一列。

### 接口示例

```json
{
  "doc_name": "堤防工程设计规范",
  "from_namespace": "法规",
  "to_namespace": "规程"
}
```

---

## 4.9 知识文档删除接口

### 接口功能

删除指定文档登记及其索引数据（非进行中状态可删）。

### URL

`http://[ip]:8083/rag/documents/delete`

### 鉴权方式

`Authorization: Bearer <service-api-key>`

### 支持格式

JSON

### HTTP 请求方式

POST

### 请求头

| 请求头 | 必填 | 说明 |
|--------|------|------|
| Authorization | true | `Bearer <service-api-key>` |
| Content-Type | true | `application/json` |

### 请求参数

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| doc_name | true | string | 文档名 |
| namespace | false | string | 分类；地降所建议必传，避免跨类误删 |

### 返回字段

| 返回字段 | 字段类型 | 说明 |
|----------|----------|------|
| ok | boolean | 是否成功 |
| deleted | int | 删除的 chunk 条数 |

### 调用方使用说明

行内「删除」：仅非进行中状态可删（摄入中禁用）。地降所建议 **必传** `namespace`，避免跨类同名误删。成功后从当前页列表去掉该行，并刷新左侧 `document_count`。`deleted` 为清理的索引条数，界面不必单独展示。

### 接口示例

```json
{
  "doc_name": "堤防工程设计规范",
  "namespace": "法规"
}
```

---

## 4.10 按分类清空知识库接口（地降所项目暂无该功能）

### 接口功能

危险操作：清空某 namespace 下全部文档（需二次确认）。

### URL

`http://[ip]:8083/rag/namespaces/{namespace}/purge`

### 鉴权方式

`Authorization: Bearer <service-api-key>`

### 支持格式

JSON

### HTTP 请求方式

POST

### 请求头

| 请求头 | 必填 | 说明 |
|--------|------|------|
| Authorization | true | `Bearer <service-api-key>` |
| Content-Type | true | `application/json` |

### 请求参数

Path：

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| namespace | true | string | 分类键 |

Body：

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| confirm | true | boolean | 必须为 `true` |

### 返回字段

| 返回字段 | 字段类型 | 说明 |
|----------|----------|------|
| ok | boolean | 是否成功 |

### 调用方使用说明

危险操作：必须二次确认，且 Body `confirm` 必须为 `true`。成功后该分类从侧栏消失（无文档不再展示），右侧若正看该类则切回「所有文献」并重拉 4.1、4.3。不要用本接口当单文档删除（单条走 4.9）。

### 接口示例

```json
{ "confirm": true }
```

---

## 附录 A：功能 × 接口速查

### A.1 智能问答

| 功能 | 方法 | 路径 |
|------|------|------|
| 流式问答 | POST | `/chatbot/chat/stream` |
| 停止 | POST | `/chatbot/chat/stop` |
| 会话列表 | GET | `/chatbot/sessions` |
| 会话消息 | GET | `/chatbot/sessions/messages` |
| 改标题 | PATCH | `/chatbot/sessions/title` |
| 清空消息 | DELETE | `/chatbot/sessions/messages` |

### A.2 智能报告生成

| 功能 | 方法 | 路径 |
|------|------|------|
| 生成报告 | POST | `/analysis-agent/run-stream` |
| 停止 | POST | `/analysis-agent/stream/stop` |

### A.3 数据查询

| 功能 | 方法 | 路径 |
|------|------|------|
| 七库名单 | GET | `/data-query-agent/libraries` |
| 感知树 | GET | `/data-query-agent/tree` |
| 浏览列表（进页默认 / 点树） | GET | `/data-query-agent/browse` |
| 解析 SQL | POST | `/data-query-agent/run-stream`（一次性 JSON） |
| 选库后继续 | POST | `/data-query-agent/resume-stream`（一次性 JSON） |
| 浏览表详参 | GET | `/data-query-agent/hud` |

### A.4 知识库

| 功能 | 方法 | 路径 |
|------|------|------|
| 分类列表 | GET | `/rag/namespaces` |
| 启用/优先级 | PATCH | `/rag/namespaces/{namespace}/kb-config` |
| 文档列表/搜索/流水线状态 | GET | `/rag/documents/overview`（不含 NL2SQL 三库） |
| 上传 | POST | `/rag/documents/upload` |
| 摄入 | POST | `/rag/jobs/ingest` |
| 任务进度 | GET | `/rag/jobs/{job_id}` |
| 任务文档明细 | GET | `/rag/jobs/{job_id}/documents` |
| 迁移 | POST | `/rag/documents/namespace/move` |
| 删除 | POST | `/rag/documents/delete` |
| 清空分类 | POST | `/rag/namespaces/{namespace}/purge` |

---

## 附录 B：明确不做 / 勿调用（前端主路径）

| 模块 | 勿调用 |
|------|--------|
| 问答 | 渲染 `meta.nl2sql_sql`；勿将 `original_preview_url` 当独立业务接口对接（由 BFF 打开预览路径即可） |
| 报告 | `/analysis/*`、`/nl2sql/query`、`/data-query-agent/run-stream`、`/analysis-agent/resume-stream`；展示取数 SQL；接 GIS |
| 数据查询 | `/nl2sql/query` 当主入口、`/analysis-agent/*`、客服内 `data_query`、解析后再 `GET /hud` |
| 知识库 | `/rag/kb-tree`、废弃 `/rag/ingest/*`、`POST /rag/query`（顶栏非语义搜）、`POST /rag/jobs/{id}/retry`、`GET /rag/documents/overview_all`（含 NL2SQL 内置库，仅运维） |

---

*完*
