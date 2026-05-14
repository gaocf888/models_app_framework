# 本地挂载与备份说明（全项目）

> 目的：说明**业务数据落在宿主机哪些目录**（bind mount），便于你做**拷贝、存储快照或离线归档**。  
> 说明：**挂载 = 持久化**；要「备份」请在停写或快照一致性有保障的前提下，对上述目录再做**第二份副本**（如 `rsync`、`tar`、磁盘快照、对象存储等）。本仓库不内置定时备份任务。

---

## 1. 一句话对照

| 你想备份的内容 | 优先看哪里 |
|----------------|------------|
| RAG 向量 / 全文索引、会话冷层（EasySearch） | `rag_db-deploy` → `EASYSEARCH_DATA` |
| 会话热数据、队列（Redis） | `app/app-deploy` → `REDIS_DATA_HOST_PATH` |
| MinIO 对象、部分图片缓存 | `MINIO_DATA_HOST_PATH` |
| 应用日志文件 | `app/app-deploy/logs`（见下表） |
| 会话对象存储（local 备份增强） | `SESSION_STORAGE_HOST_PATH` |
| 检修异步任务与元数据 | `INSPECT_EXTRACT_JOBS_HOST_PATH` |
| FAISS 索引（仅 `RAG_VECTOR_STORE_TYPE=faiss`） | `RAG_FAISS_DATA_HOST_PATH` |
| TLS 与部分配置（EasySearch） | `rag_db-deploy/easysearch/config/` 下已挂载的证书与 yml |

变量默认值以各目录 **`*.env.example`** 为准；生产请复制为 `.env` 后按需修改路径。

---

## 2. EasySearch（`rag_db-deploy/`）

| 宿主机 / 说明 | 容器内路径 | 内容 |
|----------------|------------|------|
| **`EASYSEARCH_DATA`**（示例：`/aidata/data/es_data`） | `/app/easysearch/data` | 索引与集群数据（RAG、会话冷层等凡写入 ES 的都在此） |
| `rag_db-deploy/easysearch/config/` 中单文件挂载（如 `instance.crt`、`ca.crt` 等） | `/app/easysearch/config/...` | TLS 与自定义配置片段；**纳入变更与灾备** |

编排文件：`rag_db-deploy/docker-compose.easysearch.yml`。  
环境模板：`rag_db-deploy/.env.example`（`EASYSEARCH_DATA`、`EASYSEARCH_DATA_VOLUME` 等）。

---

## 3. 应用栈（`app/app-deploy/`）

以下由 **`docker-compose.yml`** 或 **`docker-mx/docker-compose-mx.yml`** 解析；路径变量写在 **`app/app-deploy/.env.example`** 末尾「Compose 专用」段。

| 环境变量（宿主机目录） | 容器内路径 | 内容 |
|------------------------|------------|------|
| **`REDIS_DATA_HOST_PATH`**（默认 `/aidata/data/redis_data`） | Redis `/data` | AOF、会话与队列等 |
| **`MINIO_DATA_HOST_PATH`**（默认 `/aidata/data/minio_data`） | MinIO `/data` | 对象存储数据 |
| **`./logs`**（相对 `app/app-deploy/docker-compose.yml`） | 应用 `/workspace/logs` | 应用文件日志 |
| **`../logs`**（相对 `docker-mx/docker-compose-mx.yml`） | 应用 `/workspace/logs` | 与上一行**同一宿主机目录**：`app/app-deploy/logs` |
| **`SESSION_STORAGE_HOST_PATH`**（默认 `/aidata/data/session_storage`） | `/workspace/data/conversation_archive` | 会话归档 **local** 对象备份（与 `CONV_ARCHIVE_OBJECT_LOCAL_DIR` 一致） |
| **`INSPECT_EXTRACT_JOBS_HOST_PATH`**（默认 `/aidata/data/inspect_extract_jobs`） | `/workspace/data/inspection_extract_jobs` | 检修异步任务状态与产物 |
| **`RAG_FAISS_DATA_HOST_PATH`**（默认 `/aidata/data/faiss_data`） | `/workspace/data/faiss` | FAISS 索引目录（与 `RAG_FAISS_INDEX_DIR` 一致） |

应用内路径与变量对应关系见 **`app/app-deploy/.env.example`** 正文（如 `CONV_ARCHIVE_OBJECT_LOCAL_DIR`、`INSPECT_EXTRACT_ASYNC_JOBS_DIR`、`RAG_FAISS_INDEX_DIR`）。

---

## 4. 其他可选组件（按需）

| 组件 | 数据位置 | 说明 |
|------|----------|------|
| **Neo4j（GraphRAG）** | `graphrag_db-deploy` 中 `${NEO4J_DATA_VOLUME}:/data`，默认多为**命名卷** | 若也要「固定宿主机目录」，可改为 bind mount 并单独文档化路径 |
| **MinerU / Paddle 版面** | `MINERU_IO_HOST_PATH`、`PADDLE_LAYOUT_IO_HOST_PATH` 等 | 解析输入输出，与 `mineru-deploy`、`paddleocr-layout-deploy` 的 compose 一致即可 |
| **vLLM** | `MODEL_PATH`、`../logs` 等 | 权重与日志；见 `vllm-deploy/docker/docker-compose*.yml` |

---

## 5. 备份操作建议（极简）

1. **低频全量**：对上述目录分别 `tar` 或整盘快照；EasySearch 大数据量时优先**存储层快照**或官方 **snapshot**（若集群支持）。  
2. **高频增量**：对宿主机目录做 `rsync` 到备份机或另一块盘。  
3. **一致性**：EasySearch / Redis 写入多，尽量在**低峰**或**短暂停服**后备份；至少避免在高压写入时只拷部分文件。  
4. **还原**：先停对应容器 → 将备份还原到**原挂载路径**与权限 → 再启动；密钥与 `rag_db-deploy/easysearch/config` 需与当时一致。

---

## 6. 相关文件索引

| 路径 | 作用 |
|------|------|
| `rag_db-deploy/docker-compose.easysearch.yml` | EasySearch 挂载 |
| `rag_db-deploy/.env.example` | EasySearch 数据目录等 |
| `app/app-deploy/docker-compose.yml` | 标准应用栈挂载 |
| `app/app-deploy/docker-mx/docker-compose-mx.yml` | 沐曦等变体挂载 |
| `app/app-deploy/.env.example` | 应用与 Compose 路径变量 |
| `app/app-deploy/README.md` | 卷与持久化总表（会随 compose 更新） |
