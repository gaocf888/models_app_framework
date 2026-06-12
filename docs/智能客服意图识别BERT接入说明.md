# 智能客服意图识别 BERT 接入说明

## 1. 背景与目标

智能客服 LangGraph 在 `intent_classify` 节点对用户问句做意图分类，输出 `kb_qa` / `data_query` / `clarify`，再由 `_route_by_intent` 分流到 RAG、NL2SQL 或澄清话术。

本方案在**不改变图拓扑与路由契约**的前提下，增加可切换的 BERT 序列分类后端：

| 后端 | 配置值 | 说明 |
|------|--------|------|
| 规则（默认） | `rules` | 关键词 + 历史启发式，零模型依赖 |
| BERT | `bert` | **须使用已微调的三分类序列分类模型**（`AutoModelForSequenceClassification`），硬规则闸与 rules 共用 |

> **重要：不支持通用预训练 BERT**  
> 当前实现**不能**直接使用魔塔社区 / HuggingFace 下载的通用预训练模型（如 `bert-base-chinese`、`hfl/chinese-bert-wwm` 等）。  
> 这类模型只有编码器权重，**没有**针对 `kb_qa` / `data_query` / `clarify` 训练过的分类头；强行挂载后输出近似随机，无法用于生产分流。  
> **不想单独训练时，请保持 `CHATBOT_INTENT_BACKEND=rules`（默认）。**

## 2. 代码结构

```
app/llm/graphs/
├── chatbot_intent.py          # 统一入口 classify_chatbot_intent()
├── chatbot_intent_rules.py    # 规则实现 + apply_intent_hard_gates()
└── chatbot_intent_bert.py     # BERT 分类器（懒加载单例）
```

调用链（图路径与 legacy 路径一致）：

```
load_history → intent_classify → classify_chatbot_intent()
  ├─ backend=rules → classify_chatbot_intent_by_rules()
  └─ backend=bert  → apply_intent_hard_gates() → BERT 推理 →（失败）回退 rules
```

### 硬规则闸（两种后端共用）

以下场景**不经过 BERT 主分类**，行为与 rules 完全一致：

- 空 query → `clarify`
- 本轮带图 → `kb_qa`
- 极短句（≤2 字）/ 模糊指代模式 + 历史续问逻辑
- `enable_nl2sql_route=false` → `kb_qa`

## 3. 配置项

### 3.1 应用进程（`.env`）

```env
# 默认 rules；灰度验证 BERT 时改为 bert
CHATBOT_INTENT_BACKEND=rules

# BERT 专用（backend=bert 时生效）
CHATBOT_INTENT_BERT_MODEL_PATH=/workspace/models/intent/chatbot-intent-bert
# 无本地目录时可填 HuggingFace 模型名（需网络或 HF 缓存）
# CHATBOT_INTENT_BERT_MODEL_NAME=your-org/chatbot-intent-bert
CHATBOT_INTENT_BERT_DEVICE=cpu
CHATBOT_INTENT_BERT_MAX_LENGTH=256
CHATBOT_INTENT_BERT_FALLBACK_TO_RULES=true
```

与现有意图白名单、NL2SQL 开关配合使用（不变）：

```env
CHATBOT_INTENT_ENABLED=true
CHATBOT_INTENT_OUTPUT_LABELS=kb_qa,clarify,data_query
CHATBOT_NL2SQL_ROUTE_ENABLED=true
```

### 3.2 Docker Compose（宿主机挂载）

```env
INTENT_MODELS_HOST_PATH=/aidata/models/intent
```

compose 已将 `${INTENT_MODELS_HOST_PATH}/chatbot-intent-bert` 只读挂载到容器：

```
/workspace/models/intent/chatbot-intent-bert
```

与 `CHATBOT_INTENT_BERT_MODEL_PATH` 对齐即可离线加载。

## 4. 模型格式要求

### 4.1 必须使用「已微调」的序列分类模型

| 模型来源 | 能否直接用于 `CHATBOT_INTENT_BACKEND=bert` |
|----------|---------------------------------------------|
| 魔塔 / HF **通用预训练** BERT（`bert-base-chinese` 等） | **否** — 无业务三分类头，不可用 |
| 魔塔 / HF **已微调**文本/意图分类模型（标签须映射为 `kb_qa`/`data_query`/`clarify`） | 理论可行，需核对 `id2label` 与领域效果 |
| **本项目推荐**：自训或交付方提供的三分类 HF 导出目录 | **是** — 标准用法 |

代码加载方式为 `AutoModelForSequenceClassification.from_pretrained(...)`，推理依赖 `config.json` 中的 `id2label` 与分类头权重，因此**上线模型必须是完成微调并导出的完整目录**，不能仅拷贝预训练基座。

### 4.2 目录文件要求

使用 HuggingFace **序列分类** 导出目录，至少包含：

- `config.json`（含 `id2label`，标签名须为 `kb_qa` / `data_query` / `clarify` 之一）
- `pytorch_model.bin` 或 `model.safetensors`
- `tokenizer.json` / `vocab.txt` 等 tokenizer 文件

若 `id2label` 缺失，运行时使用默认映射 `{0: kb_qa, 1: data_query, 2: clarify}`。

### 4.3 训练建议（基座仅作微调起点，不能直用）

- 基座可从魔塔下载 `bert-base-chinese` 等，但**须在此基础上完成三分类微调并导出**后方可部署
- 标注三类与线上一致；混合句（「查记录并解释原因」）需在训练集中覆盖
- 输入格式与线上一致：`{query} [SEP] {history_tail}`（history 截断约 480 字符）

## 5. 部署步骤

### 5.1 保持 rules（默认，零变更）

无需挂载意图模型，`CHATBOT_INTENT_BACKEND=rules`（或不配置）。

### 5.2 切换 BERT

1. 将微调模型放到宿主机：`${INTENT_MODELS_HOST_PATH}/chatbot-intent-bert/`
2. 在 `app/app-deploy/.env` 设置：
   ```env
   CHATBOT_INTENT_BACKEND=bert
   CHATBOT_INTENT_BERT_MODEL_PATH=/workspace/models/intent/chatbot-intent-bert
   CHATBOT_INTENT_BERT_FALLBACK_TO_RULES=true
   ```
3. 重启 `models-app`（或 `models-app-gpu`）
4. 观察日志关键字：`ChatbotIntentBert: loaded model` 或 `bert_fallback_rules`

### 5.3 回滚

```env
CHATBOT_INTENT_BACKEND=rules
```

重启服务即可，无需改图或改 API。

## 6. 性能与资源

| 项 | rules | bert |
|----|-------|------|
| 延迟 | ~0.1ms | CPU 约 5–30ms；GPU 约 1–5ms |
| 内存 | 可忽略 | 约 100–400MB（视模型大小） |
| 多 worker | 无状态 | 每 worker 各载一份模型 |

相对整链（RAG + 重排 + LLM 流式）开销通常可接受。若与 vLLM 同卡，建议 `CHATBOT_INTENT_BERT_DEVICE=cpu` 或分卡。

## 7. 观测与排障

- 意图日志：`chatbot.intent decision backend=... label=... reason=...`
- BERT 成功：`intent_reason` 含 `bert_classifier|label=...`
- 回退 rules：`bert_fallback_rules|...`
- 硬规则闸：`has_images_default_kb_qa`、`short_followup_continues_thread` 等与 rules 相同

常见问题：

| 现象 | 处理 |
|------|------|
| 使用魔塔 `bert-base-chinese` 后分流异常/随机 | **预期行为**：通用预训练模型不可用；改回 `CHATBOT_INTENT_BACKEND=rules` 或更换为已微调三分类模型 |
| 启动后始终 `bert_fallback_rules` | 检查挂载路径、`CHATBOT_INTENT_BERT_MODEL_PATH`、模型文件完整性 |
| `unknown label` 警告 | 校正 `config.json` 的 `id2label` |
| 多 worker 内存翻倍 | 减少 `UVICORN_WORKERS` 或改用 CPU 小模型 |

## 8. 测试

```bash
pytest tests/test_chatbot_intent_rules.py tests/test_chatbot_intent_backend.py -q
```

## 9. 与下游能力的关系

- **软直通 / C-RAG / 厂别指代**：仅间接依赖 `intent_label`；BERT 误判 `clarify` 会阻断 `kb_qa` 子图（含软直通）
- **NL2SQL**：仅当 `intent_label=data_query` 且白名单放量时进入 `nl2sql_answer`
- **Legacy 路径**：`graph_enabled=false` 时同样走 `classify_chatbot_intent()`，backend 配置生效
