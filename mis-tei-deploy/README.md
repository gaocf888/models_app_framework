# MIS-TEI 独立部署（昇腾 Embedding / Reranker）

面向 Atlas 300I Duo（310P），使用华为 Ascend Hub 镜像：

`swr.cn-south-1.myhuaweicloud.com/ascendhub/mis-tei:26.0.0-310p-ubuntu22.04-py3.11`

与 `models-app` 解耦：本目录只起 **HTTP 推理服务**；应用通过
`EMBEDDING_BACKEND=mis_tei` / `RAG_RERANKER_BACKEND=mis_tei` 调用。

## 1. 目录与模型

```bash
cd mis-tei-deploy
cp .env.example .env
mkdir -p /aidata/models/embeddings /aidata/models/reranker
```

离线权重布局（目录名须与模型 ID 末段一致）：

```text
/aidata/models/embeddings/bge-large-zh-v1.5/     # ← MIS_TEI_EMBED_MODEL_ID=BAAI/bge-large-zh-v1.5
/aidata/models/reranker/bge-reranker-large/      # ← MIS_TEI_RERANK_MODEL_ID=BAAI/bge-reranker-large
```

## 2. 启动

```bash
docker compose --env-file .env up -d
docker compose --env-file .env ps
docker compose --env-file .env logs -f mis-tei-embed
```

默认端口（宿主机）：

| 服务 | 容器名 | 端口 | 接口 |
|------|--------|------|------|
| Embedding | `mis-tei-embed` | 8091 | `POST /embed` |
| Rerank | `mis-tei-rerank` | 8092 | `POST /rerank` |

Docker 网络名默认 **`mis-tei-stack`**（`models-app` 须以 external 接入）。

## 3. 自检

```bash
curl -sS http://127.0.0.1:8091/health
curl -sS http://127.0.0.1:8091/embed \
  -H 'Content-Type: application/json' \
  -d '{"inputs":"堤防工程设计规范"}'

curl -sS http://127.0.0.1:8092/health
curl -sS http://127.0.0.1:8092/rerank \
  -H 'Content-Type: application/json' \
  -d '{"query":"堤防规范","texts":["堤防工程设计规范 GB50286","无关文本"]}'
```

## 4. 与 models-app 对接

在 `app/app-deploy/.env`：

```env
EMBEDDING_BACKEND=mis_tei
RAG_RERANKER_BACKEND=mis_tei
MIS_TEI_EMBED_BASE_URL=http://mis-tei-embed:8080
MIS_TEI_RERANK_BASE_URL=http://mis-tei-rerank:8080
MIS_TEI_NETWORK_NAME=mis-tei-stack
```

回退进程内 SentenceTransformer / CrossEncoder：

```env
EMBEDDING_BACKEND=local
RAG_RERANKER_BACKEND=local
EMBEDDING_DEVICE=cpu
RAG_RERANKER_DEVICE=cpu
```

## 5. NPU 切分建议

| 设备 | 用途 |
|------|------|
| 0–3 | vLLM |
| 4 | mis-tei-embed（`.env` `MIS_TEI_EMBED_ASCEND_DEVICES`） |
| 5 | mis-tei-rerank |
| 6 | MinerU |

`models-app` 在 `mis_tei` 模式下可不占用 NPU 做嵌入/重排。

## 6. 说明

- 镜像详情见 [昇腾镜像仓库 MIS-TEI](https://www.hiascend.com/developer/ascendhub/detail/07a016975cc341f3a5ae131f2b52399d)。
- `ENABLE_BOOST=True` 适用于 BERT 类向量加速；若启动失败可改为 `False` 排查。
- 本 compose **不构建镜像**，仅拉取/加载已有 `mis-tei` 镜像。
