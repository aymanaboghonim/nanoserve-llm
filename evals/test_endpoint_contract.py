"""Endpoint contract tests against the served vLLM OpenAI-compatible API.

These assert *structural* correctness: HTTP status, JSON shape, usage fields,
model echo, and a soft latency budget. No LLM-as-judge involved — deterministic
and fast. All tests require a live endpoint; see ``conftest.py``.
"""

from __future__ import annotations

import time

import pytest

from nanoserve_llm.config import Settings

pytestmark = pytest.mark.e2e

PROMPT = "What is the capital of France?"

# Soft latency budget for a ~3B AWQ model on an RTX 3070 with 64 max tokens.
# TTFT + decode of 64 tokens typically lands well under this; violations hint at
# KV-cache saturation or queue pressure (see Phase 4 telemetry).
LATENCY_BUDGET_S = 30.0


def _chat(client, settings: Settings) -> dict:
    resp = client.chat.completions.create(
        model=settings.serving.model,
        messages=[{"role": "user", "content": PROMPT}],
        max_tokens=settings.load_test.max_tokens,
        temperature=0.0,
        # Granite-4.2 defaults thinking ON; disable it so the deterministic
        # contract tests assert on real content, not think traces.
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    return resp.model_dump()


class TestChatCompletionsContract:
    def test_returns_choices_with_content(self, openai_client, settings: Settings) -> None:
        data = _chat(openai_client, settings)
        choices = data["choices"]
        assert len(choices) >= 1
        message = choices[0]["message"]
        assert isinstance(message["content"], str)
        assert message["content"].strip() != ""

    def test_finish_reason_is_valid(self, openai_client, settings: Settings) -> None:
        data = _chat(openai_client, settings)
        assert data["choices"][0]["finish_reason"] in {"stop", "length"}

    def test_usage_fields_present(self, openai_client, settings: Settings) -> None:
        data = _chat(openai_client, settings)
        usage = data["usage"]
        assert usage["prompt_tokens"] > 0
        assert usage["completion_tokens"] > 0
        assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]

    def test_model_echo(self, openai_client, settings: Settings) -> None:
        data = _chat(openai_client, settings)
        assert data["model"] == settings.serving.model

    def test_object_and_id_shape(self, openai_client, settings: Settings) -> None:
        data = _chat(openai_client, settings)
        assert data["object"] == "chat.completion"
        assert isinstance(data["id"], str) and data["id"].startswith("chatcmpl-")

    def test_latency_within_budget(self, openai_client, settings: Settings) -> None:
        start = time.monotonic()
        _chat(openai_client, settings)
        elapsed = time.monotonic() - start
        assert elapsed < LATENCY_BUDGET_S, (
            f"response took {elapsed:.1f}s (budget {LATENCY_BUDGET_S}s)"
        )
