"""Tool-calling contract: OpenAI JSON-schema tool output through the gateway.

Repository 2's LangGraph agent binds tools (e.g. legal-article retrieval).
vLLM runs --enable-auto-tool-choice + --tool-call-parser qwen3_coder; the
gateway must pass the tool_calls envelope through untouched.
"""

from __future__ import annotations

import json

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

PROMPT = "Use the get_case_articles tool with case_number 2024/42. Reply only with the tool call, nothing else."

_EXTRA = {"chat_template_kwargs": {"enable_thinking": False}}


def test_tool_call_returns_openai_schema(client, model: str) -> None:
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": PROMPT}],
        tools=TOOLS,
        tool_choice="auto",
        max_tokens=128,
        temperature=0.0,
        extra_body=_EXTRA,
    )
    message = resp.choices[0].message
    assert message.tool_calls, f"no tool_calls in response: {message.content!r}"
    tc = message.tool_calls[0]
    assert tc.type == "function"
    assert tc.function.name == "get_case_articles"
    args = json.loads(tc.function.arguments)  # arguments is a JSON string
    assert "case_number" in args


def test_tool_roundtrip_with_result(client, model: str) -> None:
    """Full LangGraph-style round trip: call -> tool result -> final answer."""
    first = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": PROMPT}],
        tools=TOOLS,
        tool_choice="auto",
        max_tokens=128,
        temperature=0.0,
        extra_body=_EXTRA,
    )
    tc = first.choices[0].message.tool_calls[0]
    second = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "user", "content": PROMPT},
            first.choices[0].message,  # assistant turn carries the tool_calls
            {"role": "tool", "tool_call_id": tc.id, "content": '{"articles": [1, 2, 3]}'},
        ],
        tools=TOOLS,
        max_tokens=128,
        temperature=0.0,
        extra_body=_EXTRA,
    )
    content = second.choices[0].message.content
    assert content and content.strip()
