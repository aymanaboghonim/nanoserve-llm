#!/usr/bin/env python3
"""NanoServe streaming latency benchmark — TTFT / ITL / throughput.

Measures the same decode latency the Phase-4 telemetry correlates with
(vllm:request_decode_time_seconds) but client-side, for a local run without
Prometheus.

Usage:
    uv run --group dev python scripts/latency_benchmark.py \
        --url http://localhost:8000/v1/chat/completions \
        --model ibm-granite/granite-4.2-3b \
        --prompt "Explain a KV cache." \
        --tokens 128 --requests 5

Outputs a rich terminal summary (per-request + aggregate TTFT/ITL/throughput).
"""

from __future__ import annotations

import argparse
import sys
import time
from statistics import mean, median
from typing import Any

try:
    from openai import OpenAI
except ImportError as exc:  # pragma: no cover
    print(f"error: missing dependency: {exc}. Run: uv sync --group dev", file=sys.stderr)
    raise SystemExit(1) from None

from nanoserve_llm.config import get_settings
from nanoserve_llm.logging import get_logger

logger = get_logger(__name__)


def _run_one(
    client: OpenAI,
    *,
    model: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    thinking: bool = False,
) -> dict[str, float]:
    """Stream one completion; return timing stats in seconds.

    Granite-4.2 defaults to thinking ON (the chat template enables it unless
    told otherwise), which burns the max_tokens budget on a <think> trace and
    can leave content empty. Throughput benchmarks therefore pass an explicit
    enable_thinking override (default off). When thinking IS on, the think
    tokens arrive as reasoning deltas (not content) — those are counted too so
    ITL/token-rate stay meaningful either way.
    """
    start = time.monotonic()
    stream = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=temperature,
        stream=True,
        extra_body={"chat_template_kwargs": {"enable_thinking": thinking}},
    )
    first_token_s: float | None = None
    prev_s = start
    token_count = 0
    for chunk in stream:
        now = time.monotonic()
        if first_token_s is None:
            first_token_s = now - start
        if chunk.choices:
            delta = chunk.choices[0].delta
            text = (
                delta.content
                or getattr(delta, "reasoning_content", None)
                or getattr(delta, "reasoning", None)
            )
        else:
            text = None
        if text:
            token_count += 1
            prev_s = now
    total_s = time.monotonic() - start
    itl = (
        (prev_s - (start + first_token_s)) / token_count if (token_count and first_token_s) else 0.0
    )
    tps = token_count / total_s if total_s else 0.0
    return {
        "ttft_s": first_token_s or 0.0,
        "itl_s": itl,
        "total_s": total_s,
        "tokens": float(token_count),
        "tps": tps,
    }


def _fmt(rows: list[dict[str, Any]], *, label: str) -> None:
    """Print an aggregate summary row."""
    ttft = [r["ttft_s"] for r in rows]
    itl = [r["itl_s"] for r in rows]
    total = [r["total_s"] for r in rows]
    tps = [r["tps"] for r in rows]
    print(
        f"{label:>12} | ttft avg={mean(ttft):6.3f}s p50={median(ttft):6.3f}s "
        f"| itl avg={mean(itl):6.4f}s p50={median(itl):6.4f}s "
        f"| total avg={mean(total):6.3f}s | tok/s avg={mean(tps):7.1f}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=get_settings().serving.base_url)
    parser.add_argument("--model", default=get_settings().serving.model)
    parser.add_argument("--prompt", default="Explain the concept of a KV cache.")
    parser.add_argument("--tokens", type=int, default=128, help="max_tokens per request")
    parser.add_argument("--requests", type=int, default=5)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument(
        "--thinking",
        action="store_true",
        help="Enable Granite thinking (reasoning deltas counted in ITL/tokens).",
    )
    parser.add_argument(
        "--warmup", type=int, default=1, help="warmup requests (excluded from stats)"
    )
    args = parser.parse_args(argv)

    client = OpenAI(base_url=args.url, api_key=get_settings().serving.api_key, timeout=120.0)

    logger.info("warming up (%d request(s))...", args.warmup)
    for _ in range(args.warmup):
        _run_one(
            client,
            model=args.model,
            prompt=args.prompt,
            max_tokens=args.tokens,
            temperature=args.temperature,
            thinking=args.thinking,
        )

    logger.info("benchmarking %d request(s)...", args.requests)
    results: list[dict[str, Any]] = []
    for i in range(args.requests):
        stats = _run_one(
            client,
            model=args.model,
            prompt=args.prompt,
            max_tokens=args.tokens,
            temperature=args.temperature,
            thinking=args.thinking,
        )
        results.append(stats)
        print(
            f"  req {i + 1:>2}/{args.requests}: "
            f"ttft={stats['ttft_s']:.3f}s itl={stats['itl_s']:.4f}s "
            f"tokens={int(stats['tokens'])} tok/s={stats['tps']:.1f}"
        )

    print("\n=== summary ===")
    _fmt(results, label="all")
    print("(correlate with Phase-4 vllm:request_decode_time_seconds)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
