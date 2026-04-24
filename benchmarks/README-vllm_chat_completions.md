# Benchmarks

本目录用于放置推理与接口压测脚本。

## vLLM Chat Completions 基准脚本

脚本：`bench_vllm_chat_completions.py`

用途：
- 对比不同 `max_tokens` 下的总耗时；
- 在流式模式下统计 TTFT（首 token 时间）；
- 估算解码速度（tokens/s），定位“慢在首包”还是“慢在解码”。

## 运行前准备

- 确保 vLLM 服务已启动（例如 `http://127.0.0.1:8000`）；
- 确保模型名与服务里 `served_model_name` 一致；
- 在项目根目录执行命令。

## 常用命令

### 1) 推荐：流式模式（可看 TTFT）

```bash
python3 benchmarks/bench_vllm_chat_completions.py \
  --base-url http://127.0.0.1:8000 \
  --model qwen2.5-vl-32b-instruct \
  --max-tokens-list 32,64,128,256,512 \
  --rounds 3 \
  --stream \
  --disable-thinking
```

### 2) 快速 smoke（先看趋势）

```bash
python3 benchmarks/bench_vllm_chat_completions.py \
  --base-url http://127.0.0.1:8000 \
  --model qwen2.5-vl-32b-instruct \
  --max-tokens-list 64,256 \
  --rounds 2 \
  --stream \
  --disable-thinking
```

### 3) 非流式模式（只看整体耗时）

```bash
python3 benchmarks/bench_vllm_chat_completions.py \
  --base-url http://127.0.0.1:8000 \
  --model qwen2.5-vl-32b-instruct \
  --max-tokens-list 64,128,256 \
  --rounds 3
```

## 参数说明

- `--base-url`：vLLM 服务地址，默认 `http://127.0.0.1:8000`
- `--model`：请求使用的模型名（必填）
- `--prompt`：测试 prompt，默认 `你好，做个自我介绍`
- `--max-tokens-list`：逗号分隔的 `max_tokens` 列表，默认 `32,64,128,256,512`
- `--rounds`：每组 `max_tokens` 重复次数，默认 `3`
- `--timeout`：单请求超时秒数，默认 `600`
- `--stream`：开启流式请求（可统计 TTFT）
- `--disable-thinking`：请求中附带 `chat_template_kwargs.enable_thinking=false`

## 输出解读

每组 `max_tokens` 会输出：
- `total_s`：总耗时（平均值、P50、P95）
- `ttft_s`：首 token 延迟（仅流式模式）
- `completion_tokens`：实际生成 token 数
- `decode_tokens_per_s`：解码速度（越高越好）
- `finish_reason_count`：结束原因统计（如 `stop`、`length`）

定位建议：
- `ttft_s` 高、`decode_tokens_per_s` 正常：更偏向首包/调度慢；
- `ttft_s` 正常、`decode_tokens_per_s` 低：更偏向模型解码慢；
- `finish_reason=length` 较多：说明经常被 `max_tokens` 截断。
