# 智能客服意图识别 · 轻量 LLM（模式 B）

## 1. 概述

在保留 **rules 为默认** 的前提下，新增 `CHATBOT_INTENT_BACKEND=llm`：

| 后端 | 说明 |
|------|------|
| `rules`（默认） | 纯规则启发式 |
| `llm` | **模式 B**：硬规则闸 → rules 主判 → 边界场景进程内小模型窄触发 |
| `bert` | 须微调三分类 BERT（暂不推荐） |

轻量模型：**Qwen2.5-0.5B-Instruct**，在 **models-app 进程内 CPU 推理**（与嵌入模型相同加载方式），无需 Ollama 侧车，避免与 vLLM 主答（GPU）争抢资源。

## 2. 代码结构

```
app/llm/
├── chatbot_intent_llm_local.py   # 进程内 HF 加载 + CPU 推理
app/llm/graphs/
├── chatbot_intent.py             # 统一入口（async 支持 llm）
├── chatbot_intent_rules.py       # 规则 + apply_intent_hard_gates
└── chatbot_intent_llm.py         # 模式 B + 窄触发 + JSON 校验
```

生产路径须调用 **`classify_chatbot_intent_async`**（LangGraph `intent_classify`、legacy `ChatbotService` 已接入）。

## 3. 配置项

```env
CHATBOT_INTENT_BACKEND=rules

# llm 后端（进程内，与嵌入模型相同策略：本地路径优先，否则 HuggingFace 在线）
CHATBOT_INTENT_LLM_MODEL_PATH=/workspace/models/llm/qwen2.5-0.5b-instruct
CHATBOT_INTENT_LLM_MODEL_NAME=Qwen/Qwen2.5-0.5B-Instruct
CHATBOT_INTENT_LLM_DEVICE=cpu
CHATBOT_INTENT_LLM_MAX_TOKENS=128
CHATBOT_INTENT_LLM_TEMPERATURE=0
CHATBOT_INTENT_LLM_CONF_THRESHOLD=0.78
CHATBOT_INTENT_LLM_FALLBACK_TO_RULES=true
```

- **离线**：设置 `CHATBOT_INTENT_LLM_MODEL_PATH` 指向宿主机已下载的 HF 目录（compose 挂载见 §4）。
- **在线**：不设置 `MODEL_PATH` 时，首次调用会从 HuggingFace 按 `MODEL_NAME` 自动下载到容器内 `~/.cache/huggingface`。

### 窄触发（模式 B）

在 rules 已给出结果后，满足任一条件才调用轻量 LLM：

- `intent_confidence < CHATBOT_INTENT_LLM_CONF_THRESHOLD`（默认 0.78）
- `intent_reason` 含 `mixed_`（混合句 data+conceptual）
- `intent_reason` 含 `ambiguous_pattern_resolved_by_ctx`（指代续问边界）

高置信度的明确 `data_query` / `kb_qa` **不会**调用 LLM，控制成本与延迟。

## 4. 部署（Docker Compose）

### 4.1 宿主机路径（与嵌入模型一致）

| 路径 | 用途 |
|------|------|
| `INTENT_LLM_MODELS_HOST_PATH`（默认 `/aidata/models/llm`） | 宿主机根目录 |
| `${INTENT_LLM_MODELS_HOST_PATH}/qwen2.5-0.5b-instruct` | HF 权重目录，bind 到容器 `/workspace/models/llm/qwen2.5-0.5b-instruct` |

**可直接挂载标准 HuggingFace 目录**（与 `Qwen3-Embedding-0.6B` 相同），无需魔塔、`ollama create` 等额外步骤。

### 4.2 下载模型（任选其一）

**方式 A：huggingface-cli（推荐，不必魔塔）**

```bash
pip install -U huggingface_hub
mkdir -p /aidata/models/llm
huggingface-cli download Qwen/Qwen2.5-0.5B-Instruct \
  --local-dir /aidata/models/llm/qwen2.5-0.5b-instruct
```

**方式 B：不预下载**

有网环境首次启用 `CHATBOT_INTENT_BACKEND=llm` 时，进程会自动从 HuggingFace 拉取（写入容器 `huggingface-cache` 卷）。

离线机房：按方式 A 预下载后拷贝 `${INTENT_LLM_MODELS_HOST_PATH}/qwen2.5-0.5b-instruct` 即可。

### 4.3 启动

```bash
cd app/app-deploy
docker compose up -d models-app
```

应用配置：

```env
CHATBOT_INTENT_BACKEND=llm
CHATBOT_INTENT_LLM_MODEL_PATH=/workspace/models/llm/qwen2.5-0.5b-instruct
CHATBOT_INTENT_LLM_DEVICE=cpu
```

重启 `models-app` 后观察日志：`ChatbotIntentLocalLlm: loaded target=...`；窄触发时出现 `chatbot.intent_llm narrow_trigger`。

## 5. 与 BERT 方案对比

| | BERT `bert` | 轻量 LLM `llm` |
|--|-------------|----------------|
| 训练 | 必须微调 | **预训练 Instruct 即可** |
| 部署 | transformers 进程内 | transformers 进程内（CPU） |
| 调用频率 | 非闸区全量（若启用） | **仅窄触发** |
| 默认 | 关闭 | 关闭（`rules`） |

## 6. 测试

```bash
pytest tests/test_chatbot_intent_rules.py tests/test_chatbot_intent_backend.py tests/test_chatbot_intent_llm.py -q
```

## 7. 观测

- 成功：`intent_reason` 含 `intent_llm|...`
- 未触发 LLM：与 rules 相同的 `structured_query_heuristic` 等
- 失败回退：`intent_llm_fallback_rules|...`
