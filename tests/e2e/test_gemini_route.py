"""Tier-2 route contract: Gemini via x-ai-eg-model header routing.

Requires the Tier-2 Secret applied (`make secret` from gitignored .env with
GEMINI_API_KEY) — otherwise the gateway forwards no Authorization header and
Google answers 401. Header model selection: `x-ai-eg-model: gemini-3.5-flash-lite`.
"""

from __future__ import annotations

import pytest
from openai import OpenAI

from nanoserve_llm.config import Settings

pytestmark = [pytest.mark.live_contract, pytest.mark.e2e]

TIER2_MODEL = "gemini-3.5-flash-lite"


def test_gemini_route_returns_200(openai_client: OpenAI, settings: Settings) -> None:
    resp = openai_client.chat.completions.create(
        model=TIER2_MODEL,
        messages=[{"role": "user", "content": "Say OK"}],
        max_tokens=8,
        extra_headers={"x-ai-eg-model": TIER2_MODEL},
    )
    assert resp.model == TIER2_MODEL
    assert resp.choices[0].message.content


def test_local_route_still_serves_without_cloud_header(
    openai_client: OpenAI, settings: Settings
) -> None:
    """No x-ai-eg-model header -> default rule -> local Tier-1 granite model."""
    resp = openai_client.chat.completions.create(
        model=settings.serving.model,
        messages=[{"role": "user", "content": "ping"}],
        max_tokens=8,
    )
    assert resp.model == settings.serving.model
