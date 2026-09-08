"""Live Repository-2 contract suites (tests/e2e).

Gating: every test here is skipped at collection time unless
``NANOSERVE_E2E_LIVE=1`` (see ``config.E2E``). A developer running plain
``pytest`` must NEVER hang against a cold cluster — hence the skip message:

    E2E tests require NANOSERVE_E2E_LIVE=1 and a running local K3s cluster

Run with:  NANOSERVE_E2E_LIVE=1 pytest tests/e2e -v   (or `make test-e2e`)
"""

from __future__ import annotations

import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from openai import OpenAI

from nanoserve_llm.config import Settings

pytestmark = [pytest.mark.live_contract, pytest.mark.e2e]

SKIP_REASON = "E2E tests require NANOSERVE_E2E_LIVE=1 and a running local K3s cluster"
_E2E_DIR = Path(__file__).resolve().parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Gate this directory behind NANOSERVE_E2E_LIVE at collection time.

    Note: the hook receives ALL collected items in the run, so only items
    physically under tests/e2e are skipped.
    """
    if Settings().e2e.live:
        return
    skip = pytest.mark.skip(reason=SKIP_REASON)
    for item in items:
        try:
            here = Path(item.path).resolve() if item.path else None
        except AttributeError:
            here = None
        if here is not None and _E2E_DIR in here.parents:
            item.add_marker(skip)


def _gateway_root(base_url: str) -> str:
    """http://localhost:8000/v1 -> http://localhost:8000"""
    stripped = base_url.rstrip("/")
    if stripped.endswith("/v1"):
        return stripped[: -len("/v1")]
    return stripped


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="session")
def base_url(settings: Settings) -> str:
    # Repository-2 immutable contract: http://localhost:8000/v1 by default,
    # overridable via NANOSERVE_BASE_URL.
    return settings.serving.base_url


@pytest.fixture(scope="session")
def gateway_root(base_url: str) -> str:
    return _gateway_root(base_url)


@pytest.fixture(scope="session")
def openai_client(base_url: str, settings: Settings) -> Iterator[OpenAI]:
    yield OpenAI(
        api_key=settings.serving.api_key,
        base_url=base_url,
        timeout=settings.serving.request_timeout_s,
        max_retries=settings.serving.max_retries,
    )


@pytest.fixture(scope="session")
def health(gateway_root: str, settings: Settings) -> Callable[[], tuple[int, bytes]]:
    """Probe {gateway}/healthz with a hard timeout (stdlib, no extra deps)."""

    def _probe() -> tuple[int, bytes]:
        with urllib.request.urlopen(  # noqa: S310 — local HTTP contract probe
            f"{gateway_root}/healthz", timeout=settings.e2e.health_timeout_s
        ) as resp:
            return resp.status, resp.read()

    return _probe
