# Neo4j / GraphRAG 容器部署方案（graphrag_db-deploy）

本目录提供 **Neo4j 图数据库** 的 Docker 部署方案，用于支撑项目中的 **GraphRAG 能力**（参见 `memory-bank/01-architecture.md` 与 `framework-guide/RAG整体实现技术说明.md`）。

- **Neo4j 本身的部署**：使用本目录的 `docker-compose.neo4j.yml` + `.env`。  
- **应用侧接入（GraphRAG）**：应用通过 `NEO4J_URI` / `NEO4J_USERNAME` / `NEO4J_PASSWORD` / `NEO4J_DATABASE` 等环境变量接入，示例见 `project-env/graph-neo4j.env.example`，最终由 `app/app-deploy/.env` 注入应用进程，并由 `app/core/config.py` 的 `GraphRAGConfig` 读取。

---

## 1. 目录结构与角色

| 文件 / 目录 | 说明 |
|------------|------|
| `.env.example` | Neo4j 容器部署的环境模板（复制为 `.env` 使用） |
| `docker-compose.neo4j.yml` | Neo4j 单机部署的 Compose 文件（含数据卷、网络、健康检查） |
| `project-env/graph-neo4j.env.example` | **应用侧** GraphRAG 接入 Neo4j 的环境模板（供 `app/app-deploy/.env` 参考） |
| `conf/` | 可选：自定义 `neo4j.conf` 等配置文件目录（当前为空，默认配置即可运行） |
| `logs/` | 挂载 Neo4j 日志（`docker-compose` 会将容器 `/logs` 映射到此目录） |
| `plugins/` | 可选：放置 APOC 等官方插件 JAR（需按 Neo4j 官方文档启用） |

---

## 2. 前置条件

- 已安装 **Docker** 与 **Docker Compose V2**。  
- 宿主机资源满足 Neo4j 要求（内存、磁盘等，具体参考官方文档）。  
- 如果应用栈（`app/app-deploy`）希望与 Neo4j 在同一 Docker 网络中通信，请统一使用同一个网络名（本目录默认 `graph-stack`）。

---

## 3. 部署 Neo4j（数据库本身）

### 3.1 准备环境文件

```bash
cd graphrag_db-deploy
cp .env.example .env
```

在 `.env` 中按需调整：

- `NEO4J_IMAGE`：默认为社区版 `neo4j:5.24.0-community`，如需企业版或特定 tag 可改。  
- `NEO4J_USERNAME` / `NEO4J_PASSWORD`：首次启动时生效，**务必在生产环境改为强密码**。  
- `NEO4J_BOLT_PORT` / `NEO4J_HTTP_PORT`：Bolt 端口（应用使用）与 HTTP 管理端口（浏览器使用）。  
- `NEO4J_NETWORK`：Docker 网络名，默认 `graph-stack`。若应用栈使用其它名称，请同步。  
- `NEO4J_DATA_VOLUME`：数据卷名称，持久化 `/data`。

### 3.2 启动 Neo4j

```bash
cd graphrag_db-deploy
docker compose -f docker-compose.neo4j.yml --env-file .env up -d
```

检查：

```bash
docker ps | grep graph-neo4j
docker logs -f graph-neo4j
```

等待健康检查 `healthy`：

```bash
docker inspect -f '{{ .State.Health.Status }}' graph-neo4j
```

### 3.3 访问 Neo4j

- 浏览器（HTTP UI）：访问 `http://127.0.0.1:NEO4J_HTTP_PORT`（默认 `7474`），使用 `.env` 中配置的 `NEO4J_USERNAME` 和 `NEO4J_PASSWORD` 登录。  
- Bolt（应用 / cypher-shell）：应用栈内部通常使用 `bolt://graph-neo4j:7687`。

数据目录与日志：

- `/data` → Docker 卷 `${NEO4J_DATA_VOLUME}`（默认 `graph_neo4j_data`），用于持久化图数据。  
- `/logs` → 本目录 `logs/`。  
- `/conf`、`/plugins` → 本目录 `conf/` 与 `plugins/`（如不需要自定义配置可保持为空）。

---

## 4. 应用侧 GraphRAG 接入（NEO4J_* 环境）

应用（例如 `app/app-deploy` 中的 FastAPI 服务）通过 `app/core/config.py` 读取：

- `GRAPH_RAG_ENABLED`  
- `NEO4J_URI`  
- `NEO4J_USERNAME` / `NEO4J_PASSWORD`  
- `NEO4J_DATABASE`  
- `GRAPH_SCHEMA_CONFIG_PATH`（可选）

### 4.1 推荐的配置流程

1. 复制项目级模板：

```bash
cd graphrag_db-deploy/project-env
cp graph-neo4j.env.example ../../app/app-deploy/graph-neo4j.env
```

2. 打开 `app/app-deploy/graph-neo4j.env`，确认以下内容与本目录 `.env` 一致：

```env
GRAPH_RAG_ENABLED=true
NEO4J_URI=bolt://graph-neo4j:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=ChangeMe_123!
NEO4J_DATABASE=neo4j
```

3. 根据你当前的部署策略，将上述键合并到 **`app/app-deploy/.env`** 中（因为应用实际上读取的是该文件注入的环境变量），例如：

```env
# app/app-deploy/.env（节选）
GRAPH_RAG_ENABLED=true
NEO4J_URI=bolt://graph-neo4j:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=ChangeMe_123!
NEO4J_DATABASE=neo4j
```

4. 确保应用栈加入与 Neo4j 相同的 Docker 网络（例如在 `app/app-deploy/docker-compose.yml` 中有：

```yaml
networks:
  graph-external:
    name: graph-stack
    external: true
```

并将对应服务加入该网络）。

---

## 5. 与 app/core/config.py 的对应关系

在 `app/core/config.py` 中，GraphRAG 相关配置大致如下（简化）：

- `GRAPH_RAG_ENABLED`：控制是否启用 GraphRAG。  
- `NEO4J_URI`：如 `bolt://graph-neo4j:7687`。  
- `NEO4J_USERNAME` / `NEO4J_PASSWORD` / `NEO4J_DATABASE`：与本目录 `.env` / `project-env` 中一致。  
- `GRAPH_SCHEMA_CONFIG_PATH`：若你在容器内放置单独的 `graph_schema.yaml`，可通过该环境变量告知应用加载该 Schema。

只要上述环境变量在应用进程中正确设置，GraphRAG 模块即可通过 Neo4j 进行图结构存储与查询。

---

## 6. 运维建议与排错

### 6.1 运维要点（简版）

| 检查项 | 通过标准 |
|--------|----------|
| 容器状态 | `docker ps` 中 `graph-neo4j` 为 `Up` 且健康检查为 `healthy` |
| 端口连通性 | 本机可访问 `http://127.0.0.1:NEO4J_HTTP_PORT`；应用容器内可 `nc -vz graph-neo4j 7687` |
| 认证 | 使用 `.env` 中账号密码可登录浏览器 UI 或使用 `cypher-shell` 登录 |
| 数据卷 | `docker volume inspect NEO4J_DATA_VOLUME` 存在；数据重启后不丢失 |

### 6.2 常见问题

- **应用报 `GRAPH_RAG_ENABLED=true 但无法连接 Neo4j`**  
  - 检查应用容器是否加入 `NEO4J_NETWORK` 对应网络；  
  - 检查 `NEO4J_URI` 主机名是否为 `graph-neo4j` 而非 `localhost`；  
  - 使用 `docker exec <app-container> ping graph-neo4j` 测试。

- **Neo4j 启动失败 / 反复重启**  
  - `docker logs graph-neo4j` 查看是否为配置错误或磁盘权限问题；  
  - 确认数据卷目录有足够磁盘空间。

- **账号密码错误**  
  - `NEO4J_AUTH` 仅在首次启动时生效；此后如果修改密码需按官方 `neo4j-admin` / UI 流程重置，而不是单纯修改 `.env`。

---

## 7. 对比 rag_db-deploy 的角色

- `rag_db-deploy/`：面向 **向量 + 全文检索** 底座（EasySearch），为 RAG 文档库服务。  
- `graphrag_db-deploy/`：面向 **图数据库 / GraphRAG** 底座（Neo4j），为图结构知识与关系建模服务。

二者可独立部署，也可在同一项目中同时启用（应用栈分别通过 `RAG_ES_*` 与 `NEO4J_*` 环境变量接入）。

