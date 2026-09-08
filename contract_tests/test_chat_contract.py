"""Chat completion contract: non-stream shape + SSE stream passthrough.

The agent consumes tokens over SSE (LangGraph astream) and structured
non-stream responses — both must be standard OpenAI shape, un-buffered.
"""

from __future__ import annotations

import json
import time

import httpx

# Granite-4.2 defaults thinking ON; the deterministic contract checks always
# disable it so they assert on real content. The OpenAI SDK rejects
# chat_template_kwargs as a top-level kwarg — it MUST ride in extra_body.
_EXTRA = {"chat_template_kwargs": {"enable_thinking": False}}


def _chat(client, model: str, **overrides) -> dict:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "What is the capital of France?"}],
        "max_tokens": 64,
        "temperature": 0.0,
        "extra_body": _EXTRA,
        **overrides,
    }
    return client.chat.completions.create(**body).model_dump()


def test_non_stream_returns_openai_shape(client, model: str) -> None:
    data = _chat(client, model)
    assert data["object"] == "chat.completion"
    assert data["id"].startswith("chatcmpl")
    assert data["model"] == model
    choices = data["choices"]
    assert len(choices) >= 1
    message = choices[0]["message"]
    assert isinstance(message["content"], str) and message["content"].strip()
    assert choices[0]["finish_reason"] in {"stop", "length"}
    usage = data["usage"]
    assert usage["prompt_tokens"] > 0
    assert usage["completion_tokens"] > 0
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


def test_arabic_content_roundtrip(client, model: str) -> None:
    """An integrated agent may be an Arabic legal agent — Arabic must work end to end."""
    data = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "مرحباً، ما هو القانون المدني؟ أجب بجملة واحدة."}],
        max_tokens=64,
        temperature=0.0,
        extra_body=_EXTRA,
    ).model_dump()
    content = data["choices"][0]["message"]["content"]
    assert content and content.strip()


def test_stream_yields_delta_chunks_low_ttft(client, model: str) -> None:
    t0 = time.monotonic()
    stream = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Count from one to five."}],
        max_tokens=64,
        temperature=0.0,
        stream=True,
        extra_body=_EXTRA,
    )
    chunks = list(stream)
    ttft = time.monotonic() - t0
    assert len(chunks) >= 2
    assert ttft < 10.0
    text = "".join(c.choices[0].delta.content or "" for c in chunks)
    assert text.strip()
    assert chunks[-1].choices[0].finish_reason in ("stop", "length")


def test_raw_sse_shape_and_done_sentinel(gateway_root: str, model: str) -> None:
    """Raw SSE framing: data: lines + [DONE] terminator (no SDK involved)."""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Say hi in one word."}],
        "max_tokens": 8,
        "temperature": 0.0,
        "stream": True,
        # Raw HTTP (no SDK): chat_template_kwargs is a top-level extra body field.
        **{"chat_template_kwargs": {"enable_thinking": False}},
    }
    with (
        httpx.Client(timeout=30.0) as c,
        c.stream("POST", f"{gateway_root}/v1/chat/completions", json=body) as resp,
    ):
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        lines = [ln for ln in resp.iter_lines() if ln]
    data_lines = [ln for ln in lines if ln.startswith("data: ")]
    assert data_lines, f"no SSE data lines: {lines[:3]!r}"
    assert data_lines[-1] == "data: [DONE]"
    payload = json.loads(data_lines[0][len("data: ") :])
    assert "choices" in payload and payload["model"] == model
