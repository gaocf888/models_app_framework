# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Benchmark script for vLLM OpenAI-compatible chat/completions endpoint.

Target use case:
- Qwen2.5-VL-32B on MuXi N260 server
- Evaluate performance under different max_tokens and concurrency
- Generate vendor-friendly metrics and export files
"""

import argparse
import asyncio
import csv
import json
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
    latency_s: float
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
    avg_latency_s: float
    p50_latency_s: float
    p95_latency_s: float
    p99_latency_s: float
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


def build_payload(
    model: str,
    prompt: str,
    max_tokens: int,
    disable_thinking: bool,
    temperature: float,
) -> Dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    if disable_thinking:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    return payload


async def single_request(
    client: httpx.AsyncClient,
    url: str,
    payload: Dict,
    scenario_name: str,
    max_tokens: int,
    concurrency: int,
    request_id: int,
) -> RequestResult:
    t0 = time.perf_counter()
    try:
        resp = await client.post(url, json=payload)
        latency_s = time.perf_counter() - t0

        if resp.status_code != 200:
            return RequestResult(
                scenario_name=scenario_name,
                max_tokens=max_tokens,
                concurrency=concurrency,
                request_id=request_id,
                success=False,
                status_code=resp.status_code,
                latency_s=latency_s,
                prompt_tokens=None,
                completion_tokens=None,
                total_tokens=None,
                finish_reason=None,
                error=(resp.text or "")[:500],
            )

        data = resp.json()
        usage = data.get("usage") or {}
        choices = data.get("choices") or []
        finish_reason = choices[0].get("finish_reason") if choices else None

        return RequestResult(
            scenario_name=scenario_name,
            max_tokens=max_tokens,
            concurrency=concurrency,
            request_id=request_id,
            success=True,
            status_code=200,
            latency_s=latency_s,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            finish_reason=finish_reason,
            error=None,
        )
    except Exception as exc:  # noqa: BLE001
        latency_s = time.perf_counter() - t0
        return RequestResult(
            scenario_name=scenario_name,
            max_tokens=max_tokens,
            concurrency=concurrency,
            request_id=request_id,
            success=False,
            status_code=0,
            latency_s=latency_s,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            finish_reason=None,
            error=str(exc),
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
    )

    limits = httpx.Limits(max_connections=max(64, concurrency * 4), max_keepalive_connections=32)
    timeout = httpx.Timeout(timeout_s)
    results: List[RequestResult] = []
    results_lock = asyncio.Lock()
    request_id_queue: asyncio.Queue[int] = asyncio.Queue()
    for request_id in range(1, total_requests + 1):
        request_id_queue.put_nowait(request_id)

    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        t_start = time.perf_counter()

        async def worker() -> None:
            while True:
                try:
                    request_id = request_id_queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                result = await single_request(
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
                request_id_queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
        await asyncio.gather(*workers)
        wall_time = time.perf_counter() - t_start

    success_results = [r for r in results if r.success]
    failed_results = [r for r in results if not r.success]

    latencies = [r.latency_s for r in success_results]
    prompt_tokens = [r.prompt_tokens for r in success_results if r.prompt_tokens is not None]
    completion_tokens = [r.completion_tokens for r in success_results if r.completion_tokens is not None]

    finish_reason_count: Dict[str, int] = {}
    for r in success_results:
        k = r.finish_reason or "unknown"
        finish_reason_count[k] = finish_reason_count.get(k, 0) + 1

    total_completion_tokens = sum(completion_tokens) if completion_tokens else 0
    req_per_s = (len(success_results) / wall_time) if wall_time > 0 else 0.0
    out_tokens_per_s = (total_completion_tokens / wall_time) if wall_time > 0 else 0.0

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
        avg_latency_s=statistics.mean(latencies) if latencies else 0.0,
        p50_latency_s=percentile(latencies, 0.50) if latencies else 0.0,
        p95_latency_s=percentile(latencies, 0.95) if latencies else 0.0,
        p99_latency_s=percentile(latencies, 0.99) if latencies else 0.0,
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
        f"latency(s): avg={summary.avg_latency_s:.2f} p50={summary.p50_latency_s:.2f} "
        f"p95={summary.p95_latency_s:.2f} p99={summary.p99_latency_s:.2f}"
    )
    print(
        f"tokens: avg_prompt={summary.avg_prompt_tokens:.1f} "
        f"avg_completion={summary.avg_completion_tokens:.1f}"
    )
    print(f"finish_reason_count={summary.finish_reason_count}")


def save_outputs(
    output_dir: Path,
    summaries: List[ScenarioSummary],
    all_results: List[RequestResult],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")

    summary_json = output_dir / f"qwen25vl32b_summary_{ts}.json"
    detail_json = output_dir / f"qwen25vl32b_details_{ts}.json"
    summary_csv = output_dir / f"qwen25vl32b_summary_{ts}.csv"
    detail_csv = output_dir / f"qwen25vl32b_details_{ts}.csv"

    summary_data = [asdict(x) for x in summaries]
    detail_data = [asdict(x) for x in all_results]

    summary_json.write_text(json.dumps(summary_data, ensure_ascii=False, indent=2), encoding="utf-8")
    detail_json.write_text(json.dumps(detail_data, ensure_ascii=False, indent=2), encoding="utf-8")

    with summary_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
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
                "avg_latency_s",
                "p50_latency_s",
                "p95_latency_s",
                "p99_latency_s",
                "avg_prompt_tokens",
                "avg_completion_tokens",
                "finish_reason_count",
            ],
        )
        writer.writeheader()
        for row in summary_data:
            writer.writerow(row)

    with detail_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "scenario_name",
                "max_tokens",
                "concurrency",
                "request_id",
                "success",
                "status_code",
                "latency_s",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "finish_reason",
                "error",
            ],
        )
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
        description="Benchmark vLLM qwen2.5-vl-32b on MuXi N260 with max_tokens x concurrency matrix"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="vLLM base URL")
    parser.add_argument("--model", default="qwen2.5-vl-32b-instruct", help="model name")
    parser.add_argument("--prompt", default="你好，做个自我介绍", help="benchmark prompt")
    parser.add_argument("--max-tokens-list", default="32,64,128,256,512", help="comma-separated max_tokens")
    parser.add_argument("--concurrency-list", default="1,2,4,8", help="comma-separated concurrency levels")
    parser.add_argument("--requests-per-worker", type=int, default=3, help="requests each worker sends")
    parser.add_argument("--timeout", type=float, default=600.0, help="request timeout seconds")
    parser.add_argument("--temperature", type=float, default=0.7, help="sampling temperature")
    parser.add_argument("--disable-thinking", action="store_true", help="set chat_template_kwargs.enable_thinking=false")
    parser.add_argument("--output-dir", default="benchmarks/results", help="output directory")
    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> None:
    max_tokens_list = parse_int_list(args.max_tokens_list)
    concurrency_list = parse_int_list(args.concurrency_list)

    if args.requests_per_worker <= 0:
        raise ValueError("--requests-per-worker must be > 0")

    print("=== Benchmark Plan ===")
    print(f"base_url={args.base_url}")
    print(f"model={args.model}")
    print(f"max_tokens_list={max_tokens_list}")
    print(f"concurrency_list={concurrency_list}")
    print(f"requests_per_worker={args.requests_per_worker}")
    print(f"temperature={args.temperature}")
    print(f"disable_thinking={args.disable_thinking}")

    summaries: List[ScenarioSummary] = []
    all_results: List[RequestResult] = []

    total_scenarios = len(max_tokens_list) * len(concurrency_list)
    scenario_idx = 0

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
            )
            print_summary(summary)
            summaries.append(summary)
            all_results.extend(detail)

    save_outputs(Path(args.output_dir), summaries, all_results)


def main() -> None:
    args = parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()

