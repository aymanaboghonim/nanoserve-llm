"""Central configuration for NanoServe.

All hardware fencing constraints, serving endpoint settings, load-test defaults,
and evaluation judge settings live here as a single source of truth.

Every value can be overridden via environment variables (``NANOSERVE_*``) or a
local ``.env`` file. See ``.env.example`` for a complete reference.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

# The vLLM engine is configured with --gpu-memory-utilization 0.80, capping
# model + KV-cache residency at ~6.4GB of the 8GB VRAM budget (Granite-4.2-3B
# AutoRound AWQ INT4 needs ~2.5GB weights via awq_marlin; the rest is the fp8
# KV cache ~97K tokens), leaving ~1GB net for the WSL2 desktop/display spikes
# (0.90 leaves only ~0.3GB -> CUDA OOM risk on this box).
GPU_MEMORY_UTILIZATION_DEFAULT = 0.80

# Model family used by the serving plane. The 3B Granite AWQ INT4 checkpoint
# serves with a 32K context window under the 6.4GB cap:
# --gpu-memory-utilization 0.80 --max-model-len 32768 --kv-cache-dtype fp8.
# Tool calling: --enable-auto-tool-choice + --tool-call-parser qwen3_coder
# (IBM-recommended). Reasoning is request-controlled (chat_template_kwargs
# enable_thinking; split into a `reasoning` field by the granite_thinking_parser).
DEFAULT_MODEL = "ibm-granite/granite-4.2-3b"


class Hardware(BaseSettings):
    """Hardware fencing constants for the constrained local box."""

    model_config = SettingsConfigDict(env_prefix="NANOSERVE_", extra="ignore")

    system_ram_gb: float = Field(default=12.0, description="Host system RAM limit in GB.")
    gpu_vram_gb: float = Field(default=8.0, description="GPU VRAM in GB.")
    gpu_memory_utilization: float = Field(
        default=GPU_MEMORY_UTILIZATION_DEFAULT,
        description="vLLM --gpu-memory-utilization (fraction of VRAM for model + KV cache).",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def kv_cache_cap_gb(self) -> float:
        """VRAM budget reserved for model weights + KV cache, in GB."""
        return round(self.gpu_vram_gb * self.gpu_memory_utilization, 2)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def reserved_vram_gb(self) -> float:
        """VRAM deliberately left untouched by the engine, in GB."""
        return round(self.gpu_vram_gb - self.kv_cache_cap_gb, 2)


class Serving(BaseSettings):
    """vLLM OpenAI-compatible endpoint configuration."""

    model_config = SettingsConfigDict(env_prefix="NANOSERVE_", extra="ignore")

    base_url: str = Field(
        default="http://localhost:8000/v1",
        description="Base URL of the vLLM OpenAI-compatible API.",
    )
    model: str = Field(default=DEFAULT_MODEL, description="Model name used in requests.")
    api_key: str = Field(default="EMPTY", description="API key (vLLM default is EMPTY).")
    request_timeout_s: float = Field(
        default=120.0,
        description="Per-request timeout in seconds (small models on shared VRAM are slow).",
    )
    max_retries: int = Field(default=2, description="Retries for transient 5xx / network errors.")


class LoadTest(BaseSettings):
    """Locust load-test defaults (Phase 5). Conservative for the 6.4GB VRAM budget."""

    model_config = SettingsConfigDict(env_prefix="NANOSERVE_LOAD_", extra="ignore")

    users: int = Field(default=8, description="Peak concurrent virtual users.")
    spawn_rate: int = Field(default=1, description="Users spawned per second.")
    run_time: str = Field(default="1m", description="Headless run duration (e.g. '2m', '30s').")
    max_tokens: int = Field(default=128, description="Max generated tokens per request.")
    prompt: str = Field(
        default="Explain the concept of a KV cache in one sentence.",
        description="Prompt sent by every virtual user.",
    )
    # Multi-turn conversation mode (NANOSERVE_LOAD_TURNS). Each conversational
    # user runs a private, ordered multi-turn session (@seq_task cycle): the
    # context head is a unique long document, every assistant reply is
    # appended, and a fresh session starts after ``turns`` turns (or once the
    # conversation would exceed the context window). Sequential turns model
    # real RAG-agent bursts instead of stateless one-shot requests.
    turns: int = Field(
        default=3,
        description="Turns per conversation session before the context resets.",
    )
    system_prompt: str = Field(
        default="You are a concise, helpful assistant on a constrained local GPU.",
        description="System prompt prepended once per conversation.",
    )
    # RAG-style per-user context length (NANOSERVE_LOAD_CONTEXT_TOKENS). The
    # multi-turn user's unique document is padded to ~this many tokens so a
    # handful of concurrent users actually fill the fp8 KV pool (~97K tokens).
    # Default 2048 -> ~30-40% pool at 16 running; raise to 3000-6000 with
    # ``make stress`` for saturation runs.
    context_tokens: int = Field(
        default=2048,
        description="Approx tokens in each multi-turn user's unique context document.",
    )
    # Granite-4.2 thinking: the chat template defaults enable_thinking=true and
    # thinking tokens burn the max_tokens budget, so content can be empty on
    # short generations. Load tests measure engine throughput -> keep thinking
    # OFF by default; set NANOSERVE_LOAD_THINKING=1 (and raise max_tokens) to
    # also exercise the reasoning path under load.
    thinking: bool = Field(
        default=False,
        description="Send chat_template_kwargs.enable_thinking with each request.",
    )
    # Think-time between requests (seconds). Short = dense bursts that actually
    # build KV-cache occupancy and a scheduler queue (the observed problem:
    # 2-5 s think times left the KV pool near 0% with no queueing).
    think_min: float = Field(default=0.2, description="Min think time between requests (s).")
    think_max: float = Field(default=1.0, description="Max think time between requests (s).")
    # Ramp->plateau->end-spike shape (NANOSERVE_LOAD_SHAPE=1). Locust only uses
    # a LoadTestShape when one is defined in the file; these knobs let the
    # ``make stress`` target push a realistic end-of-window spike.
    shape: bool = Field(
        default=False,
        description="Use the ramp/plateau/spike LoadTestShape (overrides -u/-r/-t).",
    )
    warmup_s: float = Field(default=15.0, description="Shape: ramp 0 -> users over N seconds.")
    plateau_s: float = Field(default=60.0, description="Shape: hold at ``users`` for N seconds.")
    spike_s: float = Field(
        default=30.0, description="Shape: final spike to ``spike_users`` for N seconds."
    )
    spike_users: int = Field(
        default=28,
        description="Shape: user count at the end-of-window spike (KV/queue saturation).",
    )
    spike_hold_s: float = Field(
        default=30.0,
        description="Shape: hold the spike peak for N seconds (lets the queue be visible).",
    )


class Eval(BaseSettings):
    """DeepEval LLM-as-judge settings (Phase 5)."""

    model_config = SettingsConfigDict(env_prefix="NANOSERVE_EVAL_", extra="ignore")

    judge_model: str = Field(
        default=DEFAULT_MODEL,
        description="LLM used as judge. Defaults to the served model itself.",
    )
    live: bool = Field(
        default=False,
        description="Set NANOSERVE_EVAL_LIVE=1 to run e2e quality tests against a live endpoint.",
    )
    threshold: float = Field(
        default=0.5,
        description="Pass threshold for quality metrics in [0, 1].",
    )


class E2E(BaseSettings):
    """Live end-to-end contract test gate (tests/e2e — Repository 2 contract).

    The suites in ``tests/e2e`` assert the immutable HTTP contract that the
    downstream Arabic Legal RAG Agent consumes: /healthz, SSE streaming,
    OpenAI tool-calling, and the Tier-2 Gemini route. They are skipped at
    collection time unless ``NANOSERVE_E2E_LIVE=1`` so plain ``pytest`` never
    hangs against a cold cluster.
    """

    model_config = SettingsConfigDict(env_prefix="NANOSERVE_E2E_", extra="ignore")

    live: bool = Field(
        default=False,
        description="Set NANOSERVE_E2E_LIVE=1 to run tests/e2e against the live cluster.",
    )
    health_timeout_s: float = Field(
        default=5.0,
        description="Timeout for the gateway /healthz probe (curl --max-time).",
    )
    stream_tokens: int = Field(
        default=64,
        description="Max tokens for the streaming contract test.",
    )
    tool_tokens: int = Field(
        default=128,
        description="Max tokens for the tool-calling contract test.",
    )


class Settings(BaseSettings):
    """Top-level settings aggregating all namespaces."""

    model_config = SettingsConfigDict(env_prefix="NANOSERVE_", extra="ignore")

    log_level: str = Field(default="INFO", description="Root log level.")
    hardware: Hardware = Field(default_factory=Hardware)
    serving: Serving = Field(default_factory=Serving)
    load_test: LoadTest = Field(default_factory=LoadTest)
    eval: Eval = Field(default_factory=Eval)
    e2e: E2E = Field(default_factory=E2E)


@lru_cache
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance (env is read once per process)."""
    return Settings()
