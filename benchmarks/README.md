# 大模型性能测试说明

针对 vLLM 部署的大模型，进行吞吐和延迟测试（首字延迟和字间延迟）。日常 serving 压测以 `bench_vllm_stream_serving.py` 为主；同目录下其余脚本见文末简要说明。

# README - vLLM 通用流式 Serving 压测

脚本：`bench_vllm_stream_serving.py`

用途：对任意 vLLM 部署（任意模型、任意显卡）进行 **流式** Chat Completions 压测，覆盖 `max_tokens` 与并发维度，并统计 **首字延迟（TTFT）**、**字间延迟（ITL）**、**单 token 解码耗时（TPOT）** 与端到端延迟。

与 `bench_vllm_qwen25vl32b_n260.py` 的区别：

| 能力 | `bench_vllm_qwen25vl32b_n260.py` | `bench_vllm_stream_serving.py` |
|------|----------------------------------|--------------------------------|
| 请求模式 | 非流式 | 流式（默认） |
| 首字延迟 TTFT | 不支持 | 支持 |
| 字间延迟 ITL | 不支持 | 支持（相邻 chunk 间隔） |
| 单 token 解码 TPOT | 不支持 | 支持 |
| 单请求 decode tokens/s | 不支持 | 支持 |
| 模型/硬件绑定 | 文件名偏 Qwen2.5-VL + N260 | 通用 CLI + 输出文件名含 model slug |
| API Key | 不支持 | 可选 `--api-key` |

## 1) 使用方式

在项目根目录执行：

```bash
python3 benchmarks/bench_vllm_stream_serving.py \
  --base-url http://127.0.0.1:8000 \
  --model qwen2.5-vl-32b-instruct-awq \
  --max-tokens-list 32,64,128,256,512 \
  --concurrency-list 1,2,4,8 \
  --requests-per-worker 3 \
  --disable-thinking \
  --output-dir benchmarks/results
```

快速测试（耗时更短）：

```bash
python3 benchmarks/bench_vllm_stream_serving.py \
  --base-url http://127.0.0.1:8000 \
  --model qwen2.5-vl-32b-instruct-awq \
  --max-tokens-list 64,256,512 \
  --concurrency-list 1,2,4,8,12,16,24,32 \
  --requests-per-worker 2 \
  --disable-thinking
```

极限吞吐测试：

```bash
python3 benchmarks/bench_vllm_stream_serving.py \
  --base-url http://127.0.0.1:8000 \
  --model qwen2.5-vl-32b-instruct-awq \
  --max-tokens-list 1024,2048,4096,8192 \
  --concurrency-list 1,4,8,16,24,32,48,64 \
  --requests-per-worker 2 \
  --disable-thinking
```

其他模型示例（纯文本 Chat，无需 `--disable-thinking`）：

```bash
python3 benchmarks/bench_vllm_stream_serving.py \
  --base-url http://127.0.0.1:8000 \
  --model meta-llama-3-8b-instruct \
  --prompt "Summarize transformer architecture in about 200 words." \
  --max-tokens-list 128,256,512 \
  --concurrency-list 1,4,8,16 \
  --requests-per-worker 3 \
  --temperature 0
```

带鉴权的网关：

```bash
python3 benchmarks/bench_vllm_stream_serving.py \
  --base-url https://your-gateway/v1 \
  --model your-model-name \
  --api-key "$OPENAI_API_KEY" \
  --max-tokens-list 256,512 \
  --concurrency-list 1,4,8
```

## 2) 输出文件

默认在 `benchmarks/results` 生成 4 个文件（文件名含 model slug，例如 `qwen2.5-vl-32b-instruct-awq_stream_*`）：

- `{model_slug}_stream_summary_*.json`
- `{model_slug}_stream_details_*.json`
- `{model_slug}_stream_summary_*.csv`
- `{model_slug}_stream_details_*.csv`

可用 `--output-slug my_run` 自定义文件名前缀。

## 3) 核心指标说明

### 场景级（summary）

- `success_rate`：成功率
- `req_per_s`：请求吞吐（QPS）
- `out_tokens_per_s`：系统级输出 token 吞吐（wall time 口径，越高越好）
- `avg/p50/p95/p99 total_latency_s`：单请求端到端延迟（发请求到流结束）
- `avg/p50/p95/p99 ttft_s`：**首字延迟**（Time To First Token，首个可见 content chunk）
- `avg/p50/p95/p99 itl_s`：**字间延迟**（Inter-chunk Latency，相邻 content chunk 到达间隔，场景内全样本汇总）
- `avg/p50/p95/p99 tpot_s`：**单 token 解码耗时**（`(total_latency - ttft) / (completion_tokens - 1)`）
- `avg/p50/p95 decode_tokens_per_s`：单请求平均解码速度（`completion_tokens / decode_time`）
- `avg_completion_tokens`：平均输出长度
- `finish_reason_count`：结束原因分布（`stop` / `length` 等）

### 请求级（details）

每条请求包含：

| 字段 | 含义 |
|------|------|
| `ttft_s` | 首字延迟 |
| `avg_itl_s` / `p50_itl_s` / `p95_itl_s` / `min_itl_s` / `max_itl_s` | 该请求内字间延迟统计 |
| `itl_sample_count` | 字间延迟样本数（= content chunk 数 - 1） |
| `tpot_s` | 单 token 解码耗时（基于 usage 或 chunk 数） |
| `stream_chunk_count` | 收到的 content chunk 数 |
| `decode_tokens_per_s` | 整段 decode 平均吞吐 |

## 4) 如何判断“慢”

- **`ttft_s` 高、`itl_s` / `tpot_s` 正常**：更偏向 prefill / 调度 / 排队（首包慢）
- **`ttft_s` 正常、`itl_s` 或 `tpot_s` 高**：更偏向 decode 阶段慢（字间等待长）
- **`out_tokens_per_s` 低且并发提升后增长有限**：系统 decode 容量接近上限
- **`success_rate` 下降或 `p95/p99 total_latency` 激增**：并发超过服务能力，请求在排队
- **`itl_sample_count` 很小**：vLLM 可能将多 token 合并为少量 chunk，此时 ITL 偏粗，可重点看 `tpot_s`
- **`avg_completion_tokens` 为 0 且 decode 指标缺失**：检查 vLLM 是否支持 `stream_options.include_usage=true`；必要时去掉 `--no-include-usage`

## 5) 参数说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--base-url` | `http://127.0.0.1:8000` | vLLM 服务地址 |
| `--model` | （必填） | 与 `served_model_name` 一致 |
| `--prompt` | 中文短 prompt | 纯文本压测 prompt |
| `--max-tokens-list` | `32,64,128,256,512` | 逗号分隔 |
| `--concurrency-list` | `1,2,4,8` | 逗号分隔 |
| `--requests-per-worker` | `3` | 每个并发 worker 发送的请求数 |
| `--temperature` | `0.0` | 建议对比测试保持 0 |
| `--disable-thinking` | 关闭 | 仅 Qwen thinking 模型需要 |
| `--no-include-usage` | 关闭 | 禁用流式 usage 回传 |
| `--api-key` | 空 | 可选 Bearer Token |
| `--output-dir` | `benchmarks/results` | 结果目录 |
| `--output-slug` | 从 model 推导 | 输出文件名前缀 |

## 6) 适用范围与限制

**适用：**

- 任意硬件上的 vLLM Chat Completions 服务
- 并发容量、首字/字间延迟、decode 性能评估
- 跨模型 / 跨环境对比（固定 prompt 与参数）

**不适用 / 需注意：**

- 当前仅 **纯文本** `messages`，不包含图像/音频多模态输入
- 仅测试 `/v1/chat/completions`，不含 Embedding / Rerank 等 API
- `--disable-thinking` 仅对支持 `chat_template_kwargs` 的模型有意义
- 默认 `temperature=0.0`；若与旧 N260 脚本对比，需统一温度与 prompt
- 压测客户端网络与 CPU 也会成为高并发下的因素，建议客户端与服务端分机部署

## 7) 与相关脚本的分工

本目录其余脚本仅作补充，日常压测优先使用上文主脚本。

- **`bench_vllm_stream_serving.py`（本脚本）**：流式 + 并发矩阵 + TTFT/ITL/TPOT + 导出，通用 serving 压测
- **`bench_vllm_qwen25vl32b_n260.py`**：非流式并发压测，覆盖 `max_tokens` 与并发维度，面向历史 N260 交付场景（Qwen2.5-VL-32B）；不统计 TTFT/ITL
- **`bench_vllm_chat_completions.py`**：串行、轻量，按 `max_tokens` 对比总耗时；加 `--stream` 可看 TTFT 趋势，适合快速 smoke
- **`bench_llm_infer.py` / `bench_chatbot.py` / `bench_nl2sql.py`**：业务接口压测骨架（分别打 `/llm/infer`、`/chatbot/chat`、`/nl2sql/query`），打印粗略 QPS 与 P95/P99，非正式 serving 基准
