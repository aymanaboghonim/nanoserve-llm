# NanoServe

A highly constrained, end-to-end **local MLOps pipeline**: GenAI serving, load
testing, and telemetry on **12GB system RAM / 8GB VRAM** (RTX 3070), driven by
KServe's `LLMInferenceService` CRD managing the vLLM engine natively.

| Phase | Focus | Status |
| --- | --- | --- |
| 1 | Repository orchestration & tooling (this repo) | ✅ In progress |
| 2 | Infrastructure bootstrap (WSL2 + K3s + NVIDIA bridge) | Planned |
| 3 | Serving plane (KServe `LLMInferenceService` + vLLM, Gateway API) | Planned |
| 4 | Telemetry & observability (Prometheus + Grafana) | Planned |
| 5 | Load generation & evaluation (Locust + DeepEval) | Planned |

See [`Blueprint.md`](Blueprint.md) for the full architecture.

---

## Hardware fencing

Every default in this repo encodes the machine's constraints so nothing silently
over-commits memory. Verify with `nanoserve-llm doctor`:

| Constraint | Value | Where enforced |
| --- | --- | --- |
| System RAM | 12 GB | `NANOSERVE_SYSTEM_RAM_GB` |
| GPU VRAM | 8 GB | `NANOSERVE_GPU_VRAM_GB` |
| vLLM `--gpu-memory-utilization` | 0.5 | `NANOSERVE_GPU_MEMORY_UTILIZATION` |
| KV-cache + weights budget | **4 GB** | derived (`VRAM × utilization`) |
| Reserved VRAM | 4 GB | derived |

## Quickstart

```bash
# 1. Create the environment (Python 3.12, dev group only)
uv sync

# 2. Sanity-check the hardware fence against the actual machine
uv run nanoserve-llm doctor

# 3. Install the commit gate (ruff, format, mypy, unit tests)
uv run pre-commit install

# 4. Run the full gate once
uv run pre-commit run --all-files
```

Optional dependency groups (kept out of the base env to stay light):

```bash
uv sync --all-groups                 # everything (dev + load + eval)
uv run --group load locust --version # Locust load testing (Phase 5)
uv run --group eval pytest evals -v  # DeepEval LLM-as-judge (Phase 5)
```

> **No torch / CUDA is downloaded.** The evaluation harness uses LLM-as-judge
> metrics only (DeepEval lazy-imports embeddings), so the lockfile contains zero
> torch/sentence-transformers packages. Verify anytime: `uv tree | grep -i torch`
> → no matches.

## Directory map

```
├── src/nanoserve_llm/        # Core Python package
│   ├── config.py             # Hardware fence + serving/load/eval settings (pydantic-settings)
│   ├── logging.py            # Structured stdlib logging
│   ├── telemetry.py          # Thin Prometheus adapter (vLLM metrics)
│   └── cli.py                # `nanoserve-llm doctor` sanity check
├── load_tests/               # Locust suite -> /v1/chat/completions
├── evals/                    # Endpoint contract + G-Eval quality tests
├── manifests/                # K3s/KServe YAMLs (Phases 2-4: infra/, serving/, telemetry/)
└── tests/                    # Unit tests (config, telemetry, CLI)
```

## Environment reference

All settings are env-overridable (`NANOSERVE_*`) or via a local `.env` —
see [`.env.example`](.env.example).

| Variable | Default | Purpose |
| --- | --- | --- |
| `NANOSERVE_SYSTEM_RAM_GB` | `12.0` | Host RAM cap |
| `NANOSERVE_GPU_VRAM_GB` | `8.0` | GPU VRAM |
| `NANOSERVE_GPU_MEMORY_UTILIZATION` | `0.5` | vLLM memory fraction → 4GB KV cap |
| `NANOSERVE_BASE_URL` | `http://localhost:8000/v1` | vLLM OpenAI-compatible endpoint |
| `NANOSERVE_MODEL` | `Qwen/Qwen2.5-1.5B-Instruct-AWQ` | Served model |
| `NANOSERVE_LOAD_*` | — | Locust defaults (users, spawn rate, tokens…) |
| `NANOSERVE_EVAL_*` | — | Judge model, threshold, live flag |

## Developer workflow

```bash
uv run ruff check .            # lint
uv run ruff format --check .   # formatting
uv run mypy src/nanoserve_llm  # type checks
uv run pytest -q               # unit tests (evals auto-skip without a live endpoint)
```

Commits are gated by pre-commit: lint (with auto-fix), format, YAML checks,
mypy, and fast unit tests must pass — a broken commit is refused.

## Roadmap notes

- **Phase 2** fills `manifests/infra/`: `.wslconfig` (16GB swap), K3s with
  `--disable traefik`, containerd → NVIDIA Container Toolkit bridge.
- **Phase 3** fills `manifests/serving/`: KServe `LLMInferenceService` via the
  Kubernetes Gateway API (no Knative), `--gpu-memory-utilization 0.5`.
- **Phase 4** fills `manifests/telemetry/`: Prometheus + `ServiceMonitor`
  scraping vLLM `/metrics`, Grafana dashboards for TTFT and queue pressure.
- **Phase 5** runs the Locust suite and DeepEval gates against the live endpoint
  (`NANOSERVE_EVAL_LIVE=1`), watching `vllm:kv_cache_usage_perc`.
- **Embedding metrics (optional)**: if you later want DeepEval's embedding-based
  metrics, reuse your installed CUDA-12 torch instead of re-downloading it:
  create the venv from the torch environment's interpreter with
  `uv venv --python <torch-env-python> --system-site-packages` and add
  `sentence-transformers` to the `eval` group. Keep the torch env on Python 3.12.
