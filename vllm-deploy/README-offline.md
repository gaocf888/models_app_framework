## vLLM 离线 / 内网部署指引（国产算力场景）
本说明用于 **无法访问公网**、但厂商已提供 **预装 vLLM / PyTorch / 驱动栈的基础镜像** 的场景。目标是：
- 基于厂商基础镜像，先在内网「烤出」一个 **已装好 Python 依赖的基础镜像标签**；
- 然后在 `vllm-deploy` 中，将该标签配置为 `BASE_IMAGE`，并设置 `VLLM_REQUIREMENTS_PROFILE=extras`，其余部署步骤与主 `README.md` 完全一致。
> 下文示例变量：  
> - 厂商基础镜像：`vendor/vllm-stack:base`  
> - 烤好后的新镜像：`vendor/vllm-stack:vllm-deploy-2025.01`  
> 请按实际镜像名替换。

### 步骤 1：准备离线依赖包
1. 在一台 **有公网** 的开发机上，根据 `vllm-deploy/requirements-extras.txt` 下载 wheel 包：
   ```bash
   cd vllm-deploy
   mkdir -p offline-wheels
   pip download -r requirements-extras.txt -d offline-wheels
   ```
2. 将 `offline-wheels/` 目录以 U 盘、内网文件服务器等方式拷贝到 **目标国产服务器** 上，例如放到 `/opt/vllm-offline/offline-wheels`。
> 说明：`requirements-extras.txt` **不包含 vLLM / torch**，避免覆盖厂商已适配的推理栈。

### 步骤 2：基于厂商镜像启动临时容器
在目标服务器上：
```bash
docker run --name vllm-offline-build -it \
  vendor/vllm-stack:base \
  bash
```
- 若需挂载离线包目录，可加：`-v /opt/vllm-offline/offline-wheels:/offline-wheels:ro`。
- 进入容器后，确认已有 `python3` 与 `pip3`（若无，请按厂商文档安装或与其镜像维护方协作）。

### 步骤 3：在临时容器内安装 Python 依赖
在容器 **内部** 执行（假设离线包挂载到 `/offline-wheels`）：
```bash
cd /offline-wheels
pip3 install --no-index --find-links=. pyyaml requests psutil
```
如需固定安装用户目录或特定虚拟环境，请按运维规范调整命令。
安装成功后，保持容器处于退出状态（或在另一终端操作）。

### 步骤 4：提交新基础镜像
在宿主机上，将临时容器提交为新的基础镜像标签：
```bash
docker commit vllm-offline-build vendor/vllm-stack:vllm-deploy-2025.01
```
（可按需再 `docker rm vllm-offline-build` 清理临时容器。）

### 步骤 5：在 vllm-deploy 中使用该基础镜像
1. 回到应用项目的 `vllm-deploy/` 目录，复制并编辑 `.env`：
   ```bash
   cd /path/to/models_app_framework/vllm-deploy
   cp .env.example .env
   vi .env
   ```
2. 在 `.env` 中按如下方式配置（关键是两点）：
   ```bash
   # 使用已烤好的离线基础镜像
   BASE_IMAGE=vendor/vllm-stack:vllm-deploy-2025.01
   # 只安装 extras 依赖，避免用 PyPI 覆盖厂商栈
   VLLM_REQUIREMENTS_PROFILE=extras
   ```
3. 其余环境变量（`MODEL_PRESET`、`MODEL_PATH`、`CUDA_VISIBLE_DEVICES` 等）按主 `README.md` 正常设置。  
   其中建议将 `MODEL_PATH` 固定为宿主机离线路径 `/opt/models/llm`。

### 步骤 6：按主 README 的 Compose 流程构建与启动
完成以上准备后，后续完全沿用主 `README.md` 中的「构建与启动」部分：
```bash
cd vllm-deploy
chmod +x deploy.sh
./deploy.sh
```
或在 `docker/` 下手动：
```bash
cd vllm-deploy/docker
docker compose --env-file ../.env up -d --build
```
此时：
- `docker/docker-compose.yml` 的 `build.args.BASE_IMAGE` 会使用你刚提交的 `vendor/vllm-stack:vllm-deploy-2025.01`；
- Dockerfile 会根据 `VLLM_REQUIREMENTS_PROFILE=extras`，仅使用 `requirements-extras.txt` 做一次离线环境内 `pip` 补充安装；
- 运行期服务的端口、挂载、健康检查等与在线场景完全一致。
