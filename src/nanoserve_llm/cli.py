"""NanoServe CLI — ``nanoserve-llm doctor`` hardware/config sanity check.

The Blueprint's Phase 1 gates everything on the hardware fence (12GB RAM /
8GB VRAM / 4GB KV budget). ``doctor`` surfaces the *actual* machine state next
to the *configured* constants so a mismatch is visible before any load test or
deployment step.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys

from nanoserve_llm.config import Settings, get_settings
from nanoserve_llm.logging import get_logger

logger = get_logger(__name__)


def _system_ram_gb() -> float | None:
    """Host RAM in GB, best-effort via ``sysconf`` (no psutil dependency)."""
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return pages * page_size / (1024**3)
    except (ValueError, OSError, AttributeError):
        return None


def _gpu_info() -> dict[str, str] | None:
    """Query ``nvidia-smi`` for GPU name and total VRAM, best-effort."""
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi is None:
        return None
    try:
        result = subprocess.run(
            [
                nvidia_smi,
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            return None
        name, vram_mib = (part.strip() for part in result.stdout.strip().split(",")[:2])
        return {"name": name, "vram_gb": str(round(int(vram_mib) / 1024, 1))}
    except (subprocess.SubprocessError, ValueError, IndexError):
        return None


def _render(settings: Settings) -> str:
    """Render the doctor report as a plain-text block."""
    gpu = _gpu_info()
    ram = _system_ram_gb()
    lines: list[str] = [
        "NanoServe doctor",
        "===============",
        f"python        : {platform.python_version()} ({sys.executable})",
        f"platform      : {platform.platform()}",
    ]
    lines.append(f"ram (actual)  : {ram:.1f} GB" if ram else "ram (actual)  : unknown")
    if gpu:
        lines.append(f"gpu (actual)  : {gpu['name']} ({gpu['vram_gb']} GB VRAM)")
    else:
        lines.append("gpu (actual)  : nvidia-smi not found (Phase 2/3 target)")
    hw = settings.hardware
    lines.extend(
        [
            "--- configured fence ---",
            f"ram cap       : {hw.system_ram_gb:.1f} GB",
            f"vram cap      : {hw.gpu_vram_gb:.1f} GB",
            f"gpu util      : {hw.gpu_memory_utilization:.2f}",
            f"kv-cache cap  : {hw.kv_cache_cap_gb:.1f} GB (reserved {hw.reserved_vram_gb:.1f} GB)",
            "--- serving ---",
            f"base url      : {settings.serving.base_url}",
            f"model         : {settings.serving.model}",
            "--- load / eval ---",
            f"load users    : {settings.load_test.users} (spawn {settings.load_test.spawn_rate}/s)",
            f"eval live     : {settings.eval.live} (judge: {settings.eval.judge_model})",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Entry point: ``nanoserve-llm doctor``."""
    parser = argparse.ArgumentParser(
        prog="nanoserve-llm",
        description="NanoServe local GenAI serving toolbox.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="doctor",
        choices=["doctor"],
        help="Subcommand to run (default: doctor).",
    )
    args = parser.parse_args(argv)

    if args.command == "doctor":
        print(_render(get_settings()))
        return 0
    parser.error(f"unknown command: {args.command}")
    return 2  # pragma: no cover - argparse.error exits


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
