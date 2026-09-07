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


class ConversationalUser(HttpUser):
    """A virtual user holding a multi-turn conversation.

    Each user keeps a PRIVATE message list that grows on every turn: a long
    context document whose HEAD IS UNIQUE PER USER (random incident id, region,
    service — so different users share no prefix, forcing real prefill + KV
    churn) followed by user prompt -> assistant reply -> ... The context grows
    per turn toward ~1,900 tokens. Within a user's own session the head is
    stable, so only within-user prefix hits remain (turn 2+ reuse turn 1).
    ``turns`` is the number of user turns per session (env
    ``NANOSERVE_LOAD_TURNS``).

    A separate RateLimitedUser subclass (same long context, plus an
    ``x-user-id`` header) trips the per-user gateway 429 limit.
    """

    wait_time = between(SETTINGS.load_test.think_min, SETTINGS.load_test.think_max)

    # Per-user random identity injected into the context head so each user's
    # document prefix is unique (no cross-user prefix-cache reuse).
    _IDS = ("ORD", "PAY", "CHK", "RFN", "AUTH", "WLT", "CRD", "SBX")
    _REGS = ("us-east-1", "us-west-2", "eu-west-1", "ap-south-1", "eu-central-1")
    _SVCS = ("api-gateway", "checkout-svc", "auth-service", "wallet-api", "search-svc")

    # Subclasses can add per-request headers (e.g. x-user-id) and rename the
    # Locust task label.
    _extra_headers: dict[str, str] = {}
    _request_name = "/v1/chat/completions (multi-turn)"
    weight = 34

    def on_start(self) -> None:
        import random

        self._incident = f"{random.choice(self._IDS)}-{random.randint(1000, 9999)}"
        self._region = random.choice(self._REGS)
        self._service = random.choice(self._SVCS)
        self._messages = [self._system_message()]
        self._turn = 0

    def _system_message(self) -> dict[str, str]:
        """System message with THIS user's unique long context embedded."""
        return {
            "role": "system",
            "content": (f"{SETTINGS.load_test.system_prompt}\n\n{self._build_context()}"),
        }

    def _build_context(self) -> str:
        """Long incident report unique to this user (random id/region/service)."""
        i = self._incident
        r = self._region
        s = self._service
        return (
            f"# Incident report — {i} ({s} latency spike, {r})\n"
            "\n"
            "## Summary\n"
            f"Between 02:11 and 03:47 UTC on 2026-09-04, the public {s} in "
            f"region {r} served elevated p99 latencies (baseline 40 ms; peak "
            "2.1 s) for /v1/orders and /v1/payments. Error rate peaked at 4.2% "
            "(baseline <0.1%). A rollback of the auth-service canary at 03:52 UTC "
            "returned latencies to baseline by 04:05 UTC. No data loss occurred. "
            "\n"
            "## Timeline\n"
            "02:11 UTC — Datadog alert 'p99 latency > 1s' fired for "
            f"{s}.\n"
            "02:14 UTC — on-call paged; triage began; dashboards showed a slow "
            "upstream (auth-service) rather than the gateway itself.\n"
            "02:22 UTC — auth-service canary (v2.4.1-rc3) had been promoted to 50% "
            "of traffic at 01:58 UTC; its p99 was 4.3 s.\n"
            "02:40 UTC — suspected a connection-pool leak in the canary; heap dumps "
            "captured; canary traffic reduced to 10%.\n"
            "03:05 UTC — p99 improved to 900 ms but did not normalize; DB CPU on the "
            "auth read-replica rose to 78%.\n"
            "03:22 UTC — discovered the canary issued one extra token refresh per "
            "request when a new JWT claim was present; this doubled DB round-trips.\n"
            "03:47 UTC — canary fully disabled; traffic back to v2.4.0.\n"
            "03:52 UTC — full rollback of the canary completed.\n"
            "04:05 UTC — p99 back to 40 ms; error rate <0.1%.\n"
            "\n"
            "## Root cause\n"
            "Auth-service v2.4.1-rc3 introduced a token-refresh code path guarded by "
            "a new JWT claim (iss=sts). When present, every authenticated request "
            "triggered an extra synchronous call to the refresh-token table. Under "
            "the gateway's read-through cache the extra query was hidden at low "
            "traffic, but at 50% canary the additional DB load saturated the "
            "read-replica connection pool, cascading into the gateway's upstream "
            "timeouts and retries (which amplified load).\n"
            "\n"
            "## Evidence\n"
            "- p99 timeline matches canary traffic-share changes (correlation 0.93).\n"
            "- Heap dump shows 12k leaked OkHttp pools in the canary process.\n"
            "- Read-replica slow-query log shows the extra token-refresh SELECTs.\n"
            "- Replaying 5% production traffic against v2.4.1-rc3 in staging "
            "reproduced the p99 regression (1.8 s).\n"
            "\n"
            "## Resolution and follow-ups\n"
            "1. Reverted to v2.4.0; canary gated behind a feature flag (off by "
            "default) until the refresh path is reworked.\n"
            "2. Added a load test that exercises the new JWT claim at 50% traffic.\n"
            "3. Added an SLO alert on auth-service upstream p99 (warning at 300 ms).\n"
            "4. Postmortem scheduled; owner: platform-auth.\n"
        )

    # Realistic follow-ups a human would ask about the report. Each appends to
    # the private history, so per-user contexts diverge after the shared head.
    _TURN_PROMPTS = (
        "Summarize the root cause in two sentences and list the three main contributing factors.",
        "What was the single most suspicious change, and what evidence links it "
        "to the latency spike?",
        "Draft the rollback and communication steps an on-call engineer should "
        "take, in priority order.",
        "Which monitoring or alerting gaps made this incident worse, and what "
        "would you add to catch it earlier?",
        "Rewrite the timeline as a short postmortem email to engineering, "
        "tone neutral and factual.",
        "List the concrete follow-up action items with owners and rough "
        "deadlines, based only on the report.",
    )

    def _conversation_request(self) -> dict:
        """Next turn's body: carry the FULL growing history."""
        self._turn += 1
        idx = (self._turn - 1) % len(self._TURN_PROMPTS)
        self._messages.append({"role": "user", "content": self._TURN_PROMPTS[idx]})
        return {
            "model": SETTINGS.serving.model,
            "messages": list(self._messages),  # full context, growing
            "max_tokens": SETTINGS.load_test.max_tokens,
            "temperature": 0.7,
        }

    def _turn_overflow(self) -> bool:
        """Coarse token estimate (EN ~4 chars/token) vs vLLM max_model_len 2048.

        Sends a turn only while the FULL conversation fits; when it would
        exceed ~1900 tokens the session resets instead (guards against 400
        'maximum context length' errors).
        """
        total_chars = sum(len(m["content"]) for m in self._messages)
        estimated = total_chars // 4
        return estimated >= 1900

    @task
    def conversation_turn(self) -> None:
        """Send one turn; append the assistant reply to this user's history.

        When the session reaches ``turns`` turns — or the conversation would
        exceed the model's context window — start a fresh session (clear
        history), like a user opening a new chat. Keeps sustained load while
        staying under vLLM's ``max_model_len`` (2048).
        """
        if self._turn >= SETTINGS.load_test.turns or self._turn_overflow():
            self._messages = [self._system_message()]
            self._turn = 0
        body = self._conversation_request()
        headers = {**HEADERS, **self._extra_headers}
        with self.client.post(
            "/v1/chat/completions",
            json=body,
            headers=headers,
            name=self._request_name,
            catch_response=True,
        ) as response:
            if response.status_code >= 400:
                response.failure(f"HTTP {response.status_code}: {response.text[:200]!r}")
                return
            try:
                data = response.json()
                reply = data["choices"][0]["message"]["content"]
                assert reply
                # Append the assistant reply so the next turn's context grows.
                self._messages.append({"role": "assistant", "content": reply})
            except (ValueError, KeyError, IndexError, AssertionError) as exc:
                response.failure(f"Malformed response: {exc!r}")


class RateLimitedUser(ConversationalUser):
    """Same multi-turn workload, but every request carries a per-user x-user-id
    so the gateway's per-user rate limit (20 req/min) trips -> HTTP 429.

    Only a handful of these should run alongside many plain ConversationalUsers:
    the 429s are expected and visible on the dashboard rejection panel.
    """

    _request_name = "/v1/chat/completions (rate-limited 429)"
    weight = 3

    def on_start(self) -> None:
        import random

        # A small pool of ids so a handful of users share a bucket and trip it
        # faster (20 req/min each).
        self._user_id = f"load-user-{random.randint(1, 5)}"
        self._extra_headers = {"x-user-id": self._user_id}
        super().on_start()
