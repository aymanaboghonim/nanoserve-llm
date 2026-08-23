"""Locust load-test suite for the vLLM OpenAI-compatible endpoint (Phase 5).

Runs against the served model's ``/v1/chat/completions`` endpoint. All knobs are
env-driven via the NanoServe settings (``NANOSERVE_LOAD_*``, ``NANOSERVE_*``);
defaults are deliberately conservative for a ~1.5B AWQ model on a 4GB KV budget.

Run interactively (web UI on http://localhost:8089)::

    uv run --group load locust -f load_tests/locustfile.py

Run headless::

    uv run --group load locust -f load_tests/locustfile.py \
        --host http://localhost:8000 \
        --headless -u 8 -r 1 -t 1m --csv .locust/run

Requires a live endpoint (Phase 3+). Without one, Locust reports
``ConnectionError`` on every request — expected until the serving plane exists.
"""

from __future__ import annotations

import logging

from locust import HttpUser, between, task

from nanoserve_llm.config import get_settings
from nanoserve_llm.logging import get_logger

logger: logging.Logger = get_logger(__name__)

# Read settings once at import time so Locust workers share identical defaults.
SETTINGS = get_settings()

# Headers for the OpenAI-compatible chat completions API.
HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {SETTINGS.serving.api_key}",
}

# Static payload template — every user sends the same short prompt with a small
# max_tokens so a 4GB KV cache is not exhausted by long generations.
REQUEST_BODY = {
    "model": SETTINGS.serving.model,
    "messages": [{"role": "user", "content": SETTINGS.load_test.prompt}],
    "max_tokens": SETTINGS.load_test.max_tokens,
    "temperature": 0.7,
}


class ChatCompletionsUser(HttpUser):
    """A virtual user hammering the chat completions endpoint."""

    wait_time = between(1.0, 3.0)  # think-time between requests

    @task
    def chat_completions(self) -> None:
        """POST /v1/chat/completions and record the response latency."""
        with self.client.post(
            "/v1/chat/completions",
            json=REQUEST_BODY,
            headers=HEADERS,
            name="/v1/chat/completions",
            catch_response=True,
        ) as response:
            if response.status_code >= 400:
                response.failure(f"HTTP {response.status_code}: {response.text[:200]!r}")
                return
            try:
                data = response.json()
                assert data["choices"][0]["message"]["content"]
            except (ValueError, KeyError, IndexError, AssertionError) as exc:
                response.failure(f"Malformed response: {exc!r}")

    def on_start(self) -> None:
        """Log the target endpoint once per user spawn (first user only)."""
        if self.environment.runner and self.environment.runner.user_classes:
            logger.info(
                "locust user %s targeting %s model=%s",
                id(self),
                self.host,
                SETTINGS.serving.model,
            )
