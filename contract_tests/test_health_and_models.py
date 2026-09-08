"""Health + model-discovery contract checks."""

from __future__ import annotations


def test_healthz_returns_200(health) -> None:
    status, _ = health()
    assert status == 200


def test_healthz_is_fast(health) -> None:
    """Probe answers in well under the timeout budget (no buffering)."""
    import time

    t0 = time.monotonic()
    status, _ = health()
    assert status == 200
    assert time.monotonic() - t0 < 5.0


def test_models_lists_the_served_model(client, model: str) -> None:
    ids = [m.id for m in client.models.list().data]
    assert model in ids
