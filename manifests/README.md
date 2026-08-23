# Manifests (K3s / KServe infrastructure)

Declarative Kubernetes source of truth. **Populated in Phases 2–4**; this
directory is intentionally empty until the cluster exists.

## Planned schema (from the Blueprint)

| Subdirectory | Phase | Contents |
| --- | --- | --- |
| `infra/` | 2 | K3s bootstrap (containerd → NVIDIA Container Toolkit bridge), namespace, GPU resource fence |
| `serving/` | 3 | KServe `LLMInferenceService` CRD targeting `Qwen/Qwen2.5-1.5B-Instruct-AWQ`, Gateway API routing, `--gpu-memory-utilization 0.5` |
| `telemetry/` | 4 | Prometheus deployment + `ServiceMonitor` scraping vLLM `/metrics`, Grafana dashboards (TTFT, queue pressure) |

## Conventions

- Every YAML is validated by pre-commit (`check-yaml`) before commit.
- Resource limits must respect the hardware fence: 12GB RAM / 8GB VRAM, 4GB
  KV-cache budget (see `src/nanoserve_llm/config.py`).

## Layout

```
manifests/
├── README.md
├── infra/        # Phase 2
├── serving/      # Phase 3
└── telemetry/    # Phase 4
```
