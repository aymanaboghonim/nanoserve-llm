"""Shared fixtures and collection gating for the evaluation suite.

Two hard requirements drive this module:

1. **Zero-cost in the base environment.** ``uv run pytest`` (dev group only) must
   collect this directory without importing deepeval/openai — those live in the
   ``eval`` group. All heavy imports happen lazily inside fixtures.

2. **Live-endpoint gating.** Every test here is ``e2e``-marked and skipped unless
   ``NANOSERVE_EVAL_LIVE=1`` (a live vLLM endpoint must exist, Phase 3+).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from nanoserve_llm.config import Settings, get_settings

try:  # eval group only — imported lazily where used, but surfaced for fixtures
    from openai import OpenAI

    HAS_OPENAI = True
except ImportError:  # pragma: no cover - exercised in base env
    HAS_OPENAI = False

try:  # eval group only
    from deepeval.models import OpenAIModel

    HAS_DEEPEVAL = True
except ImportError:  # pragma: no cover - exercised in base env
    HAS_DEEPEVAL = False

SETTINGS = get_settings()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip all ``e2e``-marked tests unless a live endpoint is enabled."""
    if SETTINGS.eval.live:
        return
    skip = pytest.mark.skip(
        reason="requires a live endpoint; set NANOSERVE_EVAL_LIVE=1 (see evals/README.md)"
    )
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def settings() -> Settings:
    """The NanoServe settings (hardware fencing + endpoint config)."""
    return SETTINGS


@pytest.fixture(scope="session")
def openai_client(settings: Settings) -> Iterator[OpenAI]:
    """OpenAI-compatible client pointed at the served vLLM endpoint."""
    if not SETTINGS.eval.live:
        pytest.skip("openai client requires a live endpoint")
    if not HAS_OPENAI:
        pytest.skip("openai not installed; run: uv sync --group eval")
    from openai import OpenAI

    yield OpenAI(
        base_url=settings.serving.base_url,
        api_key=settings.serving.api_key,
        timeout=settings.serving.request_timeout_s,
        max_retries=settings.serving.max_retries,
    )


@pytest.fixture(scope="session")
def judge_model(settings: Settings) -> Iterator[OpenAIModel]:
    """DeepEval judge LLM wired to the served endpoint (self-judging)."""
    if not SETTINGS.eval.live:
        pytest.skip("judge model requires a live endpoint")
    if not HAS_DEEPEVAL:
        pytest.skip("deepeval not installed; run: uv sync --group eval")
    from deepeval.models import OpenAIModel

    yield OpenAIModel(
        model=settings.eval.judge_model,
        api_key=settings.serving.api_key,
        base_url=settings.serving.base_url,
    )
