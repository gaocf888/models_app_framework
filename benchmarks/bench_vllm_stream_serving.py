# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Generic streaming benchmark for vLLM OpenAI-compatible /v1/chat/completions.

Combines:
- max_tokens x concurrency matrix (concurrent load testing)
- Streaming requests with TTFT (首字延迟), ITL (字间延迟), TPOT
- CSV/JSON export for cross-model / cross-hardware comparison
"""

import argparse
import asyncio
import csv
import json
import re
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

import httpx


@dataclass
class RequestResult:
    scenario_name: str
    max_tokens: int
    concurrency: int
    request_id: int
    success: bool
    status_code: int
    total_latency_s: float
    ttft_s: Optional[float]
    avg_itl_s: Optional[float]
    p50_itl_s: Optional[float]
    p95_itl_s: Optional[float]
    min_itl_s: Optional[float]
    max_itl_s: Optional[float]
    itl_sample_count: int
    tpot_s: Optional[float]
    stream_chunk_count: int
    decode_tokens_per_s: Optional[float]
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    total_tokens: Optional[int]
    finish_reason: Optional[str]
    error: Optional[str]


@dataclass
class ScenarioSummary:
    scenario_name: str
    max_tokens: int
    concurrency: int
    total_requests: int
    success_requests: int
    failed_requests: int
    success_rate: float
    total_wall_time_s: float
    req_per_s: float
    out_tokens_per_s: float
    avg_total_latency_s: float
    p50_total_latency_s: float
    p95_total_latency_s: float
    p99_total_latency_s: float
    avg_ttft_s: float
    p50_ttft_s: float
    p95_ttft_s: float
    p99_ttft_s: float
    avg_itl_s: float
    p50_itl_s: float
    p95_itl_s: float
    p99_itl_s: float
    avg_tpot_s: float
    p50_tpot_s: float
    p95_tpot_s: float
    p99_tpot_s: float
    avg_decode_tokens_per_s: float
    p50_decode_tokens_per_s: float
    p95_decode_tokens_per_s: float
    avg_prompt_tokens: float
    avg_completion_tokens: float
    finish_reason_count: Dict[str, int]


def parse_int_list(raw: str) -> List[int]:
    values = [int(x.strip()) for x in raw.split(",") if x.strip()]
    if not values:
        raise ValueError("list arg is empty")
    for v in values:
        if v <= 0:
            raise ValueError(f"all values must be > 0, got {v}")
    return values


def percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    arr = sorted(values)
    idx = int(round((len(arr) - 1) * p))
    idx = max(0, min(idx, len(arr) - 1))
    return arr[idx]


def sanitize_slug(raw: str, fallback: str = "model") -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", raw.strip()).strip("_")
    return (slug[:64] if slug else fallback)


def build_headers(api_key: Optional[str]) -> Dict[str, str]:
    if not api_key:
        return {}
    return {"Authorization": f"Bearer {api_key}"}


def build_payload(
    model: str,
    prompt: str,
    max_tokens: int,
    disable_thinking: bool,
    temperature: float,
    include_usage: bool,
) -> Dict:
    payload: Dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }
    if include_usage:
        payload["stream_options"] = {"include_usage": True}
    if disable_thinking:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    return payload


def delta_has_visible_text(delta: Dict) -> bool:
    if not delta:
        return False
    for key in ("content", "reasoning_content", "refusal"):
        value = delta.get(key)
        if isinstance(value, str) and value:
            return True
    return False


def compute_decode_tokens_per_s(
    completion_tokens: Optional[int],
    total_latency_s: float,
    ttft_s: Optional[float],
) -> Optional[float]:
    if completion_tokens is None or ttft_s is None:
        return None
    decode_time = total_latency_s - ttft_s
    if decode_time <= 0:
        return None
    return completion_tokens / decode_time


def compute_itl_samples(chunk_timestamps_s: List[float]) -> List[float]:
    if len(chunk_timestamps_s) < 2:
        return []
    return [
        chunk_timestamps_s[i] - chunk_timestamps_s[i - 1]
        for i in range(1, len(chunk_timestamps_s))
    ]


def compute_itl_stats(itl_samples: List[float]) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float], Optional[float], int]:
    if not itl_samples:
        return None, None, None, None, None, 0
    return (
        statistics.mean(itl_samples),
        percentile(itl_samples, 0.50),
        percentile(itl_samples, 0.95),
        min(itl_samples),
        max(itl_samples),
        len(itl_samples),
    )


def compute_tpot_s(
    total_latency_s: float,
    ttft_s: Optional[float],
    completion_tokens: Optional[int],
    stream_chunk_count: int,
) -> Optional[float]:
    if ttft_s is None:
        return None
    decode_time = total_latency_s - ttft_s
    if decode_time <= 0:
        return None
    if completion_tokens is not None and completion_tokens > 1:
        return decode_time / (completion_tokens - 1)
    if stream_chunk_count > 1:
        return decode_time / (stream_chunk_count - 1)
    return None


def build_request_result(
    *,
    scenario_name: str,
    max_tokens: int,
    concurrency: int,
    request_id: int,
    success: bool,
    status_code: int,
    total_latency_s: float,
    ttft_s: Optional[float],
    chunk_timestamps_s: List[float],
    completion_tokens: Optional[int],
    prompt_tokens: Optional[int],
    total_tokens: Optional[int],
    finish_reason: Optional[str],
    error: Optional[str],
) -> tuple[RequestResult, List[float]]:
    itl_samples = compute_itl_samples(chunk_timestamps_s)
    avg_itl, p50_itl, p95_itl, min_itl, max_itl, itl_count = compute_itl_stats(itl_samples)
    stream_chunk_count = len(chunk_timestamps_s)
    tpot_s = compute_tpot_s(total_latency_s, ttft_s, completion_tokens, stream_chunk_count)
    decode_tokens_per_s = compute_decode_tokens_per_s(completion_tokens, total_latency_s, ttft_s)
    result = RequestResult(
        scenario_name=scenario_name,
        max_tokens=max_tokens,
        concurrency=concurrency,
        request_id=request_id,
        success=success,
        status_code=status_code,
        total_latency_s=total_latency_s,
        ttft_s=ttft_s,
        avg_itl_s=avg_itl,
        p50_itl_s=p50_itl,
        p95_itl_s=p95_itl,
        min_itl_s=min_itl,
        max_itl_s=max_itl,
        itl_sample_count=itl_count,
        tpot_s=tpot_s,
        stream_chunk_count=stream_chunk_count,
        decode_tokens_per_s=decode_tokens_per_s,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        finish_reason=finish_reason,
        error=error,
    )
    return result, itl_samples


async def single_request(
    client: httpx.AsyncClient,
    url: str,
    payload: Dict,
    scenario_name: str,
    max_tokens: int,
    concurrency: int,
    request_id: int,
) -> tuple[RequestResult, List[float]]:
    t0 = time.perf_counter()
    ttft_s: Optional[float] = None
    chunk_timestamps_s: List[float] = []
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    finish_reason: Optional[str] = None

    try:
        async with client.stream("POST", url, json=payload) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", errors="replace")
                total_latency_s = time.perf_counter() - t0
                return build_request_result(
                    scenario_name=scenario_name,
                    max_tokens=max_tokens,
                    concurrency=concurrency,
                    request_id=request_id,
                    success=False,
                    status_code=resp.status_code,
                    total_latency_s=total_latency_s,
                    ttft_s=None,
                    chunk_timestamps_s=[],
                    completion_tokens=None,
                    prompt_tokens=None,
                    total_tokens=None,
                    finish_reason=None,
                    error=body[:500],
                )

            async for raw_line in resp.aiter_lines():
                if not raw_line:
                    continue
                line = raw_line.strip()
                if not line.startswith("data:"):
                    continue
                data_str = line[len("data:") :].strip()
                if data_str == "[DONE]":
                    break

                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                choices = chunk.get("choices") or []
                if choices:
                    delta = choices[0].get("delta") or {}
                    if delta_has_visible_text(delta):
                        now_s = time.perf_counter() - t0
                        if ttft_s is None:
                            ttft_s = now_s
                        chunk_timestamps_s.append(now_s)

                usage = chunk.get("usage")
                if usage:
                    prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
                    completion_tokens = usage.get("completion_tokens", completion_tokens)
                    total_tokens = usage.get("total_tokens", total_tokens)

                if choices and choices[0].get("finish_reason") is not None:
                    finish_reason = choices[0].get("finish_reason")

        total_latency_s = time.perf_counter() - t0
        return build_request_result(
            scenario_name=scenario_name,
            max_tokens=max_tokens,
            concurrency=concurrency,
            request_id=request_id,
            success=True,
            status_code=200,
            total_latency_s=total_latency_s,
            ttft_s=ttft_s,
            chunk_timestamps_s=chunk_timestamps_s,
            completion_tokens=completion_tokens,
            prompt_tokens=prompt_tokens,
            total_tokens=total_tokens,
            finish_reason=finish_reason,
            error=None,
        )
    except Exception as exc:  # noqa: BLE001
        total_latency_s = time.perf_counter() - t0
        return build_request_result(
            scenario_name=scenario_name,
            max_tokens=max_tokens,
            concurrency=concurrency,
            request_id=request_id,
            success=False,
            status_code=0,
            total_latency_s=total_latency_s,
            ttft_s=ttft_s,
            chunk_timestamps_s=chunk_timestamps_s,
            completion_tokens=None,
            prompt_tokens=None,
            total_tokens=None,
            finish_reason=None,
            error=str(exc),
        )


def summarize_floats(values: List[float]) -> tuple[float, float, float, float]:
    if not values:
        return 0.0, 0.0, 0.0, 0.0
    return (
        statistics.mean(values),
        percentile(values, 0.50),
        percentile(values, 0.95),
        percentile(values, 0.99),
    )


async def run_scenario(
    base_url: str,
    model: str,
    prompt: str,
    max_tokens: int,
    concurrency: int,
    requests_per_worker: int,
    timeout_s: float,
    disable_thinking: bool,
    temperature: float,
    include_usage: bool,
    api_key: Optional[str],
) -> tuple[ScenarioSummary, List[RequestResult]]:
    total_requests = concurrency * requests_per_worker
    scenario_name = f"mt{max_tokens}_c{concurrency}"
    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    payload = build_payload(
        model=model,
        prompt=prompt,
        max_tokens=max_tokens,
        disable_thinking=disable_thinking,
        temperature=temperature,
        include_usage=include_usage,
    )

    limits = httpx.Limits(max_connections=max(64, concurrency * 4), max_keepalive_connections=32)
    timeout = httpx.Timeout(timeout_s)
    headers = build_headers(api_key)
    results: List[RequestResult] = []
    scenario_itl_samples: List[float] = []
    results_lock = asyncio.Lock()
    request_id_queue: asyncio.Queue[int] = asyncio.Queue()
    for request_id in range(1, total_requests + 1):
        request_id_queue.put_nowait(request_id)

    async with httpx.AsyncClient(timeout=timeout, limits=limits, headers=headers) as client:
        t_start = time.perf_counter()

        async def worker() -> None:
            while True:
                try:
                    request_id = request_id_queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                result, itl_samples = await single_request(
                    client=client,
                    url=url,
                    payload=payload,
                    scenario_name=scenario_name,
                    max_tokens=max_tokens,
                    concurrency=concurrency,
                    request_id=request_id,
                )
                async with results_lock:
                    results.append(result)
                    scenario_itl_samples.extend(itl_samples)
                request_id_queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
        await asyncio.gather(*workers)
        wall_time = time.perf_counter() - t_start

    success_results = [r for r in results if r.success]
    failed_results = [r for r in results if not r.success]

    total_latencies = [r.total_latency_s for r in success_results]
    ttft_values = [r.ttft_s for r in success_results if r.ttft_s is not None]
    decode_tps_values = [
        r.decode_tokens_per_s for r in success_results if r.decode_tokens_per_s is not None
    ]
    tpot_values = [r.tpot_s for r in success_results if r.tpot_s is not None]
    prompt_tokens = [r.prompt_tokens for r in success_results if r.prompt_tokens is not None]
    completion_tokens = [r.completion_tokens for r in success_results if r.completion_tokens is not None]

    finish_reason_count: Dict[str, int] = {}
    for r in success_results:
        k = r.finish_reason or "unknown"
        finish_reason_count[k] = finish_reason_count.get(k, 0) + 1

    total_completion_tokens = sum(completion_tokens) if completion_tokens else 0
    req_per_s = (len(success_results) / wall_time) if wall_time > 0 else 0.0
    out_tokens_per_s = (total_completion_tokens / wall_time) if wall_time > 0 else 0.0

    avg_total, p50_total, p95_total, p99_total = summarize_floats(total_latencies)
    avg_ttft, p50_ttft, p95_ttft, p99_ttft = summarize_floats(ttft_values)
    avg_itl, p50_itl, p95_itl, p99_itl = summarize_floats(scenario_itl_samples)
    avg_tpot, p50_tpot, p95_tpot, p99_tpot = summarize_floats(tpot_values)
    avg_decode, p50_decode, p95_decode, _ = summarize_floats(decode_tps_values)

    summary = ScenarioSummary(
        scenario_name=scenario_name,
        max_tokens=max_tokens,
        concurrency=concurrency,
        total_requests=total_requests,
        success_requests=len(success_results),
        failed_requests=len(failed_results),
        success_rate=(len(success_results) / total_requests) if total_requests > 0 else 0.0,
        total_wall_time_s=wall_time,
        req_per_s=req_per_s,
        out_tokens_per_s=out_tokens_per_s,
        avg_total_latency_s=avg_total,
        p50_total_latency_s=p50_total,
        p95_total_latency_s=p95_total,
        p99_total_latency_s=p99_total,
        avg_ttft_s=avg_ttft,
        p50_ttft_s=p50_ttft,
        p95_ttft_s=p95_ttft,
        p99_ttft_s=p99_ttft,
        avg_itl_s=avg_itl,
        p50_itl_s=p50_itl,
        p95_itl_s=p95_itl,
        p99_itl_s=p99_itl,
        avg_tpot_s=avg_tpot,
        p50_tpot_s=p50_tpot,
        p95_tpot_s=p95_tpot,
        p99_tpot_s=p99_tpot,
        avg_decode_tokens_per_s=avg_decode,
        p50_decode_tokens_per_s=p50_decode,
        p95_decode_tokens_per_s=p95_decode,
        avg_prompt_tokens=statistics.mean(prompt_tokens) if prompt_tokens else 0.0,
        avg_completion_tokens=statistics.mean(completion_tokens) if completion_tokens else 0.0,
        finish_reason_count=finish_reason_count,
    )
    return summary, results


def print_summary(summary: ScenarioSummary) -> None:
    print(f"\n=== Scenario {summary.scenario_name} ===")
    print(f"max_tokens={summary.max_tokens} concurrency={summary.concurrency}")
    print(
        f"requests={summary.total_requests} success={summary.success_requests} "
        f"failed={summary.failed_requests} success_rate={summary.success_rate * 100:.2f}%"
    )
    print(
        f"wall_time={summary.total_wall_time_s:.2f}s req/s={summary.req_per_s:.3f} "
        f"out_tokens/s={summary.out_tokens_per_s:.3f}"
    )
    print(
        f"total_latency(s): avg={summary.avg_total_latency_s:.2f} p50={summary.p50_total_latency_s:.2f} "
        f"p95={summary.p95_total_latency_s:.2f} p99={summary.p99_total_latency_s:.2f}"
    )
    print(
        f"ttft/首字延迟(s): avg={summary.avg_ttft_s:.3f} p50={summary.p50_ttft_s:.3f} "
        f"p95={summary.p95_ttft_s:.3f} p99={summary.p99_ttft_s:.3f}"
    )
    print(
        f"itl/字间延迟(s): avg={summary.avg_itl_s:.3f} p50={summary.p50_itl_s:.3f} "
        f"p95={summary.p95_itl_s:.3f} p99={summary.p99_itl_s:.3f}"
    )
    print(
        f"tpot/单token解码(s): avg={summary.avg_tpot_s:.3f} p50={summary.p50_tpot_s:.3f} "
        f"p95={summary.p95_tpot_s:.3f} p99={summary.p99_tpot_s:.3f}"
    )
    print(
        f"decode_tokens/s: avg={summary.avg_decode_tokens_per_s:.2f} "
        f"p50={summary.p50_decode_tokens_per_s:.2f} p95={summary.p95_decode_tokens_per_s:.2f}"
    )
    print(
        f"tokens: avg_prompt={summary.avg_prompt_tokens:.1f} "
        f"avg_completion={summary.avg_completion_tokens:.1f}"
    )
    print(f"finish_reason_count={summary.finish_reason_count}")


def save_outputs(
    output_dir: Path,
    model_slug: str,
    summaries: List[ScenarioSummary],
    all_results: List[RequestResult],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    prefix = f"{model_slug}_stream"

    summary_json = output_dir / f"{prefix}_summary_{ts}.json"
    detail_json = output_dir / f"{prefix}_details_{ts}.json"
    summary_csv = output_dir / f"{prefix}_summary_{ts}.csv"
    detail_csv = output_dir / f"{prefix}_details_{ts}.csv"

    summary_data = [asdict(x) for x in summaries]
    detail_data = [asdict(x) for x in all_results]

    summary_json.write_text(json.dumps(summary_data, ensure_ascii=False, indent=2), encoding="utf-8")
    detail_json.write_text(json.dumps(detail_data, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_fields = [
        "scenario_name",
        "max_tokens",
        "concurrency",
        "total_requests",
        "success_requests",
        "failed_requests",
        "success_rate",
        "total_wall_time_s",
        "req_per_s",
        "out_tokens_per_s",
        "avg_total_latency_s",
        "p50_total_latency_s",
        "p95_total_latency_s",
        "p99_total_latency_s",
        "avg_ttft_s",
        "p50_ttft_s",
        "p95_ttft_s",
        "p99_ttft_s",
        "avg_itl_s",
        "p50_itl_s",
        "p95_itl_s",
        "p99_itl_s",
        "avg_tpot_s",
        "p50_tpot_s",
        "p95_tpot_s",
        "p99_tpot_s",
        "avg_decode_tokens_per_s",
        "p50_decode_tokens_per_s",
        "p95_decode_tokens_per_s",
        "avg_prompt_tokens",
        "avg_completion_tokens",
        "finish_reason_count",
    ]
    detail_fields = [
        "scenario_name",
        "max_tokens",
        "concurrency",
        "request_id",
        "success",
        "status_code",
        "total_latency_s",
        "ttft_s",
        "avg_itl_s",
        "p50_itl_s",
        "p95_itl_s",
        "min_itl_s",
        "max_itl_s",
        "itl_sample_count",
        "tpot_s",
        "stream_chunk_count",
        "decode_tokens_per_s",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "finish_reason",
        "error",
    ]

    with summary_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=summary_fields)
        writer.writeheader()
        for row in summary_data:
            writer.writerow(row)

    with detail_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=detail_fields)
        writer.writeheader()
        for row in detail_data:
            writer.writerow(row)

    print("\n=== Exported files ===")
    print(summary_json)
    print(detail_json)
    print(summary_csv)
    print(detail_csv)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Streaming benchmark for vLLM /v1/chat/completions with "
            "max_tokens x concurrency matrix (model/hardware agnostic)"
        )
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="vLLM base URL")
    parser.add_argument("--model", required=True, help="model name (must match served_model_name)")
    parser.add_argument("--prompt", default="请用300字左右介绍深度学习的基本概念。", help="benchmark prompt")
    parser.add_argument("--max-tokens-list", default="32,64,128,256,512", help="comma-separated max_tokens")
    parser.add_argument("--concurrency-list", default="1,2,4,8", help="comma-separated concurrency levels")
    parser.add_argument("--requests-per-worker", type=int, default=3, help="requests each worker sends")
    parser.add_argument("--timeout", type=float, default=600.0, help="request timeout seconds")
    parser.add_argument("--temperature", type=float, default=0.0, help="sampling temperature")
    parser.add_argument(
        "--disable-thinking",
        action="store_true",
        help="set chat_template_kwargs.enable_thinking=false (Qwen thinking models)",
    )
    parser.add_argument(
        "--no-include-usage",
        action="store_true",
        help="do not send stream_options.include_usage=true",
    )
    parser.add_argument("--api-key", default="", help="optional Bearer token for gated endpoints")
    parser.add_argument("--output-dir", default="benchmarks/results", help="output directory")
    parser.add_argument(
        "--output-slug",
        default="",
        help="optional filename slug; default derived from --model",
    )
    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> None:
    max_tokens_list = parse_int_list(args.max_tokens_list)
    concurrency_list = parse_int_list(args.concurrency_list)

    if args.requests_per_worker <= 0:
        raise ValueError("--requests-per-worker must be > 0")

    include_usage = not args.no_include_usage
    model_slug = sanitize_slug(args.output_slug or args.model)

    print("=== Benchmark Plan ===")
    print(f"base_url={args.base_url}")
    print(f"model={args.model}")
    print(f"stream=True include_usage={include_usage}")
    print(f"max_tokens_list={max_tokens_list}")
    print(f"concurrency_list={concurrency_list}")
    print(f"requests_per_worker={args.requests_per_worker}")
    print(f"temperature={args.temperature}")
    print(f"disable_thinking={args.disable_thinking}")
    print(f"output_slug={model_slug}")

    summaries: List[ScenarioSummary] = []
    all_results: List[RequestResult] = []

    total_scenarios = len(max_tokens_list) * len(concurrency_list)
    scenario_idx = 0
    api_key = args.api_key.strip() or None

    for max_tokens in max_tokens_list:
        for concurrency in concurrency_list:
            scenario_idx += 1
            print(f"\n[{scenario_idx}/{total_scenarios}] Running max_tokens={max_tokens}, concurrency={concurrency}")
            summary, detail = await run_scenario(
                base_url=args.base_url,
                model=args.model,
                prompt=args.prompt,
                max_tokens=max_tokens,
                concurrency=concurrency,
                requests_per_worker=args.requests_per_worker,
                timeout_s=args.timeout,
                disable_thinking=args.disable_thinking,
                temperature=args.temperature,
                include_usage=include_usage,
                api_key=api_key,
            )
            print_summary(summary)
            summaries.append(summary)
            all_results.extend(detail)

    save_outputs(Path(args.output_dir), model_slug, summaries, all_results)


def main() -> None:
    args = parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
