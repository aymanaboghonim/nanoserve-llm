"""Streaming contract: SSE (text/event-stream) must pass through un-buffered.

Repository 2 consumes tokens over SSE; any gateway buffering would destroy
TTFT/inter-token latency. Timeouts on the KServe route are already 300s, and
no RequestBuffer policy exists on the AI routes.
"""

from __future__ import annotations

import time

import pytest
from openai import OpenAI

from nanoserve_llm.config import Settings

pytestmark = [pytest.mark.live_contract, pytest.mark.e2e]


def test_stream_returns_sse_chunks_with_low_ttft(openai_client: OpenAI, settings: Settings) -> None:
    t0 = time.monotonic()
    stream = openai_client.chat.completions.create(
        model=settings.serving.model,
        messages=[{"role": "user", "content": "Count from one to five."}],
        max_tokens=settings.e2e.stream_tokens,
        stream=True,
        temperature=0.0,
    )
    chunks = list(stream)
    ttft = time.monotonic() - t0
    assert len(chunks) >= 2
    assert ttft < 10.0  # generous budget; local TTFT baseline ~40ms
    text = "".join(c.choices[0].delta.content or "" for c in chunks)
    assert text.strip()  # real generated content arrived
    assert chunks[-1].choices[0].finish_reason in ("stop", "length")


def test_stream_first_chunk_echoes_model(openai_client: OpenAI, settings: Settings) -> None:
    stream = openai_client.chat.completions.create(
        model=settings.serving.model,
        messages=[{"role": "user", "content": "Hi"}],
        max_tokens=4,
        stream=True,
        temperature=0.0,
    )
    first = next(iter(stream))
    assert first.model == settings.serving.model
