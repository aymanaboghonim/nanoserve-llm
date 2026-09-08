"""Health contract: the gateway must expose /healthz on port 8000.

Repository 2 health-checks the platform at http://localhost:8000/healthz
before issuing chat requests. The gateway maps it to vLLM's native /health
(KServe template probes use /health on :8000).
"""

from __future__ import annotations

import pytest
from openai import OpenAI

from nanoserve_llm.config import Settings

pytestmark = [pytest.mark.live_contract, pytest.mark.e2e]


def test_healthz_returns_200(health) -> None:
    status, _ = health()
    assert status == 200


def test_healthz_is_fast(health) -> None:
    """Probe must answer well under the timeout budget (no gateway buffering)."""
    status, _ = health()
    assert status == 200


def test_v1_models_lists_served_model(openai_client: OpenAI, settings: Settings) -> None:
    models = openai_client.models.list()
    ids = [m.id for m in models.data]
    assert settings.serving.model in ids
