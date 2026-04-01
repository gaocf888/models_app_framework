
## 部署说明

### 下载模型权重

```text  方式一
# 安装 git-lfs
sudo yum install git-lfs

# 初始化git-lfs
git lfs install

# 进入models路径
cd vllm-deploy/models

# 克隆模型仓库（使用ModelScope 或者huggingface都行，ModelScope 国内快一些）--局域网服务器可以下载后上传
git clone https://www.modelscope.cn/Qwen/Qwen2.5-VL-7B-Instruct.git
cd Qwen2.5-VL-7B-Instruct
git lfs pull
```

```text   方式二 modelscope(国内更快)
pip install modelscope

python -c "from modelscope import snapshot_download; snapshot_download('qwen/Qwen2.5-VL-7B-Instruct', cache_dir='./models/Qwen2.5-VL-7B-Instruct')"
```

```text   方式三 huggingface
pip install huggingface-hub

python -c "from huggingface_hub import snapshot_download; snapshot_download('Qwen/Qwen2.5-VL-7B-Instruct', local_dir='./models/Qwen2.5-VL-7B-Instruct', endpoint='https://hf-mirror.

```


### 本地部署

```bash

# 1. 克隆或上传项目
git clone <repository> vllm-deploy
cd vllm-deploy

# 2. 配置模型路径
vi config/vllm.yaml
# 设置 model.path，例如: /data/models/Qwen2.5-7B-Instruct

# 3. 一键部署
chmod +x deploy.sh
./deploy.sh

# 4. 查看状态
python3 scripts/start.py status

# 5. 停止服务
python3 scripts/start.py stop

# 6. 重启服务
python3 scripts/start.py restart
```

### docker 部署

```bash
# 1. 配置环境变量
cp .env.example .env
vi .env

# 2. 启动服务
cd docker
docker-compose up -d

# 3. 查看日志
docker-compose logs -f

# 4. 停止服务
docker-compose down

# 5. 带监控启动
docker-compose --profile monitoring up -d
```

### docker 部署后快速测试

```bash
# 1) 健康检查
curl -s http://127.0.0.1:8000/health

# 2) OpenAI 兼容接口：查看模型列表
curl -s http://127.0.0.1:8000/v1/models

# 3) OpenAI 兼容接口：最小聊天请求
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "default",
    "messages": [{"role": "user", "content": "你好，做个自我介绍"}],
    "max_tokens": 64,
    "temperature": 0.2
  }'
```

> 若你的 `served_model_name` 不是 `default`，请将请求中的 `model` 改为实际名称。

### Langchain调用示例

```python
from langchain_openai import ChatOpenAI
import yaml
from pathlib import Path

# 加载配置
config_path = Path(__file__).parent / "config" / "vllm.yaml"
with open(config_path) as f:
    config = yaml.safe_load(f)

# 创建客户端
llm = ChatOpenAI(
    model=config["server"].get("served_model_name", "default"),
    openai_api_key="EMPTY",
    base_url=f"http://{config['server']['host']}:{config['server']['port']}/v1",
    temperature=0.7,
    max_tokens=512,
)

# 调用
response = llm.invoke("你好，请介绍一下自己")
print(response.content)
```



## 各模型配置详解


## 硬件配置建议

```yaml
# 根据不同模型调整硬件配置

# Qwen2.5-7B (单卡 16GB 即可)
hardware:
  tensor_parallel_size: 1
  gpu_memory_utilization: 0.85

# Qwen2.5-14B (建议 2 卡 16GB 或单卡 32GB)
hardware:
  tensor_parallel_size: 2
  gpu_memory_utilization: 0.85

# Qwen2.5-72B (建议 4 卡 24GB+)
hardware:
  tensor_parallel_size: 4
  gpu_memory_utilization: 0.85

# Qwen2.5-VL-7B (多模态，单卡 24GB 推荐)
hardware:
  tensor_parallel_size: 1
  gpu_memory_utilization: 0.8  # 多模态预留更多显存

# Qwen2.5-VL-32B / Qwen3-VL-32B (多模态，建议 2 卡 40GB+)
hardware:
  tensor_parallel_size: 2
  gpu_memory_utilization: 0.8
```


## 模型切换（重启）

### 本地部署-切换模型

```shell
export MODEL_PRESET=qwen2.5-vl-7b
python3 scripts/start.py start
```


### docker部署--切换模型

```shell
# docker-compose 中切换
cat > .env << EOF
MODEL_PRESET=qwen2.5-vl-7b
CUDA_VISIBLE_DEVICES=0
TENSOR_PARALLEL_SIZE=1
EOF

docker-compose up -d
```


## 性能优化建议

```yaml
# 针对不同场景的性能配置

# 高并发场景（QPS 优先）
performance:
  max_num_seqs: 64
  max_num_batched_tokens: 65536
  enable_prefix_caching: true

# 长文本场景（长上下文优先）
performance:
  max_num_seqs: 8
  max_num_batched_tokens: 8192
  enable_prefix_caching: true
  max_model_len: 131072  # 支持 128K 上下文

# 显存受限场景
hardware:
  gpu_memory_utilization: 0.7  # 降低显存占用
  tensor_parallel_size: 1
performance:
  max_num_seqs: 8
  max_num_batched_tokens: 8192
```