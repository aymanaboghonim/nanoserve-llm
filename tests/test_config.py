"""Tests for the hardware-fencing configuration model."""

from __future__ import annotations

from nanoserve_llm.config import GPU_MEMORY_UTILIZATION_DEFAULT, Hardware, Settings


class TestHardwareDefaults:
    def test_default_caps_match_blueprint(self) -> None:
        hw = Hardware()
        assert hw.system_ram_gb == 12.0
        assert hw.gpu_vram_gb == 8.0
        assert hw.gpu_memory_utilization == GPU_MEMORY_UTILIZATION_DEFAULT

    def test_kv_cache_cap_tracks_utilization_default(self) -> None:
        hw = Hardware()
        # 8.0 VRAM x 0.80 (Granite-4.2-3B AWQ INT4 budget: ~2.5 weights + fp8 KV)
        assert hw.kv_cache_cap_gb == 6.4
        assert hw.reserved_vram_gb == 1.6

    def test_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv("NANOSERVE_GPU_MEMORY_UTILIZATION", "0.25")
        hw = Hardware()
        assert hw.kv_cache_cap_gb == 2.0
        assert hw.reserved_vram_gb == 6.0


class TestServingDefaults:
    def test_openai_compatible_base_url(self) -> None:
        settings = Settings()
        assert settings.serving.base_url == "http://localhost:8000/v1"
        assert "granite" in settings.serving.model

    def test_env_prefix_applies_to_nested_models(self, monkeypatch) -> None:
        monkeypatch.setenv("NANOSERVE_BASE_URL", "http://10.0.0.5:8000/v1")
        settings = Settings()
        assert settings.serving.base_url == "http://10.0.0.5:8000/v1"

    def test_unknown_env_is_ignored(self, monkeypatch) -> None:
        monkeypatch.setenv("NANOSERVE_TYPO_FIELD", "whatever")
        Settings()  # must not raise


class TestEvalGate:
    def test_live_flag_off_by_default(self) -> None:
        assert Settings().eval.live is False

    def test_live_flag_env(self, monkeypatch) -> None:
        monkeypatch.setenv("NANOSERVE_EVAL_LIVE", "1")
        assert Settings().eval.live is True


class TestE2EGate:
    def test_live_flag_off_by_default(self) -> None:
        assert Settings().e2e.live is False

    def test_live_flag_env(self, monkeypatch) -> None:
        monkeypatch.setenv("NANOSERVE_E2E_LIVE", "1")
        assert Settings().e2e.live is True

    def test_e2e_timeout_defaults(self) -> None:
        s = Settings()
        assert s.e2e.health_timeout_s == 5.0
        assert s.e2e.stream_tokens == 64
        assert s.e2e.tool_tokens == 128
