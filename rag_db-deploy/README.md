# EasySearch 数据库部署与项目对接指南（Docker）

本文提供 RAG 数据库（EasySearch，兼容 ES API）的容器化部署方案，适用于“模型服务容器化 + 应用容器化 + 数据库容器化”的统一交付模式。

## 1. 目录说明

- `docker-compose.easysearch.yml`：EasySearch 单节点 Docker 编排文件。
- `.env.example`：部署变量模板（容器侧）。
- `easysearch/config/easysearch.yml`：数据库配置**参考示例**（默认不挂载进容器；运行时使用镜像内 `easysearch.yml`。**注意**：官方镜像静态层里往往没有预先打包的 `instance.crt` 等，TLS 材料由容器入口脚本首次生成，或需执行 `bin/initialize.sh`，见下文「TLS / instance.crt」。）
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

- **Q：执行 `docker run --rm --entrypoint ls … /app/easysearch/config/` 看不到 `instance.crt` / `instance.key` / `ca.crt`，说明镜像坏了吗？**  
  A：**不一定。**`--entrypoint ls` 会**完全替换**镜像默认的 `ENTRYPOINT`/`CMD`，不会执行官方在「正常启动」前做的初始化逻辑。很多情况下证书是在**容器按默认入口启动时**才写入 `config/` 的，因此用 `ls` 扫静态文件系统看不到这些文件是常见现象。不要用这种方式判断「镜像里有没有证书」。若要确认，应使用默认入口启动一次临时容器（不要改 `entrypoint`），再在运行中的容器里执行 `ls /app/easysearch/config/`（或 `docker inspect` 查看镜像的 `Entrypoint`/`Cmd`）。

- **Q：启动仍报 `Unable to read …/instance.crt`（Security 插件 / `security.ssl.transport.cert_file`）？**  
  A：说明在 **Java 进程启动时** `config/` 下仍没有可读的这些文件。常见原因：  
  1. 曾把**宿主机空目录或只有 yml 的目录**挂载到 `/app/easysearch/config`，盖住了入口本应生成的文件；  
  2. 权限不足（官方文档：容器内 `easysearch` 用户 uid **602**，持久化目录需 `chown 602:602`）；  
  3. 所用镜像/标签与官方行为不一致。  
  **处理**：先检查 compose 是否挂载了整个 `./easysearch/config` 且其中缺少证书；若有，去掉该挂载或按 [INFINI Docker 文档](https://docs.infinilabs.com/easysearch/main/docs/deployment/install-guide/docker/) 先把镜像内 `config` 拷到宿主机并补齐初始化。  
  **关于 `initialize.sh`**：与 [快速开始](https://docs.infinilabs.com/easysearch/main/docs/quick-start/) 一致，证书与初始状态可由 `/app/easysearch/bin/initialize.sh`（常见带 `-s`）生成；**必须对「运行时会挂载的同一个 `config` 目录」执行**，否则证书写在临时容器层里，下一次 `compose up` 仍然缺文件。当前 `docker-compose.easysearch.yml` 若**未**挂载 `config` 卷，一般应依赖镜像**默认入口**在启动前写入；若入口未执行或失败，可改为官方推荐的三卷模式（`data` + `config` + `logs`），先对宿主机/命名卷中的 `config` 执行文档中的拷贝与初始化，再启动服务。脚本路径若不同，可先 `docker run --rm infinilabs/easysearch:2.1.1 ls /app/easysearch/bin` 确认。
