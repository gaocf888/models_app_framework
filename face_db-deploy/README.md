# Milvus 人脸向量库

为人脸识别 1:N 检索提供 **Milvus Standalone** 部署，与 RAG 使用的 FAISS / EasySearch **独立**，避免业务混库。

## 1. 启动 Milvus

```bash
cd face_db-deploy
cp .env.example .env
docker compose -f docker-compose.milvus.yml --env-file .env up -d
docker compose -f docker-compose.milvus.yml ps
```

健康检查：`curl http://127.0.0.1:9091/healthz`

## 2. 配置应用

在 `app/app-deploy/.env` 中：

```bash
FACE_VECTOR_BACKEND=milvus
MILVUS_URI=http://milvus-standalone:19530
MILVUS_FACE_COLLECTION=face_embeddings
FACE_EMBEDDING_DIM=512
FACE_MILVUS_NETWORK=face-milvus-stack
```

确保 `models-app` / `models-app-gpu` 已加入 `face-milvus-stack` 网络（见 `app/app-deploy/docker-compose.yml`）。

Python 依赖（应用镜像或宿主机）：

```bash
pip install -r requirements-人脸识别-Milvus.txt
```

## 3. 迁移已有 JSON 人脸库

```bash
# 在项目根目录，且已设置 FACE_VECTOR_BACKEND=milvus 与 MILVUS_URI
python scripts/migrate_face_galleries_to_milvus.py
python scripts/migrate_face_galleries_to_milvus.py --gallery-id default
python scripts/migrate_face_galleries_to_milvus.py --dry-run
```

元数据（person、图片路径）仍保留在 `data/face_galleries/`；Milvus 仅存向量与检索字段。

## 4. 回退到 local

```bash
FACE_VECTOR_BACKEND=local
```

无需停 Milvus；应用自动使用 JSON + numpy/faiss。JSON 中 embedding 仍保留，可随时再迁移。

## 5. Collection 结构

| 字段 | 类型 | 说明 |
|------|------|------|
| sample_id | VARCHAR PK | 样本 ID |
| gallery_id | VARCHAR | 人脸库 ID |
| person_id | VARCHAR | 人员 ID |
| person_name | VARCHAR | 展示名 |
| embedding | FLOAT_VECTOR(512) | InsightFace buffalo_l 向量 |

默认索引：`AUTOINDEX`，度量 `COSINE`（与 `match_threshold` 语义一致：越大越相似）。
