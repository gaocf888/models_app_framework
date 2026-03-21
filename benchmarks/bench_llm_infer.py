from __future__ import annotations

"""
简单的 /llm/infer 压测脚本骨架。

说明：
- 使用 httpx.AsyncClient 并发调用 /llm/infer；
- 打印粗略 QPS 与 P95/P99 延迟统计；
- 仅作为示例骨架，实际压测建议使用 locust/k6 等专业工具。
"""

import asyncio
import statistics
import time
from typing import List

import httpx


async def worker(client: httpx.AsyncClient, num_requests: int, latencies: List[float]) -> None:
    payload = {
        "user_id": "bench",
        "session_id": "bench-session",
        "prompt": "请用一句话自我介绍。",
        "model": "default",
        "enable_rag": False,
        "enable_context": False,
    }
    for _ in range(num_requests):
        t0 = time.perf_counter()
        resp = await client.post("/llm/infer", json=payload)
        resp.raise_for_status()
        latencies.append(time.perf_counter() - t0)


async def main(concurrency: int = 5, total: int = 50, base_url: str = "http://localhost:8000") -> None:
    per_worker = total // concurrency
    latencies: List[float] = []

    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        tasks = [worker(client, per_worker, latencies) for _ in range(concurrency)]
        t0 = time.perf_counter()
        await asyncio.gather(*tasks)
        duration = time.perf_counter() - t0

    if not latencies:
        print("no requests completed")
        return

    latencies_ms = [x * 1000 for x in latencies]
    latencies_ms.sort()
    p95 = latencies_ms[int(0.95 * len(latencies_ms)) - 1]
    p99 = latencies_ms[int(0.99 * len(latencies_ms)) - 1]
    avg = statistics.mean(latencies_ms)
    qps = len(latencies) / duration

    print(f"Total requests: {len(latencies)}")
    print(f"Total time: {duration:.2f}s")
    print(f"QPS: {qps:.2f}")
    print(f"Avg latency: {avg:.2f} ms")
    print(f"P95 latency: {p95:.2f} ms")
    print(f"P99 latency: {p99:.2f} ms")


if __name__ == "__main__":
    asyncio.run(main())

