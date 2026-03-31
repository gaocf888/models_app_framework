# 应用服务部署简明说明（面向项目开发同学）

> 本文是 `app/app-deploy/README.md` 的「精简版」，只保留 **日常开发 / 自己机器上部署和连通性配置** 所需的信息。  
> 生产运维、完整参数解释与排错仍以主文档 `README.md` 为准。

---

## 1. 你要搞清楚的三件事

1. **你只负责 FastAPI 应用**（`app/main.py` 对外的接口），大模型和底层库已经有固定部署方案：  
   - 大模型（LLM / VL）：`vllm-deploy/`  
   - 向量 + 全文库（RAG）：`rag_db-deploy/`（EasySearch）  
   - 图数据库（GraphRAG，可选）：`graphrag_db-deploy/`（Neo4j）
2. **应用所有配置都来自环境变量**（`.env → docker compose → 容器环境 → app/core/config.py`）。  
3. 你主要修改的文件只有两个：  
   - `app/app-deploy/.env`（从 `.env.example` 复制）  
   - 极少数情况下，针对新接口改 `app/core/config.py` 里的默认值。

---

## 2. 最小部署流程（开发环境）

### 2.1 准备依赖服务

在仓库根目录依次执行：

```bash
# 1) 启动 EasySearch（RAG 底层库）
cd rag_db-deploy
cp .env.example .env        # 只需第一次
docker compose -f docker-compose.easysearch.yml --env-file .env up -d

# 2) 启动 vLLM（大模型服务）
cd ../vllm-deploy/docker
docker compose up -d
```

（如需 GraphRAG，再单独看 `graphrag_db-deploy/README.md`，一般开发阶段可以先不用管。）

### 2.2 准备应用栈 `.env`

```bash
cd ../../app/app-deploy
cp .env.example .env
```

只要改几项（其他可以先不动）：

```env
LLM_DEFAULT_ENDPOINT=http://vllm-service:8000/v1   # 不要写 127.0.0.1
LLM_DEFAULT_MODEL=qwen2.5-vl-7b-instruct           # 和 vllm-deploy 中 served-model-name 保持一致

RAG_ES_HOSTS=https://rag-easysearch:9200
RAG_ES_USERNAME=admin
RAG_ES_PASSWORD=ChangeMe_123!                      # 和 rag_db-deploy 中 admin 密码一致

REDIS_URL=redis://redis:6379/0                     # 保持默认即可
```

若你在本机有 MySQL，并打算调试 NL2SQL，按实际情况改：

```env
DB_URL=mysql+aiomysql://root:your_mysql_password@host.docker.internal:3306/aishare
```

> 提醒：**容器内访问其它容器不要用 `127.0.0.1`，用容器名**（例如 `vllm-service`、`rag-easysearch`、`redis`）。

### 2.3 启动应用服务

```bash
cd app/app-deploy
docker compose up -d --build
```

访问：

```bash
curl -s "http://127.0.0.1:8080/health/"
```

看到 `{"status":"ok"}` 即 FastAPI 应用正常启动。

---

## 3. 常用开发命令

在 `app/app-deploy` 目录下：

```bash
# 查看日志
docker compose logs -f models-app

# 修改代码后重启应用容器
docker compose restart models-app

# 停掉应用和 Redis（不会影响 vLLM / EasySearch）
docker compose down
```

如使用小模型 GPU profile：

```bash
docker compose --profile small-model-gpu up -d --build
docker compose logs -f models-app-gpu
```

（profile 的详细说明见主文档 `README.md`，开发阶段如不用小模型可以忽略。）

---

## 4. 你可能会改到的配置

绝大多数情况下，只需要改 `.env`，下面是几类常见修改：

- **更换大模型**：  
  - 在 `vllm-deploy/config/models.yaml` 里增加/修改 preset；  
  - 把 `LLM_DEFAULT_MODEL` 改成新的 `served-model-name`。

- **接入别的 RAG 集群**：  
  - 把 `RAG_ES_HOSTS` 改成对应地址（可以是 `https://127.0.0.1:9200` 或远程服务）；  
  - 同步改 `RAG_ES_USERNAME/RAG_ES_PASSWORD`。

- **开启 GraphRAG**（如后续需要）：  
  - 在 `graphrag_db-deploy` 启动 Neo4j；  
  - 在 `.env` 中设置 `GRAPH_RAG_ENABLED=true`，并填上 `NEO4J_URI/NEO4J_USERNAME/NEO4J_PASSWORD/NEO4J_DATABASE`；  
  - 代码侧 `app/core/config.py` 已经支持这些变量，无需再改。

---

## 5. 快速排错思路（开发视角）

遇到问题可以按这个顺序排：

1. **服务是否都在跑**  
   - `docker ps`：有没有 `rag-easysearch`、`vllm-service`、`models-app`。  
2. **网络是否通**  
   - `docker exec -it models-app sh` 进容器：  
     - `ping vllm-service`、`ping rag-easysearch`。  
3. **变量是否写对**  
   - `docker compose exec models-app env | grep LLM_DEFAULT_ENDPOINT` 看应用里实际拿到的值。  
4. **接口层是否正常**  
   - 本机直接 `curl http://127.0.0.1:8080/llm/infer` 做一次最简单的推理测试（见对应 API 文档）。

更完整的运维和排错，请看同目录的主文档 `README.md` 中的「故障排查」和「运维检查清单」。\n
