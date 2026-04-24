# EasySearch 数据库部署与项目对接指南（Docker）

本文提供 RAG 数据库（EasySearch，兼容 ES API）的容器化部署方案，适用于“模型服务容器化 + 应用容器化 + 数据库容器化”的统一交付模式。

## 1. 目录说明

- `docker-compose.easysearch.yml`：EasySearch 单节点 Docker 编排文件。
- `.env.example`：部署变量模板（容器侧）。
- `easysearch/config/easysearch.yml`：数据库配置**参考示例**（默认不挂载进容器；编排使用镜像内建配置即可稳定 `down`/`up`）。
- `easysearch/init/01-init-rag-indexes.sh`：初始化脚本（可选），用于创建 RAG 索引和别名。
- `project-env/rag-es.env.example`：项目侧环境变量模板（应用服务读取）。

## 2. 前置条件

- 已安装 Docker 与 Docker Compose。
- 机器可用内存建议 >= 8 GB（仅开发演示可更低）。
- 端口 `9200` 未被占用（如占用请改 `.env` 中 `EASYSEARCH_PORT`）。

## 3. 部署步骤

### 3.1 准备配置

在 `rag_db-deploy/` 下复制模板：

```powershell
Copy-Item ".env.example" ".env"
```

按需修改 `.env` 关键项：

- `EASYSEARCH_IMAGE`：EasySearch 镜像地址（由你们制品库提供）。
- `EASYSEARCH_USERNAME` / `EASYSEARCH_PASSWORD`：数据库认证账号密码。
- `EASYSEARCH_PORT`：对外端口（默认 `9200`）。

### 3.2 启动数据库

- 启动数据库
> 针对离线的环境(无法访问互联网)，可以提前在有外网的服务器中easysearch镜像，然后导入到离线服务器中即可
```powershell
docker-compose --env-file ".env" -f "docker-compose.easysearch.yml" up -d
```

- 启动easysearch的容器后，进入容器，初始化设置固定密码
```powershell
# 1. 进入容器
docker exec -it rag-easysearch bash

# 2. 执行curl请求，设置密码
curl -X PUT \
  --cert /app/easysearch/config/admin.crt \
  --key /app/easysearch/config/admin.key \
  -H 'Content-Type: application/json' \
  -k \
  -d '{
    "password": "ChangeMe_123!", 
    "external_roles": ["admin"]
  }' \
  https://localhost:9200/_security/user/admin
```


### 3.3 验证可用性

```powershell
curl -k -u admin:ChangeMe_123! "https://127.0.0.1:9200/_cluster/health?pretty"
```

若返回 `status`（yellow/green）且接口可访问，表示启动成功。

> 若返回 401，可在容器内执行 `reset_admin_password.sh` 重新生成 admin 密码后再测试。

### 3.4 执行初始化（可选）

项目已支持启动自动迁移（`RAG_ES_AUTO_MIGRATE_ON_START=true`），可不执行此步骤。  
若希望数据库先预建索引/别名，可执行：

```powershell
docker exec rag-easysearch sh /opt/easysearch/init/01-init-rag-indexes.sh
```

## 4. 项目侧配置（应用如何连接 EasySearch）

将 `project-env/rag-es.env.example` 内容合并到应用 `.env`（或容器环境变量）：

- `RAG_VECTOR_STORE_TYPE=es`（默认，推荐）
- `RAG_ES_HOSTS=https://rag-easysearch:9200`（容器间访问）
- `RAG_ES_USERNAME` / `RAG_ES_PASSWORD`
- `RAG_ES_INDEX_*`、`RAG_ES_DOCS_INDEX_*`、`RAG_ES_JOBS_INDEX_*`

> 注意：如果应用和 EasySearch 在同一 Docker 网络中，`RAG_ES_HOSTS` 建议填容器名；本机调试可填 `https://127.0.0.1:9200`。

## 5. Docker 化部署建议（与 vLLM / 应用并行部署）

- 建议将“应用容器 + vLLM 容器 + EasySearch 容器”加入同一网络（如 `ai-stack`）。
- 应用只通过环境变量访问 EasySearch，不在代码中写死地址。
- 生产环境建议启用：
  - 持久化卷（已在 compose 中提供）；
  - 认证与 TLS（建议开启并统一使用 HTTPS）；
  - 监控告警（磁盘、JVM、索引写入失败率）。

## 6. 常见问题

- **Q：必须是 EasySearch 吗？**  
  A：项目按 ES API 接口实现，EasySearch 与 Elasticsearch 兼容，可按实际交付镜像替换。

- **Q：索引需要手工建吗？**  
  A：默认不需要，项目启动可自动迁移。初始化脚本用于“先建库后启服务”的运维场景。

- **Q：如何切回 FAISS？**  
  A：将项目环境变量改为 `RAG_VECTOR_STORE_TYPE=faiss` 并配置 `RAG_FAISS_INDEX_DIR`，无需改数据库部署。

- **Q: 启动时报错：1.启动时报错：[1]: max virtual memory areas vm.max_map_count [65530] is too low, increase to at least [262144] ?**
  A: 这是因为默认vm.max_map_count = 65530，es需要262144，下面是修复方法：
    1.编辑/etc/sysctl.conf（或 /etc/sysctl.d/99-easysearch.conf）追加一行：vm.max_map_count=262144
    2.让配置生效：sudo sysctl -p 
    3.确认：sysctl vm.max_map_count

- **Q: 若启动时报错（配置文件的问题easysearch.yml中配置项与默认项目不匹配）?**
  ```text
  目前已经采用不挂载配置文件了，始终使用默认配置（所以就不需要如下配置了）
  # 第一步：停止服务，清除卷数据，重启
  cd rag_db-deploy
  docker compose -f docker-compose.easysearch.yml --env-file .env down -v
  docker volume rm rag_easysearch_data
  
  # 第二步：
  docker-compose配置文件中，把下面的一行配置注释掉（使用easysearch默认配置）
  - ./easysearch/config/easysearch.yml:/app/easysearch/config/easysearch.yml:ro
  
  # 第三步：重新启动
  docker compose -f docker-compose.easysearch.yml --env-file .env up -d
  
  # 第三步：
  按照第一种方法正常启动后，使用下面命令复制容器中默认配置文件
  docker cp rag-easysearch:/app/easysearch/config/easysearch.yml ./easysearch/config/easysearch.exported.yml
  然后easysearch.exported.yml重命名为 easysearch.yml  （原来的该文件删除），并赋予文件权限：chmod 777 easysearch.yml
  然后修改文件名和里面的个性配置（比如集群名称），然后再把上述docker-compose中的注释掉的放开注释
  然后重启docker-compose（使用上述 第一步方式）
  ```
