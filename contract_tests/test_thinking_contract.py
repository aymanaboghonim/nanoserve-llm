"""Thinking (Granite 4.2 reasoning) contract.

Thinking is request-controlled via chat_template_kwargs.enable_thinking. The
template DEFAULTS to on, so the suite pins both modes explicitly. With
thinking off, content-only answers are guaranteed; with thinking on, a
reasoning trace must be present (message.reasoning) or, at minimum, the
completion must still produce output within budget.
"""

from __future__ import annotations

_PROMPT = (  # noqa: E501 - long reasoning fixture
    "A bat and a ball cost $1.10 in total. The bat costs $1.00 more than the ball. "
    "How much does the ball cost? Reason step by step."
)


def _completion(client, model: str, enable_thinking: bool, max_tokens: int = 256):
    return client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": _PROMPT}],
        max_tokens=max_tokens,
        temperature=0.0,
        extra_body={"chat_template_kwargs": {"enable_thinking": enable_thinking}},
    )


def test_thinking_off_returns_content_only(client, model: str) -> None:
    msg = _completion(client, model, enable_thinking=False).choices[0].message
    assert msg.content and msg.content.strip()
    reasoning = getattr(msg, "reasoning", None) or getattr(msg, "reasoning_content", None)
    assert not reasoning


def test_thinking_on_returns_reasoning_trace(client, model: str) -> None:
    msg = _completion(client, model, enable_thinking=True, max_tokens=512).choices[0].message
    reasoning = getattr(msg, "reasoning", None) or getattr(msg, "reasoning_content", None)
    assert reasoning, f"expected a reasoning trace, got content={msg.content!r}"
    # With enough budget the final content must also arrive.
    assert msg.content and msg.content.strip()
