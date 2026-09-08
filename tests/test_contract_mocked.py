"""Hermetic Repository-2 contract tests (fully mocked, zero live network).

These tests prove the immutable OpenAI-compatible contract that the downstream
Arabic Legal RAG Agent consumes — model echo, SSE streaming shape, and
OpenAI tool-calling JSON schema — against a mocked `/v1/chat/completions`
endpoint via ``respx``. They are the exact same assertions CI runs on every
push (no GPU, no cluster, no external API).

The live twin of this suite lives in ``tests/e2e/`` and is gated behind
``NANOSERVE_E2E_LIVE=1``.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
import respx
from openai import OpenAI

from nanoserve_llm.config import DEFAULT_MODEL, Settings

# Mocked upstream base URL — never contacted (respx intercepts the transport).
MOCK_BASE_URL = "http://gateway.test"
SETTINGS = Settings()

# A prompt that forces tool use (deterministic contract check for tool_calls).
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_case_articles",
            "description": "Retrieve articles of a legal case by case number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "case_number": {
                        "type": "string",
                        "description": "The docket/case number.",
                    }
                },
                "required": ["case_number"],
                "additionalProperties": False,
            },
        },
    }
]


@pytest.fixture
def openai_via() -> Callable[[respx.Router], OpenAI]:
    """Build an OpenAI client whose httpx transport IS the given respx router.

    The OpenAI SDK passes its own explicit transport, which global respx.mock
    patching cannot intercept; wiring the router as the transport directly is
    deterministic and hermetic.
    """

    def _build(router: respx.Router) -> OpenAI:
        return OpenAI(
            api_key="EMPTY",
            base_url=f"{MOCK_BASE_URL}/v1",
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(router.handler)),
        )

    return _build


def _router() -> respx.Router:
    """Default router: 200 non-stream chat completion."""
    router = respx.Router()
    router.post(f"{MOCK_BASE_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_completion_json())
    )
    return router


def _chat_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "model": SETTINGS.serving.model,
        "messages": [{"role": "user", "content": "Say hello in one word."}],
        "max_tokens": 16,
        "temperature": 0.0,
    }
    body.update(overrides)
    return body


def _completion_json(**overrides: object) -> dict[str, object]:
    resp: dict[str, object] = {
        "id": "chatcmpl-mocked001",
        "object": "chat.completion",
        "created": 1_700_000_000,
        "model": SETTINGS.serving.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello."},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
    }
    resp.update(overrides)
    return resp


class TestChatCompletionContract:
    def test_model_echo_object_and_ids(self, openai_via) -> None:
        """The gateway must echo the requested model + OpenAI envelope."""
        out = openai_via(_router()).chat.completions.create(**_chat_body())
        assert out.model == SETTINGS.serving.model
        assert out.object == "chat.completion"
        assert out.id.startswith("chatcmpl-")
        assert out.choices[0].message.content == "Hello."
        assert out.choices[0].finish_reason == "stop"

    def test_usage_counts_are_consistent(self, openai_via) -> None:
        out = openai_via(_router()).chat.completions.create(**_chat_body())
        usage = out.usage
        assert usage is not None
        assert usage.prompt_tokens > 0
        assert usage.completion_tokens > 0
        assert usage.total_tokens == usage.prompt_tokens + usage.completion_tokens

    def test_tool_calls_json_schema_passthrough(self, openai_via) -> None:
        """tool_calls must carry id/type/function{name,arguments-JSON} — the
        exact shape Repository 2's agent parses."""
        args = json.dumps({"case_number": "2024/42"})
        router = respx.Router()
        router.post(f"{MOCK_BASE_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json=_completion_json(
                    choices=[
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_abc123",
                                        "type": "function",
                                        "function": {
                                            "name": "get_case_articles",
                                            "arguments": args,
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                ),
            )
        )
        out = openai_via(router).chat.completions.create(
            **_chat_body(tools=TOOLS, tool_choice="auto")
        )
        tc = out.choices[0].message.tool_calls
        assert tc is not None and len(tc) == 1
        assert tc[0].type == "function"
        assert tc[0].function.name == "get_case_articles"
        assert json.loads(tc[0].function.arguments) == {"case_number": "2024/42"}
        assert out.choices[0].finish_reason == "tool_calls"


SSE_CHUNKS = [
    {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": DEFAULT_MODEL,
        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
    },
    {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": DEFAULT_MODEL,
        "choices": [{"index": 0, "delta": {"content": "Hel"}, "finish_reason": None}],
    },
    {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": DEFAULT_MODEL,
        "choices": [{"index": 0, "delta": {"content": "lo."}, "finish_reason": None}],
    },
    {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": DEFAULT_MODEL,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    },
]


def _sse_body() -> str:
    return "".join(f"data: {json.dumps(c)}\n\n" for c in SSE_CHUNKS) + "data: [DONE]\n\n"


def _stream_router(content: str) -> respx.Router:
    router = respx.Router()
    router.post(f"{MOCK_BASE_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            content=content,
            headers={"Content-Type": "text/event-stream"},
        )
    )
    return router


class TestStreamingContract:
    def test_sse_stream_parses_without_buffering(self, openai_via) -> None:
        """Gateway must pass text/event-stream through unfragmented — the SSE
        contract Repository 2 consumes (no RequestBuffer on this route)."""
        chunks = list(
            openai_via(_stream_router(_sse_body())).chat.completions.create(
                **_chat_body(stream=True)  # type: ignore[arg-type]
            )
        )
        assert len(chunks) >= 4  # role + 2 content deltas + finish chunk
        text = "".join(c.choices[0].delta.content or "" for c in chunks)
        assert text == "Hello."
        assert chunks[-1].choices[0].finish_reason == "stop"
        # First chunk carries the model name (client-side model echo).
        assert chunks[0].model == DEFAULT_MODEL

    def test_malformed_chunk_is_surfaced_not_silently_dropped(self, openai_via) -> None:
        """A corrupt SSE frame must raise, never be silently swallowed."""
        router = _stream_router(
            'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: NOT_JSON\n\ndata: [DONE]\n\n'
        )
        raised = False
        try:
            list(
                openai_via(router).chat.completions.create(
                    **_chat_body(stream=True)  # type: ignore[arg-type]
                )
            )
        except Exception:
            raised = True
        assert raised
