## 框架实现说明

### 小模型训练
目前已实现 yolo 所有模型的训练工程化（`app/train/yolo`）。

### 视频解码、任务队列、图像识别的多通道线程安全业务流
目前已实现，入口：`app/api/small_model.py`。

### RAG 数据库（EasySearch）快速启动

本项目默认使用 ES/EasySearch 作为 RAG 检索库，提供了一键部署包：

1. 进入部署目录并复制环境变量模板：
   ```bash
   cd rag_db-deploy
   cp .env.example .env
   ```
2. 启动 EasySearch 数据库容器：
   ```bash
   docker compose --env-file ".env" -f "docker-compose.easysearch.yml" up -d
   ```
3. 将 `project-env/rag-es.env.example` 中的内容合并到应用服务的环境变量中（或直接加载为 `.env`），即可让应用通过 `RAG_ES_HOSTS` 等变量连接到该实例。

更多说明与参数含义详见：`rag_db-deploy/README.md`。