"""Portable conformance suite for integrated agents.

This suite is the DECOUPLED definition of "the gateway contract holds". It
depends only on ``openai`` + ``httpx`` and plain environment variables — it
does NOT import this repository's package, settings, or manifests. A consumer
agent can run it against any deployment:

    export NANOSERVE_CONTRACT_LIVE=1
    export NANOSERVE_BASE_URL=http://localhost:8000/v1   # default
    export NANOSERVE_MODEL=ibm-granite/granite-4.2-3b    # default
    uv run --group dev pytest contract_tests -v

Gate: all tests here are skipped unless NANOSERVE_CONTRACT_LIVE=1 (so a plain
``pytest`` run in this repo — whose testpaths are ``tests`` + ``evals`` — never
touches a live endpoint).
"""

from __future__ import annotations

import os
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

_CONTRACT_LIVE = os.environ.get("NANOSERVE_CONTRACT_LIVE", "0") == "1"
BASE_URL = os.environ.get("NANOSERVE_BASE_URL", "http://localhost:8000/v1")
MODEL = os.environ.get("NANOSERVE_MODEL", "ibm-granite/granite-4.2-3b")
TIMEOUT = float(os.environ.get("NANOSERVE_REQUEST_TIMEOUT", "120"))
_D = Path(__file__).resolve().parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip everything in this directory unless the live gate is on."""
    if _CONTRACT_LIVE:
        return
    skip = pytest.mark.skip(
        reason="conformance suite requires NANOSERVE_CONTRACT_LIVE=1 and a live endpoint"
    )
    for item in items:
        try:
            here = Path(item.path).resolve() if item.path else None
        except AttributeError:
            here = None
        if here is not None and _D in here.parents:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL.rstrip("/")


@pytest.fixture(scope="session")
def model() -> str:
    return MODEL


@pytest.fixture(scope="session")
def gateway_root(base_url: str) -> str:
    """http://host:8000/v1 -> http://host:8000 (healthz lives at the root)."""
    stripped = base_url.rstrip("/")
    if stripped.endswith("/v1"):
        return stripped[: -len("/v1")]
    return stripped


@pytest.fixture(scope="session")
def client(base_url: str) -> Iterator[object]:
    from openai import OpenAI

    yield OpenAI(base_url=base_url, api_key="EMPTY", timeout=TIMEOUT, max_retries=2)


@pytest.fixture(scope="session")
def health(gateway_root: str) -> Callable[[], tuple[int, bytes]]:
    def _probe() -> tuple[int, bytes]:
        with urllib.request.urlopen(  # noqa: S310 — local HTTP contract probe
            f"{gateway_root}/healthz", timeout=5.0
        ) as resp:
            return resp.status, resp.read()

    return _probe
