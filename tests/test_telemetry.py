"""Tests for the telemetry adapter, using a mocked Prometheus client."""

from __future__ import annotations

from unittest.mock import MagicMock

from nanoserve_llm.telemetry import (
    DECODE_TIME_METRIC,
    KV_CACHE_USAGE_METRIC,
    Telemetry,
    _first_value,
    _latest_value,
)


class TestValueExtractors:
    def test_latest_value(self) -> None:
        samples = [{"value": [123.0, "0.42"]}]
        assert _latest_value(samples) == 0.42

    def test_latest_value_empty(self) -> None:
        assert _latest_value([]) is None

    def test_latest_value_malformed(self) -> None:
        assert _latest_value([{"value": "not-a-list"}]) is None

    def test_first_value(self) -> None:
        result = [{"value": [123.0, "1.5"]}]
        assert _first_value(result) == 1.5

    def test_first_value_empty(self) -> None:
        assert _first_value([]) is None


class TestTelemetry:
    def test_kv_cache_usage(self) -> None:
        client = MagicMock()
        client.get_current_metric_value.return_value = [{"value": [1.0, "0.63"]}]
        telemetry = Telemetry("http://prometheus:9090")
        telemetry._client = client  # noqa: SLF001 - test seam

        assert telemetry.kv_cache_usage() == 0.63
        client.get_current_metric_value.assert_called_once_with(KV_CACHE_USAGE_METRIC)

    def test_kv_cache_usage_absent(self) -> None:
        client = MagicMock()
        client.get_current_metric_value.return_value = []
        telemetry = Telemetry("http://prometheus:9090")
        telemetry._client = client  # noqa: SLF001

        assert telemetry.kv_cache_usage() is None

    def test_decode_p95(self) -> None:
        client = MagicMock()
        client.custom_query.return_value = [{"value": [1.0, "0.234"]}]
        telemetry = Telemetry("http://prometheus:9090")
        telemetry._client = client  # noqa: SLF001

        assert telemetry.decode_p95_seconds() == 0.234
        assert "histogram_quantile" in client.custom_query.call_args.args[0]
        assert DECODE_TIME_METRIC in client.custom_query.call_args.args[0]

    def test_snapshot_collects_both(self) -> None:
        client = MagicMock()
        client.get_current_metric_value.return_value = [{"value": [1.0, "0.5"]}]
        client.custom_query.return_value = [{"value": [1.0, "0.1"]}]
        telemetry = Telemetry("http://prometheus:9090")
        telemetry._client = client  # noqa: SLF001

        snap = telemetry.snapshot()
        assert snap.kv_cache_usage_perc == 0.5
        assert snap.decode_p95_seconds == 0.1
        assert KV_CACHE_USAGE_METRIC in snap.raw
        assert DECODE_TIME_METRIC in snap.raw

    def test_snapshot_handles_query_failure(self) -> None:
        client = MagicMock()
        client.get_current_metric_value.side_effect = RuntimeError("connection refused")
        client.custom_query.return_value = [{"value": [1.0, "0.1"]}]
        telemetry = Telemetry("http://prometheus:9090")
        telemetry._client = client  # noqa: SLF001

        snap = telemetry.snapshot()  # must not raise
        assert snap.kv_cache_usage_perc is None
