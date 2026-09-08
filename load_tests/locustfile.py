"""Locust load-test suite for the vLLM OpenAI-compatible endpoint (Phase 5).

Runs against the served model's ``/v1/chat/completions`` endpoint. All knobs are
env-driven via the NanoServe settings (``NANOSERVE_LOAD_*``, ``NANOSERVE_*``);
defaults are sized for the 3B Granite AWQ model on a ~6.4GB VRAM budget (0.80 x
8GB; fp8 KV pool ~97K tokens at 32K context).

Clients use **FastHttpUser** (geventhttpclient) so per-request overhead stays
negligible and Locust itself never becomes the bottleneck at high concurrency.
Conversations are **stateful single tasks with a monotonic turn counter**: a
user opens a long unique context and sends ordered follow-ups back-to-back
(short think time), like a RAG agent resolving one query — dense enough to
actually fill the KV pool and form a scheduler queue (a 2-5 s think time left
the pool near 0% with no queueing).

Run interactively (web UI on http://localhost:8089)::

    uv run --group load locust -f load_tests/locustfile.py

Run headless::

    uv run --group load locust -f load_tests/locustfile.py \
        --host http://localhost:8000 \
        --headless -u 8 -r 1 -t 1m --csv .locust/run

Stress run with a ramp -> plateau -> end-of-window spike (load shape)::

    NANOSERVE_LOAD_SHAPE=1 uv run --group load locust -f load_tests/locustfile.py \
        --host http://localhost:8000 --headless --csv .locust/stress
    # or: make stress

Requires a live endpoint (Phase 3+). Without one, Locust reports
``ConnectionError`` on every request — expected until the serving plane exists.
"""

from __future__ import annotations

import logging

from locust import FastHttpUser, between, task

from nanoserve_llm.config import get_settings
from nanoserve_llm.logging import get_logger

logger: logging.Logger = get_logger(__name__)

# Read settings once at import time so Locust workers share identical defaults.
SETTINGS = get_settings()

# Traffic mix by user-class weight:
#   - ChatCompletionsUser (6):  short one-shot questions.
#   - ConversationalUser (40):  RAG-style ordered multi-turn sessions.
#   - RateLimitedUser (3):      same sessions + per-user x-user-id header
#                               (trips the gateway 20 req/min limit -> 429s,
#                               recorded as expected, not failures).

# Headers for the OpenAI-compatible chat completions API.
HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {SETTINGS.serving.api_key}",
}

# Granite-4.2 thinking: the chat template DEFAULTS to enable_thinking=true, so
# omitting the kwargs still lets the model burn the max_tokens budget on a
# <think> trace and return empty content. ALWAYS send the explicit override:
#   false (default) -> thinking off, content-only replies (throughput tests)
#   true            -> reasoning path (NANOSERVE_LOAD_THINKING=1; also raise
#                      max_tokens to ~256+ so content survives the trace)
_THINKING = SETTINGS.load_test.thinking
THINKING_KWARGS = {"chat_template_kwargs": {"enable_thinking": _THINKING}}


def _base_body(messages: list[dict[str, str]]) -> dict:
    """Shared completion body: model, full message history, capped output."""
    return {
        "model": SETTINGS.serving.model,
        "messages": messages,
        "max_tokens": SETTINGS.load_test.max_tokens,
        "temperature": 0.7,
        **THINKING_KWARGS,
    }


def _check(response, extra_name: str = "") -> bool:
    """Classify one completion response; returns True on success.

    - 429            -> expected (per-user rate-limit lever): recorded, not a
                        failure (keeps the default run's exit code clean while
                        the dashboard rejection panel still shows the 429s).
    - >= 400         -> real failure (4xx contract / 5xx overload).
    - malformed body -> failure.
    - otherwise      -> success; asserts content (or reasoning when thinking
                        is enabled) so empty short generations count as errors.
    """
    if response.status_code == 429:
        response.success()
        return True
    if response.status_code >= 400:
        response.failure(f"HTTP {response.status_code}: {response.text[:200]!r}")
        return False
    try:
        message = response.json()["choices"][0]["message"]
        content = message.get("content") or ""
        reasoning = message.get("reasoning") or ""
        ok = bool(content) if not _THINKING else bool(content or reasoning)
        if not ok:
            response.failure("Malformed response: empty content/reasoning")
            return False
        return True
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        response.failure(f"Malformed response: {exc!r}")
        return False


class ChatCompletionsUser(FastHttpUser):
    """A virtual user firing short one-shot questions (stateless)."""

    wait_time = between(SETTINGS.load_test.think_min, SETTINGS.load_test.think_max)
    weight = 6

    @task
    def chat_completions(self) -> None:
        """POST /v1/chat/completions and record the response latency."""
        body = _base_body([{"role": "user", "content": SETTINGS.load_test.prompt}])
        with self.client.post(
            "/v1/chat/completions",
            json=body,
            headers=HEADERS,
            name="/v1/chat/completions",
            catch_response=True,
        ) as response:
            _check(response)

    def on_start(self) -> None:
        """Log the target endpoint once per user spawn (first user only)."""
        if self.environment.runner and self.environment.runner.user_classes:
            logger.info(
                "locust user %s targeting %s model=%s",
                id(self),
                self.host,
                SETTINGS.serving.model,
            )


class ConversationalUser(FastHttpUser):
    """A RAG-style agent user: one long unique context, ordered follow-ups.

    Runs a private, ordered conversation — turn 1, turn 2, … — as a single
    stateful task: every time the task runs, ``_turn`` advances one step
    (Locust 2.46 has no ``seq_task``; a monotonic per-user turn counter inside
    one task is the robust way to guarantee order while users interleave).
    Each user keeps a PRIVATE message list: a long context document whose HEAD
    IS UNIQUE PER USER (random incident id, region, service — so different
    users share no prefix, forcing real prefill + KV churn), then user prompt
    -> assistant reply -> ... When the session reaches ``turns`` turns — or
    the conversation would exceed the context window — a fresh session starts
    (new chat), keeping sustained load while staying under vLLM's
    ``max_model_len`` (32768).

    RateLimitedUser subclasses this (same workload + per-user ``x-user-id``)
    to trip the gateway's per-user 429 limit.
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
    weight = 40

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
        """Long incident report unique to this user (random id/region/service).

        Padded with deterministic legal boilerplate up to ``context_tokens`` so
        each concurrent user holds thousands of KV entries (filling the fp8 KV
        pool) instead of ~450-token documents that leave it near 0%.
        """
        i = self._incident
        r = self._region
        s = self._service
        report = (
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
        # Pad with deterministic legal boilerplate up to the context target so
        # each user's document is thousands of tokens long (RAG-agent style).
        target_chars = SETTINGS.load_test.context_tokens * 4
        if len(report) < target_chars:
            padding = (
                "\n## Addendum — service terms and data-handling annex\n"
                "1. Scope. This annex governs the processing of personally "
                "identifiable information (PII) and incident telemetry collected "
                f"by the {s} platform in region {r} during the reporting window.\n"
                "2. Retention. Raw event logs are retained for ninety (90) days; "
                "derived metrics for thirteen (13) months. Aggregates used for "
                "capacity planning are kept for the life of the service.\n"
                "3. Access. Only on-call engineers with the incident-response "
                "role may query raw traces. All access is audited and reviewed "
                "weekly by the security team.\n"
                "4. Escalation. A suspected data breach must be reported to the "
                "privacy office within four (4) hours of confirmation, with an "
                "initial impact assessment covering the number of records "
                "affected, the classes of data exposed, and the mitigation plan.\n"
                "5. Deletion. On request, all PII belonging to a data subject is "
                "deleted from primary and derived stores within thirty (30) days, "
                "unless legal hold applies. Deletion jobs are idempotent and "
                "logged to the compliance ledger.\n"
                "6. Subprocessors. No third-party subprocessor receives raw "
                "incident data. Analytics partners receive only aggregates above "
                "a minimum cell size of ten (10) records.\n"
            )
            while len(report) < target_chars:
                report += padding
        return report

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

    def _turn_overflow(self) -> bool:
        """Coarse token estimate (EN ~4 chars/token) vs vLLM max_model_len 32768.

        Sends a turn only while the FULL conversation fits; when it would
        exceed ~8000 tokens the session resets instead (guards against 400
        'maximum context length' errors; 8K of context headroom keeps prefill
        cost realistic for the 32K window without hammering the 8GB card).
        """
        total_chars = sum(len(m["content"]) for m in self._messages)
        estimated = total_chars // 4
        return estimated >= 8000

    def _reset_session(self) -> None:
        """Start a fresh chat (same unique identity; new system context)."""
        self._messages = [self._system_message()]
        self._turn = 0

    def _maybe_reset(self) -> None:
        """Rotate to a fresh session once this one is used up (or too long)."""
        if self._turn >= SETTINGS.load_test.turns or self._turn_overflow():
            self._reset_session()

    def _send_turn(self) -> None:
        """Append the next prompt, POST the full history, keep the reply.

        ``_turn`` advances only on success so a transient failure does not
        silently shorten sessions; the next task execution retries the same
        turn with the same context.
        """
        self._maybe_reset()
        idx = self._turn % len(self._TURN_PROMPTS)
        self._messages.append({"role": "user", "content": self._TURN_PROMPTS[idx]})
        body = _base_body(list(self._messages))
        headers = {**HEADERS, **self._extra_headers}
        with self.client.post(
            "/v1/chat/completions",
            json=body,
            headers=headers,
            name=self._request_name,
            catch_response=True,
        ) as response:
            if not _check(response, self._request_name):
                return
            try:
                reply = response.json()["choices"][0]["message"].get("content") or ""
                self._messages.append({"role": "assistant", "content": reply})
            except (ValueError, KeyError, IndexError) as exc:
                response.failure(f"Malformed response: {exc!r}")
                return
        self._turn += 1

    @task
    def conversation_turn(self) -> None:
        """Send the next turn of this user's conversation (ordered by _turn)."""
        self._send_turn()


class RateLimitedUser(ConversationalUser):
    """Same multi-turn workload, but every request carries a per-user x-user-id
    so the gateway's per-user rate limit (20 req/min) trips -> HTTP 429.

    Only a handful of these should run alongside many plain ConversationalUsers:
    the 429s are expected (recorded, not failed) and visible on the dashboard
    rejection panel.
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


# LoadTestShape — ramp -> plateau -> END-OF-WINDOW SPIKE. Locust uses a shape
# whenever one is defined in the file (ignoring -u/-r/-t), so it is only
# defined when NANOSERVE_LOAD_SHAPE=1 (``make stress``); plain ``make load`` /
# manual -u runs keep the classic fixed-concurrency behaviour.
if SETTINGS.load_test.shape:
    from locust import LoadTestShape

    class RampAndSpikeShape(LoadTestShape):
        """Realistic curve: warm-up ramp, steady plateau, final spike.

        The spike at the end of the window is what actually fills the fp8 KV
        pool and forms a scheduler queue (--max-num-seqs 16) — steady low
        concurrency never does. Watch Grafana
        (vllm:kv_cache_usage_perc + num_requests_waiting) during the spike.
        """

        def __init__(self) -> None:
            super().__init__()
            lt = SETTINGS.load_test
            self._warmup = lt.warmup_s
            self._plateau = lt.plateau_s
            self._spike = lt.spike_s
            self._spike_hold = lt.spike_hold_s
            self._plateau_users = lt.users
            self._spike_users = lt.spike_users
            self._total = self._warmup + self._plateau + self._spike + self._spike_hold

        def tick(self) -> tuple[int, float] | None:
            t = self.get_run_time()
            if t <= self._warmup:
                # Ramp 0 -> plateau users over the warm-up window.
                n = int(self._plateau_users * (t / self._warmup))
                return max(1, n), 2.0
            if t <= self._warmup + self._plateau:
                # Steady plateau below the --max-num-seqs cap: no queue, just
                # throughput + prefix-cache hits.
                return self._plateau_users, 2.0
            if t <= self._warmup + self._plateau + self._spike:
                # Ramp from plateau up to the spike peak (crosses max_num_seqs).
                dt = t - self._warmup - self._plateau
                n = self._plateau_users + int(
                    (self._spike_users - self._plateau_users) * (dt / self._spike)
                )
                return n, 4.0
            if t <= self._total:
                # HOLD the spike peak: sustained over-subscription so the KV
                # pool fills and the scheduler queue stays visible in Grafana
                # (vllm:num_requests_waiting).
                return self._spike_users, 2.0
            return None  # spike hold over -> stop the run
