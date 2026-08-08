from __future__ import annotations

"""
简单的 /chatbot/chat/stream（SSE）压测脚本骨架。
"""

import asyncio
import json
import statistics
import time
from typing import List

import httpx


async def worker(client: httpx.AsyncClient, num_requests: int, latencies: List[float]) -> None:
    payload = {
        "user_id": "bench",
        "session_id": "bench-session",
        "query": "你好，请简单介绍一下你能做什么。",
        "enable_rag": False,
        "enable_context": False,
    }
    for _ in range(num_requests):
        t0 = time.perf_counter()
        async with client.stream("POST", "/chatbot/chat/stream", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw:
                    continue
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if ev.get("finished") is True or ev.get("error"):
                    break
        latencies.append(time.perf_counter() - t0)


async def main(concurrency: int = 5, total: int = 50, base_url: str = "http://localhost:8000") -> None:
    per_worker = total // concurrency
    latencies: List[float] = []

    async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as client:
        tasks = [worker(client, per_worker, latencies) for _ in range(concurrency)]
        t0 = time.perf_counter()
        await asyncio.gather(*tasks)
        duration = time.perf_counter() - t0

    if not latencies:
        print("no successful requests")
        return

    print(f"requests={len(latencies)} concurrency={concurrency} wall_s={duration:.2f}")
    print(f"latency_avg_s={statistics.mean(latencies):.3f}")
    print(f"latency_p50_s={statistics.median(latencies):.3f}")
    if len(latencies) >= 20:
        print(f"latency_p95_s={statistics.quantiles(latencies, n=20)[18]:.3f}")


if __name__ == "__main__":
    asyncio.run(main())
