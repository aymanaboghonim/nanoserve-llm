"""Thin telemetry wrapper around vLLM's Prometheus metrics.

vLLM (Phase 3/4) exposes native metrics via Prometheus. This module deliberately
stays a *thin* adapter around :class:`PrometheusConnect` — the metric names and
the query shapes map 1:1 to what vLLM emits, so the Phase 4 ``ServiceMonitor``
relabeling and any dashboard queries keep working without changes here.

Critical metrics tracked (per the Blueprint):
    - ``vllm:kv_cache_usage_perc``          -> KV-cache saturation in [0, 1]
    - ``vllm:request_decode_time_seconds``  -> decode (inter-token) latency histogram
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from prometheus_api_client import PrometheusConnect

from nanoserve_llm.config import Settings

KV_CACHE_USAGE_METRIC = "vllm:kv_cache_usage_perc"
DECODE_TIME_METRIC = "vllm:request_decode_time_seconds"

# Query templates. vLLM emits these with a ``model_name`` label; the 1-minute
# range keeps the payload small for dashboard + gate checks.
KV_CACHE_QUERY = f'{KV_CACHE_USAGE_METRIC}{{model_name=~".+"}}[1m]'
DECODE_TIME_QUERY = f"histogram_quantile(0.95, sum(rate({DECODE_TIME_METRIC}_bucket[1m])) by (le))"


@dataclass(frozen=True)
class TelemetrySnapshot:
    """A point-in-time view of the engine's critical metrics."""

    kv_cache_usage_perc: float | None
    decode_p95_seconds: float | None
    raw: dict[str, Any]


class Telemetry:
    """Adapter for querying a Prometheus server scraping vLLM."""

    def __init__(self, base_url: str, settings: Settings | None = None) -> None:
        self._client = PrometheusConnect(url=base_url, disable_ssl=True)
        self._settings = settings

    @classmethod
    def from_settings(cls, settings: Settings) -> Telemetry:
        """Build a client from ``settings.serving.base_url`` + ``:9090`` Prometheus."""
        prometheus_url = settings.serving.base_url.replace("/v1", "").replace(":8000", ":9090")
        return cls(prometheus_url, settings=settings)

    def kv_cache_usage(self) -> float | None:
        """Return the latest KV-cache usage fraction in [0, 1], or None if absent."""
        values = self._client.get_current_metric_value(KV_CACHE_USAGE_METRIC)
        return _latest_value(values)

    def decode_p95_seconds(self) -> float | None:
        """Return the p95 decode latency in seconds, or None if absent."""
        result = self._client.custom_query(DECODE_TIME_QUERY)
        return _first_value(result)

    def snapshot(self) -> TelemetrySnapshot:
        """Collect both critical metrics in a single callable snapshot.

        Each query is isolated: if one metric is unavailable (e.g. Prometheus
        not yet scraping), the others are still returned and the missing one is
        ``None`` rather than raising.
        """
        raw: dict[str, Any] = {}
        kv_cache: float | None = None
        decode_p95: float | None = None

        try:
            kv_cache_values = self._client.get_current_metric_value(KV_CACHE_USAGE_METRIC)
            raw[KV_CACHE_USAGE_METRIC] = kv_cache_values
            kv_cache = _latest_value(kv_cache_values)
        except Exception as exc:  # noqa: BLE001 - telemetry must never crash the caller
            raw[KV_CACHE_USAGE_METRIC] = exc

        try:
            decode_result = self._client.custom_query(DECODE_TIME_QUERY)
            raw[DECODE_TIME_METRIC] = decode_result
            decode_p95 = _first_value(decode_result)
        except Exception as exc:  # noqa: BLE001
            raw[DECODE_TIME_METRIC] = exc

        return TelemetrySnapshot(
            kv_cache_usage_perc=kv_cache,
            decode_p95_seconds=decode_p95,
            raw=raw,
        )


def _latest_value(values: list[dict[str, Any]]) -> float | None:
    """Extract the most recent sample value from ``get_current_metric_value``."""
    if not values:
        return None
    try:
        return float(values[0]["value"][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _first_value(result: list[dict[str, Any]]) -> float | None:
    """Extract the first sample value from a ``custom_query`` result."""
    if not result:
        return None
    try:
        return float(result[0]["value"][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
