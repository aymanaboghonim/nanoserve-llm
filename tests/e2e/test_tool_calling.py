"""Tool-calling contract: OpenAI JSON-schema tool output through the gateway.

Repository 2's agent relies on deterministic function-calling. vLLM runs with
--enable-auto-tool-choice + --tool-call-parser hermes (this runtime build has
no native qwen25 parser); the gateway must pass the tool_calls envelope
through untouched.
"""

from __future__ import annotations

import json

import pytest
from openai import OpenAI

from nanoserve_llm.config import Settings

pytestmark = [pytest.mark.live_contract, pytest.mark.e2e]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_case_articles",
            "description": (
                "Retrieve the articles of a legal case by case number. "
                "ALWAYS call this tool when asked about a case."
            ),
            "parameters": {
                "type": "object",
                "properties": {"case_number": {"type": "string", "description": "Docket number."}},
                "required": ["case_number"],
                "additionalProperties": False,
            },
        },
    }
]

PROMPT = (
    "Use the get_case_articles tool with case_number 2024/42. "
    "Reply only with the tool call, nothing else."
)


def test_tool_call_returns_openai_schema(openai_client: OpenAI, settings: Settings) -> None:
    resp = openai_client.chat.completions.create(
        model=settings.serving.model,
        messages=[{"role": "user", "content": PROMPT}],
        tools=TOOLS,
        tool_choice="auto",
        max_tokens=settings.e2e.tool_tokens,
        temperature=0.0,
    )
    message = resp.choices[0].message
    assert message.tool_calls, f"no tool_calls in response: {message.content!r}"
    tc = message.tool_calls[0]
    assert tc.type == "function"
    assert tc.function.name == "get_case_articles"
    # arguments must be valid JSON (Repository 2 parses them directly)
    args = json.loads(tc.function.arguments)
    assert "case_number" in args
