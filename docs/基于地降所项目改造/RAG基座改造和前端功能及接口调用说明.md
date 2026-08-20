# 地降所项目 — RAG 基座改造和前端功能及接口调用说明

> **版本**：2026-08-20（修订：不新增 `documents/ingest`；摄入/重灌复用 `POST /rag/jobs/ingest`，`content` 为稳定对象键，禁止预签名 URL）  
> **范围**：知识管理台（扁平 `namespace` 分类）+ RAG 基座增量（上传/原文对象存储、上传与摄入拆分、空 ns 校验、文档名模糊查询、文件大小）。不改客服 / 分析 / NL2SQL 默认召回契约。  
> **现网基线**：  
> - `docs/RAG基于namespace的状态和优先级的改造实现方案.md`  
> - `framework-guide/RAG整体实现技术说明.md`  
> **明确不作本期**：同目录 `RAG多级知识目录改造落地方案.md`（多级目录树）已回滚，本文不再建设 `/rag/kb-tree` 或 `kb_dir_*`。

---

## 0. 一句话结论

**左侧分类 = 现网扁平 `namespace`（由已有文档自动出现，不做空类、不做目录树）。上传只落对象存储并登记文档；点「摄入 / 重新摄入」用现网 `POST /rag/jobs/ingest`（`content` = 稳定对象键），再跳转独立进度页。不新增 `documents/ingest`。启用/优先级仍按整个 namespace 配置。文档主键、召回 `term namespace`、kb-config / purge / move 保持现网语义。**

---

## 1. 需求与现网对照

| # | 需求 | 现网 | 本期 |
|---|------|------|------|
| 1 | 不做空类；摄入 `namespace` 禁止为空 | 无空 ns 校验；空/省略 → `__default__` | 管理面上传 / 摄入 / move 目标 ns 为空 → 400 |
| 2 | multipart 上传 | 无；`content` 仅为正文 / 本地路径 / 可选 http URL | 新增 `POST /rag/documents/upload` |
| 3 | 列表展示文件大小 | `DocumentMetaItem` 无此字段 | 上传时写入 `metadata.file_size`（字节） |
| 4 | 状态跟现网，不跟原型双徽章 | docs：`SUCCESS` / `FAILED`；任务：`PENDING`/`RUNNING`/`SUCCESS`/`FAILED`/`PARTIAL` | 文档增 `UPLOADED`；前端按 §4 映射 |
| 5 | 原文可重灌；失败后可再摄入 | `source_uri` 不下载；job 快照若是路径/预签名 URL 会失效 | 原文进对象存储；记下稳定 `object_key`；重灌再调 `jobs/ingest`，`content` 用该键 |
| 6 | 启用/优先级在左侧 namespace 操作 | `GET/PATCH /rag/namespaces*` 已有 | **前端接上即可**；未传 kb 字段的新文档须继承该 ns 已有配置 |
| 7 | 顶栏文档名模糊查询 | `overview`/`meta` 有 `doc_name`，实现为 **精确 term** | 管理台改为包含匹配（见 §3.4） |
| 8 | 上传与摄入拆开；点摄入后进进度页 | `jobs/ingest` 提交即跑，但须当场给 `content` | 上传不提交任务；点摄入再调 **现网** `POST /rag/jobs/ingest`（`content`=对象键）→ 用返回 `job_id` 进进度页。**不新增** `documents/ingest` |

**兼容**：锅炉等仍可能用空 ns 时，用配置开关（如 `RAG_REQUIRE_NAMESPACE`，地降所开启）。NL2SQL 三库（`nl2sql_schema` / `nl2sql_biz_knowledge` / `nl2sql_qa_examples`）本页侧栏排除，其写入不受「空 ns」影响（本身非空）。

---

## 2. 主流程

```text
知识管理页                              摄入进度页（新页）
  选 namespace
  → 上传文件（对象存储 + docs 登记 UPLOADED）
  → 列表出现「已上传、未摄入」
  → 点「摄入」  ─────────────────────►  轮询 GET /rag/jobs/{job_id}
  → 失败后再点「重新摄入」 ───────────►  同一进度页
```

- **上传**：不切块、不写向量。  
- **摄入 / 重新摄入**：前端用列表（或上传响应）里的 `object_key` 调现网 `POST /rag/jobs/ingest`；服务端按对象键 **内部 get** 原文（不走预签名 HTTP，也不依赖 `RAG_CONTENT_FETCH` 拉 MinIO）。  
- **进度**：只在进度页展示；知识管理页不转圈（最多刷新行状态）。  
- 管理台重灌以 docs 上的 `object_key` 再调 `jobs/ingest` 为准，不要用 `POST /rag/jobs/{job_id}/retry` 当主按钮（旧任务快照可能仍是路径/过期 URL）。

---

## 3. RAG 基座改造

### 3.1 文档状态

| `status` | 含义 | 向量 |
|----------|------|------|
| `UPLOADED` | 已上传，尚未成功摄入 | 无（`chunk_count=0`） |
| `SUCCESS` | 切块 + 索引完成 | 有 |
| `FAILED` | 摄入失败（原文一般仍在对象存储） | 无或旧数据（以实现为准） |

任务状态仍仅用于进度页。若 `last_job_status` 为 `PENDING`/`RUNNING`，列表展示「摄入中」，即使 docs 的 `status` 暂未改写。

上传成功即 `upsert` docs 记录，否则刷新后看不到待摄入文件，侧栏 `document_count` 也不含该类。

### 3.2 新增：只上传

**`POST /rag/documents/upload`**（`multipart/form-data`）

| 字段 | 必填 | 说明 |
|------|------|------|
| `file` | 是 | 原文件 |
| `namespace` | 是 | 非空；即左侧分类 |
| `dataset_id` | 建议 | 无则后端默认（管理台可隐藏） |
| `doc_name` | 否 | 默认文件名（去扩展名） |
| `description` | 否 | 人读说明 |

行为：

1. 校验 `namespace` 非空。  
2. 原文写入对象存储（复用/扩展 `RagAssetStorage`，与 figure 前缀分开）。  
3. `source_uri` = 稳定标识（如 `minio://bucket/key`），**禁止**预签名 URL。  
4. `metadata.file_size`、`metadata.object_key`、`metadata.original_filename`。  
5. docs：`status=UPLOADED`，`chunk_count=0`。  
6. **不** `submit_job`。

同主键再上传：禁止覆盖进行中任务（409）；否则覆盖对象。已 `SUCCESS` 时覆盖后将 `status` 置 `UPLOADED`（旧向量保留至再次摄入且 `replace_if_exists=true`）。

不可复用 `POST /chatbot/upload`（仅图片、短 TTL）。

### 3.3 摄入 / 重灌：复用现网 `POST /rag/jobs/ingest`（不新增接口）

**不新增 `POST /rag/documents/ingest`。** 管理台「摄入」「重新摄入」都走现网异步入口，把 `content` 设为上传时落下的 **稳定对象引用**。

前端从上传响应或列表行取数，例如：

```json
{
  "documents": [{
    "dataset_id": "<列表 dataset_id 或默认值>",
    "doc_name": "<doc_name>",
    "namespace": "<namespace>",
    "source_type": "pdf",
    "content": "minio://<bucket>/<object_key>",
    "source_uri": "minio://<bucket>/<object_key>",
    "replace_if_exists": true
  }]
}
```

`content` / `source_uri` 与 `metadata.object_key` 同源。无对象键则前端禁用摄入并提示重新上传。

**为何不用「上传返回的预签名 URL」当 `content`：** 现网 URL 拉取要开 `RAG_CONTENT_FETCH_ENABLED`，且默认拦截私网 IP（MinIO 常在内网）；预签名会过期，失败后再灌、过几天再灌都会挂。`source_uri` 现网 **不用于下载**。

**基座必改（否则复用 `jobs/ingest` 拉不到 MinIO 原文）：**

1. `content` 识别稳定对象键（如 `minio://bucket/key` 或约定前缀的 `object_key`），编排器 **MinIO SDK / 本地回退内部 get**，不绕 HTTP、不依赖 content-fetch 白名单。  
2. Job 快照只存该对象引用，不存二进制、不存预签名 URL。  
3. 未传 `namespace_kb_enabled` / `namespace_kb_priority` 时，**继承该 ns 已有配置**（与 `GET /rag/namespaces` 同源）。  
4. 开关开启时 `jobs/ingest` 禁止空 `namespace`。  
5. 删除文档时级联删除对象存储原文。

返回仍是现网的 `job_id`，前端跳转进度页。

### 3.4 现有接口小改

| 改动 | 说明 |
|------|------|
| 空 ns | 上传、`jobs/ingest`、`upsert`、`move` 的目标 ns |
| 文档名包含 | `GET /rag/documents/overview`、`GET /rag/documents/meta`：新增 `doc_name_contains`（wildcard / 子串）；保留 `doc_name` 精确，以免破坏其它调用方。管理台顶栏只用 `doc_name_contains` |
| `content` 对象键 | 见 §3.3：内部 get 原文 |
| kb 继承 | 见 §3.3 |

**不改**：文档主键 `{tenant}::{namespace}::{doc_name}::{version}`；召回 `term namespace`；`PATCH .../kb-config` 整 ns 生效。

### 3.5 进度页：不新增接口

`GET /rag/jobs/{job_id}` 已含 `status`、`step`、`error_code`、`error_message`、`metrics`（`documents_total` / `documents_success` / `documents_failed`、`chunks_total`、`step_durations_ms`、`doc_stats`）。可选 `GET /rag/jobs/{job_id}/documents`。

---

## 4. 前端功能及接口调用说明

两个页面。分类 = `namespace` 字符串；无建夹/改夹/多级树。本页排除 NL2SQL 三库。不要调用 `/rag/kb-tree`、废弃 `/rag/ingest/*`、`GET /rag/datasets`、`POST /rag/query`（顶栏不是语义检索）。**不要**新增或调用 `POST /rag/documents/ingest`。

### 4.1 页面 A — 知识管理

**布局**：顶栏搜索 +「+ 上传」｜左侧分类 ｜右侧文档表。

**顶栏**

| 功能 | 行为 | 接口 |
|------|------|------|
| 按名称搜索 | 当前分类下文档名**包含**匹配；「全部」不传 ns | `GET /rag/documents/overview?doc_name_contains=&namespace=&limit=&offset=` |
| + 上传 | 只上传，成功后留在本页刷列表，**不跳转** | `POST /rag/documents/upload` |

上传对话框：分类（默认=当前左侧；在「全部」则必选）、文件、文档名（默认文件名）、可选说明。`dataset_id` 用默认值，可不展示。

**左侧分类**

| 功能 | 行为 | 接口 |
|------|------|------|
| 分类列表 | 无空类：有 docs 登记（含仅上传）才出现 | `GET /rag/namespaces` |
| 所有文献 | 不传 ns；count = 业务分类 `document_count` 之和 | 点选后 overview 不带 `namespace` |
| 点某分类 | 右侧按该 ns 过滤 | `GET /rag/documents/overview?namespace=` |
| 启用 / 优先级 | 作用于该 ns 下全部文档/chunk | `PATCH /rag/namespaces/{namespace}/kb-config` |
| 清空本类（可选） | 二次确认 | `POST /rag/namespaces/{namespace}/purge` |

每项展示：`namespace`、`document_count`、启用开关、优先级（数值越小越优先）。关闭启用 = 不参与召回，列表仍在，与「未摄入/失败」分开。中文展示名若需要，仅前端映射，后台仍用 ns 键。

**右侧表**

| 列 | 字段 |
|----|------|
| 文档名称 | `doc_name` |
| 文件大小 | `metadata.file_size`（无则 —） |
| 格式 | `source_type` |
| 切块数 | `chunk_count`（未摄入为 0） |
| 文档状态 | 见下表 |
| 失败原因 | `error`（仅失败） |
| 分类 | `namespace`（在「全部」下显示） |
| 创建 / 更新 | `created_at` / `updated_at` |
| 操作 | 见下表 |

状态（不要用原型「已录入 / 已向量化」）：

| 判定 | 展示 |
|------|------|
| `last_job_status` 为 PENDING / RUNNING | 摄入中 |
| `status=UPLOADED` | 已上传 |
| `status=SUCCESS` | 已摄入 |
| `status=FAILED` | 摄入失败 |

行操作：

| 状态 | 按钮 | 接口 |
|------|------|------|
| 已上传 | **摄入** → 跳转进度页 | `POST /rag/jobs/ingest`（`content`=行内 `object_key`）→ 用返回 `job_id` 打开页面 B |
| 摄入中 | **查看进度** | 用 `last_job_id` 打开页面 B；禁用再摄入 |
| 摄入失败 | **重新摄入** → 跳转进度页 | 同上 `POST /rag/jobs/ingest`（仍用 docs 上的对象键，不要用过期 URL / `jobs/retry`） |
| 非进行中 | 删除 | `POST /rag/documents/delete` |
| 已上传 / 已摄入 | 迁移到其它 ns | `POST /rag/documents/namespace/move`（目标 ns 非空） |

分页：overview 的 `limit` / `offset` / `total_documents`。主列表只用 overview，不必再调 `GET /rag/documents/meta`。

### 4.2 页面 B — 摄入进度

从「摄入 / 重新摄入 / 查看进度」进入（如 `/kb/ingest-jobs/{job_id}`）。

| 功能 | 接口 |
|------|------|
| 进度主数据（PENDING/RUNNING 轮询） | `GET /rag/jobs/{job_id}` |
| 本任务文档清单（可选） | `GET /rag/jobs/{job_id}/documents` |

展示：`status`、当前 `step`、成功/失败文档数、`chunks_total`、`error_message`。  
终态：返回知识管理页。失败后在页面 A 对原文档再点「重新摄入」（再次 `jobs/ingest`）。本页不要调 `POST /rag/jobs/{id}/retry`。

### 4.3 功能 × 接口速查

| 功能 | 方法 | 路径 | 现状 |
|------|------|------|------|
| 侧栏分类 / 启用 / 优先级 / 文档数 | GET | `/rag/namespaces` | 已有 |
| 改启用与优先级 | PATCH | `/rag/namespaces/{namespace}/kb-config` | 已有 |
| 文档列表 / 分页 / 按分类 | GET | `/rag/documents/overview` | 已有 |
| 顶栏名称模糊 | GET | `/rag/documents/overview?doc_name_contains=` | **需加参数** |
| 上传（不摄入） | POST | `/rag/documents/upload` | **新增** |
| 摄入 / 失败后重灌 | POST | `/rag/jobs/ingest`（`content`=对象键） | **已有，须支持对象键内部 get** |
| 进度页 | GET | `/rag/jobs/{job_id}` | 已有 |
| 进度页文档明细 | GET | `/rag/jobs/{job_id}/documents` | 已有 |
| 迁移分类 | POST | `/rag/documents/namespace/move` | 已有 |
| 删除文档 | POST | `/rag/documents/delete` | 已有（须级联删对象） |
| 清空分类 | POST | `/rag/namespaces/{namespace}/purge` | 已有（可选入口） |

后台已有、本页可不做：`GET /rag/jobs`（任务中心）、`GET /rag/knowledge/trends`、`POST /rag/query`、`GET /rag/assets/presign`（figure）、NL2SQL 管理接口。

---

## 5. 实施顺序

1. 空 ns 校验、`doc_name_contains`、摄入继承 ns 的 kb-config（前端可先接侧栏、列表、搜索、启用/优先级）。  
2. 对象存储原文 + `POST /rag/documents/upload`（`UPLOADED` + `file_size` / `object_key`）。  
3. `jobs/ingest` 的 `content` 支持对象键内部 get；进度页对接 `GET /rag/jobs/{job_id}`。  
4. 按 §4 铺两个前端页面（摄入按钮调 `jobs/ingest`，不调已取消的 `documents/ingest`）。

---

## 6. 待实现时确认的两点

1. **空 ns**：地降所管理面一律 400，或 `RAG_REQUIRE_NAMESPACE` 仅地降所开启。  
2. **同名再上传**：按 §3.2（进行中 409，否则覆盖对象；已成功则改回 `UPLOADED` 待再摄入）。
