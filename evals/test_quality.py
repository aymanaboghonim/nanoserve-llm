"""LLM-as-judge quality tests using DeepEval's G-Eval metric.

The served model acts as both the system under test and the judge —
zero torch, zero external APIs, fully local (matches the Blueprint's
"programmatic evaluation suite ... assert correctness, structural integrity,
and latency thresholds").
"""

from __future__ import annotations

import pytest

from nanoserve_llm.config import Settings

pytestmark = pytest.mark.e2e

CRITERIA = (
    "Determine whether the answer is clear, factually correct, and directly "
    "addresses the user's question. Penalize vague, off-topic, or incorrect answers."
)


def _completion(client, settings: Settings, prompt: str) -> str:
    resp = client.chat.completions.create(
        model=settings.serving.model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=settings.load_test.max_tokens,
        temperature=0.0,
        # Granite-4.2 defaults thinking ON; disable it so judged answers are
        # the model's actual content (not an empty body after a think trace).
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    content = resp.choices[0].message.content
    assert content, "endpoint returned an empty completion"
    return content


def _g_eval_score(prompt: str, answer: str, judge_model, settings: Settings) -> float:
    """Run G-Eval with the served LLM as judge; return the raw score."""
    from deepeval.metrics import GEval
    from deepeval.metrics.g_eval.utils import SingleTurnParams
    from deepeval.test_case import LLMTestCase

    # deepeval >= 4.1 requires `evaluation_params` (the test-case fields the
    # judge should consider); `criteria`/`evaluation_steps` stay optional.
    metric = GEval(
        name="Quality",
        evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT],
        criteria=CRITERIA,
        evaluation_steps=[
            "Assess whether the answer is clear, well-structured, and easy to understand.",
            "Assess whether the answer is factually correct and directly addresses the question.",
            "Penalize vague, off-topic, or incorrect answers.",
        ],
        model=judge_model,
        threshold=settings.eval.threshold,
        async_mode=False,
    )
    metric.measure(LLMTestCase(input=prompt, actual_output=answer))
    return metric.score


class TestGEvalQuality:
    @pytest.mark.parametrize(
        ("prompt", "expect_relevant"),
        [
            ("Explain what a KV cache is in one sentence.", True),
            ("What is the capital of France?", True),
        ],
    )
    def test_quality_threshold(
        self, openai_client, judge_model, settings: Settings, prompt: str, expect_relevant: bool
    ) -> None:
        answer = _completion(openai_client, settings, prompt)
        score = _g_eval_score(prompt, answer, judge_model, settings)
        assert 0.0 <= score <= 1.0
        assert score >= settings.eval.threshold, (
            f"G-Eval score {score:.2f} below threshold {settings.eval.threshold}: {answer!r}"
        )

    def test_deterministic_answers_stable(
        self, openai_client, judge_model, settings: Settings
    ) -> None:
        """Same question twice (temperature 0) should produce close scores."""
        prompt = "Define 'time-to-first-token' in one sentence."
        first = _completion(openai_client, settings, prompt)
        second = _completion(openai_client, settings, prompt)
        assert (
            abs(
                _g_eval_score(prompt, first, judge_model, settings)
                - _g_eval_score(prompt, second, judge_model, settings)
            )
            < 0.35
        )
