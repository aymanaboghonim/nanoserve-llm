#!/usr/bin/env python3
"""NanoServe OTel tracer — emits inference spans to the OpenTelemetry Collector.

Tracks the two critical phases of a chat-completions request (same metrics the
Phase-4 telemetry layer correlates with):
  - ``ttft``   time to first token (streaming)
  - ``itl``    inter-token latency

Usage:
    uv run --group dev python scripts/tracer.py \
        --url http://localhost:8000/v1/chat/completions \
        --model Qwen/Qwen2.5-1.5B-Instruct-AWQ \
        --prompt "Explain a KV cache." \
        --otlp-endpoint localhost:4317 \
        --tokens 64

Signals: SIGINT/SIGTERM flush pending spans and exit cleanly (exit 0).
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from typing import Any

# OTel SDK is a dev-group dependency; fail loudly if the group is missing.
try:
    from openai import OpenAI
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
except ImportError as exc:  # pragma: no cover - exercised in base env
    print(f"error: missing dependency: {exc}. Run: uv sync --group dev", file=sys.stderr)
    raise SystemExit(1) from None

from nanoserve_llm.config import get_settings
from nanoserve_llm.logging import get_logger

logger = get_logger(__name__)

_graceful = False


def _handle_signal(signum: int, _frame: Any) -> None:
    """Flag a graceful shutdown (SIGINT/SIGTERM)."""
    global _graceful
    _graceful = True
    logger.info("signal %s received — flushing and exiting", signum)


def _emit_span(
    tracer: trace.Tracer,
    client: OpenAI,
    *,
    model: str,
    prompt: str,
    max_tokens: int,
) -> dict[str, float]:
    """Run one streaming chat-completions request and record ttft/itl spans."""
    span = tracer.start_span("chat.completions")
    span.set_attribute("gen_ai.request.model", model)
    start = time.monotonic()
    try:
        stream = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            stream=True,
        )
        first_token_s = None
        token_times: list[float] = []
        content = ""
        for chunk in stream:
            now = time.monotonic()
            if first_token_s is None:
                first_token_s = now - start
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                content += delta
                if token_times:
                    token_times.append(now - token_times[-1])
                else:
                    token_times.append(now - (start + first_token_s))
        total_s = time.monotonic() - start
        itl = sum(token_times) / len(token_times) if token_times else 0.0
        span.set_attribute("gen_ai.response.model", model)
        span.set_attribute("gen_ai.usage.completion_tokens", len(content.split()))
        span.set_attributes({"ttft_s": first_token_s or 0.0, "itl_s": itl, "total_s": total_s})
        return {"ttft_s": first_token_s or 0.0, "itl_s": itl, "total_s": total_s}
    except Exception as exc:  # noqa: BLE001
        span.record_exception(exc)
        span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
        logger.error("request failed: %s", exc)
        return {"ttft_s": 0.0, "itl_s": 0.0, "total_s": 0.0}
    finally:
        span.end()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url", default=get_settings().serving.base_url, help="OpenAI-compatible base URL"
    )
    parser.add_argument("--model", default=get_settings().serving.model, help="Model name")
    parser.add_argument("--prompt", default="Explain the concept of a KV cache.", help="Prompt")
    parser.add_argument("--tokens", type=int, default=64, help="max_tokens")
    parser.add_argument("--requests", type=int, default=1, help="Number of requests")
    parser.add_argument(
        "--otlp-endpoint", default=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "localhost:4317")
    )
    args = parser.parse_args(argv)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    provider = TracerProvider(resource=Resource.create({"service.name": "nanoserve-tracer"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=args.otlp_endpoint)))
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer("nanoserve.tracer")

    client = OpenAI(base_url=args.url, api_key=get_settings().serving.api_key, timeout=120.0)

    logger.info(
        "emitting %d request(s) to %s model=%s otlp=%s",
        args.requests,
        args.url,
        args.model,
        args.otlp_endpoint,
    )
    for i in range(args.requests):
        if _graceful:
            break
        stats = _emit_span(
            tracer, client, model=args.model, prompt=args.prompt, max_tokens=args.tokens
        )
        logger.info(
            "[%d/%d] ttft=%.3fs itl=%.3fs total=%.3fs",
            i + 1,
            args.requests,
            stats["ttft_s"],
            stats["itl_s"],
            stats["total_s"],
        )

    provider.shutdown()  # flush pending spans
    logger.info("done")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
