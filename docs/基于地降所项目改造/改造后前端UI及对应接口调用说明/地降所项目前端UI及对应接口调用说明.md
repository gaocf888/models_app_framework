# 地降所项目 — 前端 UI 及对应接口调用说明

> **版本**：2026-08-20  
> **结构**：与左侧导航四大板块对齐。前三块本期预留（只保留标题）；第四块 **知识库** 为当前实现。  
> **后台契约**（知识库）：`docs/基于地降所项目改造/RAG基座改造和前端功能及接口调用说明.md`  
> **本目录资源**：原型与改造后线框图均放在本文件夹内。

| 文件 | 说明 |
|------|------|
| `prototype-kb-original.png` | 原始知识库原型截图 |
| `kb-page-redesign.png` | 改造后：知识管理主页面 |
| `kb-upload-dialog.png` | 改造后：上传对话框（只上传、不摄入） |
| `kb-ingest-progress.png` | 改造后：摄入进度页 |

线框图中的中文可能有绘制误差，**以本文表格与接口为准**。

---

## 一、智能问答

---

## 二、自动报告生成

---

## 三、数据查询

---

## 四、知识库

本期落地。侧栏「知识库」为可进入页面；路由建议 `/kb`（管理）、`/kb/ingest-jobs/:jobId`（摄入进度）。

### 4.0 与原始原型的差异（必须按改造方案改）

原始原型（`prototype-kb-original.png`）保留壳层：左侧四模块、顶栏标题、分类导航 + 右侧表格。以下按基座方案调整：

| 原始原型 | 改造后（本期） |
|----------|----------------|
| 「+ 添加」一次完成 | 「+ **上传**」只落对象存储；再点行内「**摄入**」 |
| 「已录入 / 已向量化」两列徽章 | 单列 **文档状态**：已上传 / 摄入中 / 已摄入 / 摄入失败 |
| 搜索「文献、规范、Biot…」像语义搜 | 顶栏仅为 **文档名包含** 匹配 |
| 分类无启用/优先级 | 分类行可配置 **启用、优先级**（整 namespace） |
| 操作偏刷新/删除 | **摄入 / 重新摄入 / 查看进度 / 迁移 / 删除** |
| 无进度页 | 点摄入后 **跳转进度页**，本页不转圈 |
| 空分类也展示（count=0） | **无空类**：有 docs 登记才出现；新分类靠第一次上传带 namespace |

分类仍是扁平 `namespace`（如 `法规`），不是多级目录树。侧栏排除 `nl2sql_*` 三库。不要调用 `/rag/kb-tree`、废弃 `/rag/ingest/*`、`POST /rag/query`（顶栏不是语义检索）、不要新增 `POST /rag/documents/ingest`。

### 4.1 页面 A — 知识管理

![改造后知识管理主页面](kb-page-redesign.png)

布局：顶栏搜索 +「+ 上传」｜左侧分类 ｜右侧文档表。

#### 进入页面

`GET /rag/namespaces` → 画左侧分类（丢掉 NL2SQL 三库）。  
默认选中「所有文献」，再 `GET /rag/documents/overview?limit=20&offset=0`（不传 `namespace`）。

#### 顶栏

| UI | 行为 | 接口 |
|----|------|------|
| 搜索框 +「搜索」 | 当前分类下文档名**包含**；「全部」不传 ns | `GET /rag/documents/overview?doc_name_contains={q}&namespace={可选}&limit=&offset=` |
| + 上传 | 打开上传对话框，成功后留在本页刷新列表 | 见下方「上传对话框」 |

占位符改为「搜索文档名称…」，不要做成语义检索。

#### 左侧分类

数据：`GET /rag/namespaces` 的 `namespace`、`document_count`、`namespace_kb_enabled`、`namespace_kb_priority`。

展示名可用前端映射（后台仍用键）：

| 展示名 | namespace |
|--------|-----------|
| 政策法规台账 | `法规` |
| 学术前沿专著 | `专著` |
| InSAR术语词汇 | `术语` |
| 施工勘测规程 | `规程` |
| 历史灾险案例 | `险情` |

未在映射表中的 ns 直接显示键名。无文档的分类不出现。

| UI | 行为 | 接口 |
|----|------|------|
| 所有文献 | 不传 ns；count = 业务分类 `document_count` 之和 | `GET /rag/documents/overview?limit&offset` |
| 点某分类 | 右侧按该 ns 过滤 | `GET /rag/documents/overview?namespace={ns}` |
| 启用开关 / 优先级数字 | 作用该 ns 下全部文档与 chunk | `PATCH /rag/namespaces/{namespace}/kb-config` |
| 清空本类（可选，二次确认） | 危险操作 | `POST /rag/namespaces/{namespace}/purge` `{ "confirm": true }` |

关闭启用 = 不参与召回，列表仍在；与「未摄入 / 失败」分开显示。

`PATCH` 请求体：

```json
{ "namespace_kb_enabled": true, "namespace_kb_priority": 1 }
```

默认分区路径参数为 `__default__`；本期上传禁止空 ns，侧栏一般不会出现默认分区。

#### 上传对话框

![上传对话框](kb-upload-dialog.png)

| 字段 | 说明 |
|------|------|
| 知识分类 | 必填；默认=当前左侧分类；在「所有文献」则必选 |
| 文件 | 必填 |
| 文档名称 | 默认文件名去扩展名 |
| 说明 | 可选 |
| dataset_id | 用环境默认，**不展示** |

`POST /rag/documents/upload`（`multipart/form-data`）

- `file`、`namespace` 必填  
- 可选：`doc_name`、`description`、`dataset_id`、`doc_version`

成功：`document.status=UPLOADED`，记住 `object_key` / `source_uri` / `file_size`。列表刷新，**不跳转进度页**。

进行中任务再传同名 → 409，提示等待或查看进度。

#### 右侧表格

主列表只用 overview，不必再调 `GET /rag/documents/meta`。分页：`limit` / `offset` / `total_documents`。

| 列 | 字段 |
|----|------|
| 文档名称 | `doc_name` |
| 文件大小 | `metadata.file_size`（字节，前端格式化；无则 —） |
| 格式 | `source_type` |
| 切块数 | `chunk_count`（未摄入为 0） |
| 文档状态 | 见下表 |
| 失败原因 | `error`（仅失败显示） |
| 分类 | `namespace`（在「所有文献」下显示） |
| 创建 / 更新 | `created_at` / `updated_at` |
| 操作 | 见下表 |

**状态判定（不要用原型「已录入 / 已向量化」）：**

| 判定 | 展示 |
|------|------|
| `last_job_status` 为 `PENDING` / `RUNNING` | 摄入中 |
| `status=UPLOADED` | 已上传 |
| `status=SUCCESS` | 已摄入 |
| `status=FAILED` | 摄入失败 |

**行操作：**

| 状态 | 按钮 | 接口 |
|------|------|------|
| 已上传 | **摄入** → 跳转页面 B | `POST /rag/jobs/ingest` |
| 摄入中 | **查看进度** | 用 `last_job_id` 打开 `/kb/ingest-jobs/{jobId}`，禁用再摄入 |
| 摄入失败 | **重新摄入** → 跳转页面 B | 同上 `POST /rag/jobs/ingest`（用 docs 上的对象键，不要 `jobs/retry`） |
| 非进行中 | 删除 | `POST /rag/documents/delete` |
| 已上传 / 已摄入 | 迁移 | `POST /rag/documents/namespace/move`（目标 ns 非空） |

摄入请求示例（`content` = 行内 `metadata.object_key` 或 `source_uri`）：

```json
{
  "documents": [{
    "dataset_id": "<行内 dataset_id 或默认>",
    "doc_name": "<doc_name>",
    "namespace": "<namespace>",
    "source_type": "pdf",
    "content": "minio://<bucket>/<key>",
    "source_uri": "minio://<bucket>/<key>",
    "replace_if_exists": true
  }]
}
```

无 `object_key`：禁用摄入，提示重新上传。返回 `job_id` 后立即跳转页面 B。

### 4.2 页面 B — 摄入进度

![摄入进度页](kb-ingest-progress.png)

从「摄入 / 重新摄入 / 查看进度」进入。

| UI | 接口 |
|----|------|
| 进度主数据（PENDING/RUNNING 轮询，建议 1～2s） | `GET /rag/jobs/{job_id}` |
| 本任务文档清单（可选） | `GET /rag/jobs/{job_id}/documents` |

展示：`status`、当前 `step`、成功/失败文档数、`chunks_total`、`error_message`。  
终态：按钮「返回知识库」。失败后回页面 A 再点「重新摄入」。**不要**调用 `POST /rag/jobs/{id}/retry`。

### 4.3 功能 × 接口速查

| 功能 | 方法 | 路径 |
|------|------|------|
| 侧栏分类 / 启用 / 优先级 / 文档数 | GET | `/rag/namespaces` |
| 改启用与优先级 | PATCH | `/rag/namespaces/{namespace}/kb-config` |
| 文档列表 / 分页 / 按分类 | GET | `/rag/documents/overview` |
| 顶栏名称模糊 | GET | `/rag/documents/overview?doc_name_contains=` |
| 上传（不摄入） | POST | `/rag/documents/upload` |
| 摄入 / 失败后重灌 | POST | `/rag/jobs/ingest`（`content`=对象键） |
| 进度页 | GET | `/rag/jobs/{job_id}` |
| 进度页文档明细 | GET | `/rag/jobs/{job_id}/documents` |
| 迁移分类 | POST | `/rag/documents/namespace/move` |
| 删除文档 | POST | `/rag/documents/delete` |
| 清空分类 | POST | `/rag/namespaces/{namespace}/purge` |

本页可不做：`GET /rag/jobs`（任务中心）、`GET /rag/knowledge/trends`、`POST /rag/query`、`GET /rag/assets/presign`、NL2SQL 管理接口。

### 4.4 联调注意

- 地降所：`RAG_REQUIRE_NAMESPACE=true`，上传与摄入 namespace 必填。  
- `object_key` 为 `minio://...` 或 `local:...`，禁止把预签名 URL 当 `content`。  
- 侧栏 count 含「仅上传未摄入」的 docs 登记。
