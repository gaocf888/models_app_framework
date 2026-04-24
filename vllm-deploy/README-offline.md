## vLLM 离线 / 内网部署指引（国产算力场景）
本说明用于 **无法访问公网**、但厂商已提供 **预装 vLLM / PyTorch / 驱动栈的基础镜像** 的场景。目标是：
- 基于厂商基础镜像，先在内网「烤出」一个 **已装好 Python 依赖的基础镜像标签**；
- 然后在 `vllm-deploy` 中，将该标签配置为 `BASE_IMAGE`，并设置 `VLLM_REQUIREMENTS_PROFILE=extras`，其余部署步骤与主 `README.md` 完全一致。
> 下文示例变量：  
> - 厂商基础镜像：`vendor/vllm-stack:base`  
> - 烤好后的新镜像：`vendor/vllm-stack:vllm-deploy-2025.01`  
> 请按实际镜像名替换。

> 步骤1到步骤4，针对厂商提供的镜像中没有安装必要的python依赖(pyyaml requests psutil),通过离线方式安装并构建新的镜像
> 若通过验证，厂商提供的镜像中包含了上述必要依赖，忽略步骤1到步骤4，直接执行步骤5即可

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
3. 其余环境变量按主 `README.md` 正常设置（`MODEL_PRESET`、`MODEL_PATH`、设备可见变量、`VLLM_PLATFORM` 等）。  
   - 英伟达使用 `CUDA_VISIBLE_DEVICES`；国产请按卡型设置 `ASCEND_RT_VISIBLE_DEVICES` / `MLU_VISIBLE_DEVICES` / `MX_VISIBLE_DEVICES` / `XPU_VISIBLE_DEVICES`。  
   - 其中建议将 `MODEL_PATH` 固定为宿主机离线路径 `/aidata/models/llm`。
4. 选择平台 overlay：  
   - 英伟达：`VLLM_PLATFORM=nvidia`（或脚本参数 `--platform nvidia`）  
   - 寒武纪：`VLLM_PLATFORM=cambricon`（或脚本参数 `--platform cambricon`）  
   - 沐曦：`VLLM_PLATFORM=mthreads`（或脚本参数 `--platform mthreads`）  
   - 昇腾：`VLLM_PLATFORM=ascend`（或脚本参数 `--platform ascend`）  
   - 其他国产平台建议继续新增 `docker/docker-compose.<platform>.yml`，不要改坏现有平台文件。
5. 参数优先级与来源请参考主文档 `README.md` 的「参数来源与覆盖逻辑（模型启动 / 部署）」章节；离线场景沿用同一套规则。

### 步骤 6：按主 README 的 Compose 流程构建与启动
完成以上准备后，后续完全沿用主 `README.md` 中的「构建与启动」部分：
```bash
cd vllm-deploy
chmod +x deploy.sh
./deploy.sh --platform cambricon
```
或在 `docker/` 下手动：
```bash
cd vllm-deploy/docker
docker compose --env-file ../.env -f docker-compose.yml -f docker-compose.cambricon.yml up -d --build
```
此时：
- `docker/docker-compose.yml` 的 `build.args.BASE_IMAGE` 会使用你刚提交的 `vendor/vllm-stack:vllm-deploy-2025.01`；
- Dockerfile 会根据 `VLLM_REQUIREMENTS_PROFILE=extras`，仅使用 `requirements-extras.txt` 做一次离线环境内 `pip` 补充安装；
- 运行期服务的端口、挂载、健康检查等与在线场景完全一致。

> 说明：当前仓库默认 `docker/Dockerfile` 已切换为 `yum/dnf` 包管理版本，原 `apt-get` 版本备份为 `docker/Dockerfile_bak`。若你的离线基础镜像是 Debian/Ubuntu 体系，请改用 `Dockerfile_bak` 或按现场镜像调整包安装命令。
