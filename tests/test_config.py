"""Tests for the hardware-fencing configuration model."""

from __future__ import annotations

from nanoserve_llm.config import GPU_MEMORY_UTILIZATION_DEFAULT, Hardware, Settings


class TestHardwareDefaults:
    def test_default_caps_match_blueprint(self) -> None:
        hw = Hardware()
        assert hw.system_ram_gb == 12.0
        assert hw.gpu_vram_gb == 8.0
        assert hw.gpu_memory_utilization == GPU_MEMORY_UTILIZATION_DEFAULT

    def test_kv_cache_cap_is_half_of_vram(self) -> None:
        hw = Hardware()
        assert hw.kv_cache_cap_gb == 4.0
        assert hw.reserved_vram_gb == 4.0

    def test_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv("NANOSERVE_GPU_MEMORY_UTILIZATION", "0.25")
        hw = Hardware()
        assert hw.kv_cache_cap_gb == 2.0
        assert hw.reserved_vram_gb == 6.0


class TestServingDefaults:
    def test_openai_compatible_base_url(self) -> None:
        settings = Settings()
        assert settings.serving.base_url == "http://localhost:8000/v1"
        assert settings.serving.model.startswith("Qwen")

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
