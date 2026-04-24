# README - vLLM Qwen2.5-VL-32B (N260)

脚本：`bench_vllm_qwen25vl32b_n260.py`

用途：测试 `qwen2.5-vl-32b-instruct` 在沐曦 N260 上的推理性能，覆盖 `max_tokens` 和并发维度。

## 1) 使用方式

在项目根目录执行：

```bash
python3 benchmarks/bench_vllm_qwen25vl32b_n260.py \
  --base-url http://127.0.0.1:8000 \
  --model qwen2.5-vl-32b-instruct \
  --max-tokens-list 32,64,128,256,512 \
  --concurrency-list 1,2,4,8 \
  --requests-per-worker 3 \
  --disable-thinking \
  --output-dir benchmarks/results
```

快速测试（耗时更短）：

```bash
python3 benchmarks/bench_vllm_qwen25vl32b_n260.py \
  --base-url http://127.0.0.1:8000 \
  --model qwen2.5-vl-32b-instruct \
  --max-tokens-list 64,256,512 \
  --concurrency-list 1,2,4 \
  --requests-per-worker 2 \
  --disable-thinking
```

## 2) 输出文件

默认在 `benchmarks/results` 生成 4 个文件：

- `qwen25vl32b_summary_*.json`
- `qwen25vl32b_details_*.json`
- `qwen25vl32b_summary_*.csv`
- `qwen25vl32b_details_*.csv`

## 3) 核心指标说明

- `success_rate`：成功率
- `avg/p50/p95/p99 latency`：延迟统计（秒）
- `req_per_s`：请求吞吐（QPS）
- `out_tokens_per_s`：输出 token 吞吐（越高越好）
- `avg_completion_tokens`：平均输出长度
- `finish_reason_count`：结束原因分布（`stop/length` 等）

## 4) 如何判断“慢”

- `TTFT` 不在本脚本统计范围内（本脚本是非流式并发压测）
- 若 `out_tokens_per_s` 很低且并发提升后增长有限，通常是模型解码性能瓶颈
- 若 `success_rate` 下降或 `p95/p99` 激增，通常是并发压力下容量不足

