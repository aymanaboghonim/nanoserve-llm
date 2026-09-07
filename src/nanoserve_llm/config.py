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

# The vLLM engine is configured with --gpu-memory-utilization 0.5, capping
# model + KV-cache residency at 4GB of the 8GB VRAM budget.
GPU_MEMORY_UTILIZATION_DEFAULT = 0.5

# Model family used by the serving plane (Phase 3). A ~1.5B AWQ quantized model
# is small enough to fit under the 4GB VRAM cap on this hardware.
DEFAULT_MODEL = "Qwen/Qwen2.5-1.5B-Instruct-AWQ"


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
    """Locust load-test defaults (Phase 5). Conservative for a 4GB KV budget."""

    model_config = SettingsConfigDict(env_prefix="NANOSERVE_LOAD_", extra="ignore")

    users: int = Field(default=8, description="Peak concurrent virtual users.")
    spawn_rate: int = Field(default=1, description="Users spawned per second.")
    run_time: str = Field(default="1m", description="Headless run duration (e.g. '2m', '30s').")
    max_tokens: int = Field(default=64, description="Max generated tokens per request.")
    prompt: str = Field(
        default="Explain the concept of a KV cache in one sentence.",
        description="Prompt sent by every virtual user.",
    )
    # Multi-turn conversation mode (NANOSERVE_LOAD_TURNS). Each virtual user
    # carries a private message history and appends every assistant reply, so
    # context grows per turn — realistic conversations that exercise KV-cache
    # growth + prefix reuse, instead of stateless one-shot requests.
    turns: int = Field(
        default=1,
        description="Conversation turns per user (>1 enables multi-turn context growth).",
    )
    system_prompt: str = Field(
        default="You are a concise, helpful assistant on a constrained local GPU.",
        description="System prompt prepended once per conversation.",
    )
    # Think-time between a user's turns (seconds). Lower = denser load.
    think_min: float = Field(default=2.0, description="Min think time between turns (s).")
    think_max: float = Field(default=5.0, description="Max think time between turns (s).")


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


class Settings(BaseSettings):
    """Top-level settings aggregating all namespaces."""

    model_config = SettingsConfigDict(env_prefix="NANOSERVE_", extra="ignore")

    log_level: str = Field(default="INFO", description="Root log level.")
    hardware: Hardware = Field(default_factory=Hardware)
    serving: Serving = Field(default_factory=Serving)
    load_test: LoadTest = Field(default_factory=LoadTest)
    eval: Eval = Field(default_factory=Eval)


@lru_cache
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance (env is read once per process)."""
    return Settings()
