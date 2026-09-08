"""Tests for the ``nanoserve-llm doctor`` CLI."""

from __future__ import annotations

from unittest.mock import patch

from nanoserve_llm import cli


class TestDoctorCLI:
    def test_render_contains_fence(self) -> None:
        from nanoserve_llm.config import Settings

        report = cli._render(Settings())
        assert "kv-cache cap  : 6.4 GB" in report
        assert "granite-4.2-3b" in report
        assert "NanoServe doctor" in report

    def test_ram_unknown_on_missing_sysconf(self, monkeypatch) -> None:
        def boom(name: str) -> int:
            raise OSError("not available")

        monkeypatch.setattr(cli.os, "sysconf", boom)
        assert cli._system_ram_gb() is None

    def test_gpu_none_without_nvidia_smi(self, monkeypatch) -> None:
        monkeypatch.setattr(cli.shutil, "which", lambda _: None)
        assert cli._gpu_info() is None

    def test_gpu_parses_smi_output(self, monkeypatch) -> None:
        class FakeResult:
            returncode = 0
            stdout = "NVIDIA GeForce RTX 3070, 8192\n"

        monkeypatch.setattr(cli.shutil, "which", lambda _: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(
            cli.subprocess,
            "run",
            lambda *a, **k: FakeResult(),  # noqa: ARG005
        )
        info = cli._gpu_info()
        assert info is not None
        assert info["name"] == "NVIDIA GeForce RTX 3070"
        assert info["vram_gb"] == "8.0"

    def test_main_runs_doctor(self, capsys) -> None:
        with patch.object(cli, "_gpu_info", return_value=None):
            exit_code = cli.main(["doctor"])
        captured = capsys.readouterr()
        assert exit_code == 0
        assert "NanoServe doctor" in captured.out

    def test_main_defaults_to_doctor(self, capsys) -> None:
        with patch.object(cli, "_gpu_info", return_value=None):
            exit_code = cli.main([])
        assert exit_code == 0
        assert "NanoServe doctor" in capsys.readouterr().out
