# -*- coding: utf-8 -*-
from __future__ import annotations

"""
vLLM OpenAI Chat Completions 基准测试脚本（单机场景）。

用途：
- 对比不同 max_tokens 的总耗时与解码吞吐；
- 可选流式模式，统计首 token 时间（TTFT）；
- 辅助定位“慢在首包”还是“慢在逐 token 解码”。
"""

import argparse
import json
import statistics
import time
from dataclasses import dataclass
from typing import List, Optional

import requests


@dataclass
class BenchResult:
    max_tokens: int
    round_idx: int
    success: bool
    status_code: int
    total_s: float
    ttft_s: Optional[float]
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    finish_reason: Optional[str]
    error: Optional[str] = None

    @property
    def decode_tps(self) -> Optional[float]:
        """completion_tokens / (total_s - ttft_s)"""
        if self.completion_tokens is None:
            return None
        if self.ttft_s is None:
            # 非流式场景没有 TTFT，只能给出整体 token/s
            if self.total_s <= 0:
                return None
            return self.completion_tokens / self.total_s
        decode_time = self.total_s - self.ttft_s
        if decode_time <= 0:
            return None
        return self.completion_tokens / decode_time


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark vLLM /v1/chat/completions")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="vLLM 服务地址")
    parser.add_argument("--model", required=True, help="模型名称（如 qwen2.5-vl-32b-instruct）")
    parser.add_argument("--prompt", default="你好，做个自我介绍", help="测试提示词")
    parser.add_argument(
        "--max-tokens-list",
        default="32,64,128,256,512",
        help="逗号分隔的 max_tokens 列表",
    )
    parser.add_argument("--rounds", type=int, default=3, help="每个 max_tokens 重复次数")
    parser.add_argument("--timeout", type=float, default=600.0, help="单次请求超时时间（秒）")
    parser.add_argument(
        "--stream",
        action="store_true",
        help="启用流式请求（可统计 TTFT，推荐用于定位慢点）",
    )
    parser.add_argument(
        "--disable-thinking",
        action="store_true",
        help="在请求体中追加 chat_template_kwargs.enable_thinking=false",
    )
    return parser.parse_args()


def _build_payload(
    model: str,
    prompt: str,
    max_tokens: int,
    stream: bool,
    disable_thinking: bool,
) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "stream": stream,
    }
    if disable_thinking:
        # OpenAI 兼容请求推荐放顶层，不要放 extra_body
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    return payload


def run_once(
    session: requests.Session,
    base_url: str,
    model: str,
    prompt: str,
    max_tokens: int,
    timeout_s: float,
    round_idx: int,
    stream: bool,
    disable_thinking: bool,
) -> BenchResult:
    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    payload = _build_payload(
        model=model,
        prompt=prompt,
        max_tokens=max_tokens,
        stream=stream,
        disable_thinking=disable_thinking,
    )

    t0 = time.perf_counter()
    try:
        if not stream:
            resp = session.post(url, json=payload, timeout=timeout_s)
            total = time.perf_counter() - t0
            if not resp.ok:
                return BenchResult(
                    max_tokens=max_tokens,
                    round_idx=round_idx,
                    success=False,
                    status_code=resp.status_code,
                    total_s=total,
                    ttft_s=None,
                    prompt_tokens=None,
                    completion_tokens=None,
                    finish_reason=None,
                    error=resp.text[:500],
                )

            data = resp.json()
            usage = data.get("usage") or {}
            choices = data.get("choices") or []
            finish_reason = choices[0].get("finish_reason") if choices else None
            return BenchResult(
                max_tokens=max_tokens,
                round_idx=round_idx,
                success=True,
                status_code=resp.status_code,
                total_s=total,
                ttft_s=None,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                finish_reason=finish_reason,
            )

        # 流式：统计首 token 时间 + 总耗时
        ttft_s: Optional[float] = None
        completion_tokens: Optional[int] = None
        prompt_tokens: Optional[int] = None
        finish_reason: Optional[str] = None

        with session.post(url, json=payload, timeout=timeout_s, stream=True) as resp:
            if not resp.ok:
                total = time.perf_counter() - t0
                return BenchResult(
                    max_tokens=max_tokens,
                    round_idx=round_idx,
                    success=False,
                    status_code=resp.status_code,
                    total_s=total,
                    ttft_s=None,
                    prompt_tokens=None,
                    completion_tokens=None,
                    finish_reason=None,
                    error=(resp.text or "")[:500],
                )

            for raw_line in resp.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                line = raw_line.strip()
                if not line.startswith("data:"):
                    continue
                data_str = line[len("data:"):].strip()
                if data_str == "[DONE]":
                    break

                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                # 首个增量 token 到达时间
                if ttft_s is None:
                    choices = chunk.get("choices") or []
                    if choices:
                        delta = choices[0].get("delta") or {}
                        if delta.get("content"):
                            ttft_s = time.perf_counter() - t0

                usage = chunk.get("usage")
                if usage:
                    prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
                    completion_tokens = usage.get("completion_tokens", completion_tokens)

                choices = chunk.get("choices") or []
                if choices and choices[0].get("finish_reason") is not None:
                    finish_reason = choices[0].get("finish_reason")

        total = time.perf_counter() - t0
        return BenchResult(
            max_tokens=max_tokens,
            round_idx=round_idx,
            success=True,
            status_code=200,
            total_s=total,
            ttft_s=ttft_s,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            finish_reason=finish_reason,
        )
    except Exception as exc:  # noqa: BLE001
        total = time.perf_counter() - t0
        return BenchResult(
            max_tokens=max_tokens,
            round_idx=round_idx,
            success=False,
            status_code=0,
            total_s=total,
            ttft_s=None,
            prompt_tokens=None,
            completion_tokens=None,
            finish_reason=None,
            error=str(exc),
        )


def percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    arr = sorted(values)
    idx = max(0, min(len(arr) - 1, int(round((len(arr) - 1) * p))))
    return arr[idx]


def summarize(results: List[BenchResult], max_tokens: int) -> None:
    ok = [r for r in results if r.success]
    fail = [r for r in results if not r.success]

    print(f"\n=== max_tokens={max_tokens} ===")
    print(f"success={len(ok)} fail={len(fail)}")
    if fail:
        print(f"last_error={fail[-1].error}")
    if not ok:
        return

    total_s_list = [r.total_s for r in ok]
    ttft_list = [r.ttft_s for r in ok if r.ttft_s is not None]
    decode_tps = [r.decode_tps for r in ok if r.decode_tps is not None]
    completion_tokens = [r.completion_tokens for r in ok if r.completion_tokens is not None]

    print(
        "total_s: avg={:.2f} p50={:.2f} p95={:.2f}".format(
            statistics.mean(total_s_list),
            percentile(total_s_list, 0.50),
            percentile(total_s_list, 0.95),
        )
    )
    if ttft_list:
        print(
            "ttft_s : avg={:.2f} p50={:.2f} p95={:.2f}".format(
                statistics.mean(ttft_list),
                percentile(ttft_list, 0.50),
                percentile(ttft_list, 0.95),
            )
        )
    if completion_tokens:
        print(
            "completion_tokens: avg={:.1f} min={} max={}".format(
                statistics.mean(completion_tokens),
                min(completion_tokens),
                max(completion_tokens),
            )
        )
    if decode_tps:
        print(
            "decode_tokens_per_s: avg={:.2f} p50={:.2f} p95={:.2f}".format(
                statistics.mean(decode_tps),
                percentile(decode_tps, 0.50),
                percentile(decode_tps, 0.95),
            )
        )

    finish_reason_cnt = {}
    for r in ok:
        key = r.finish_reason or "unknown"
        finish_reason_cnt[key] = finish_reason_cnt.get(key, 0) + 1
    print(f"finish_reason_count={finish_reason_cnt}")


def main() -> None:
    args = parse_args()
    max_tokens_list = [int(x.strip()) for x in args.max_tokens_list.split(",") if x.strip()]
    if not max_tokens_list:
        raise ValueError("max-tokens-list 不能为空")
    if args.rounds <= 0:
        raise ValueError("rounds 必须 > 0")

    print("=== vLLM chat completions benchmark ===")
    print(f"base_url={args.base_url}")
    print(f"model={args.model}")
    print(f"stream={args.stream}")
    print(f"disable_thinking={args.disable_thinking}")
    print(f"rounds={args.rounds}")
    print(f"max_tokens_list={max_tokens_list}")

    with requests.Session() as session:
        for max_tokens in max_tokens_list:
            results: List[BenchResult] = []
            for i in range(1, args.rounds + 1):
                r = run_once(
                    session=session,
                    base_url=args.base_url,
                    model=args.model,
                    prompt=args.prompt,
                    max_tokens=max_tokens,
                    timeout_s=args.timeout,
                    round_idx=i,
                    stream=args.stream,
                    disable_thinking=args.disable_thinking,
                )
                results.append(r)
                line = (
                    f"[max_tokens={max_tokens} round={i}] success={r.success} "
                    f"total={r.total_s:.2f}s"
                )
                if r.ttft_s is not None:
                    line += f" ttft={r.ttft_s:.2f}s"
                if r.decode_tps is not None:
                    line += f" decode_tps={r.decode_tps:.2f}"
                if r.completion_tokens is not None:
                    line += f" completion_tokens={r.completion_tokens}"
                if r.finish_reason:
                    line += f" finish_reason={r.finish_reason}"
                if r.error:
                    line += f" error={r.error}"
                print(line)
            summarize(results, max_tokens)


if __name__ == "__main__":
    main()

