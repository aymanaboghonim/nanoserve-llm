# Manifests (K3s / KServe infrastructure)

Declarative Kubernetes source of truth. Populated phase by phase.

## Schema

| Subdirectory | Phase | Contents | Status |
| --- | --- | --- | --- |
| `infra/` | 2 | K3s bootstrap + GPU bridge + Gateway API platform stack | ✅ Populated |
| `serving/` | 3 | KServe `LLMInferenceService` CRD targeting `Qwen/Qwen2.5-1.5B-Instruct-AWQ`, Gateway routing, `--gpu-memory-utilization 0.5` | Planned |
| `telemetry/` | 4 | Prometheus + `ServiceMonitor` scraping vLLM `/metrics`, Grafana dashboards (TTFT, queue pressure) | Planned |

## Phase 2 (`infra/`)

| File | Purpose |
| --- | --- |
| `device-plugin.yaml` | NVIDIA device plugin DaemonSet → `nvidia.com/gpu` capacity |
| `gpu-test-pod.yaml` | Smoke pod: `runtimeClassName: nvidia` + `nvidia.com/gpu: 1` |
| `kustomization.yaml` | Bundle for `kubectl apply -k manifests/infra` |
| `gateway-stack.sh` | cert-manager + Gateway API/GIE/llm-d CRDs (pinned remote manifests) + Envoy (AI) Gateway + GatewayClass/Gateway |
| `gateway/gateway.yaml` | `GatewayClass` + `Gateway` (LB, port 80) |
| `gateway/hello.yaml` | Hello-world HTTPRoute (validates the stack) |

Host-level (non-manifest) config lives in `infra/`: `wslconfig.example`,
`k3s/config.yaml`, `install-clis.sh`, `bootstrap.sh`, `verify.sh`.

## Conventions

- Every YAML is validated by pre-commit (`check-yaml`) before commit.
- Resource limits must respect the hardware fence: 12GB RAM / 8GB VRAM, 4GB
  KV-cache budget (see `src/nanoserve_llm/config.py`).

## Layout

```
manifests/
├── README.md
├── infra/        # Phase 2: GPU bridge + Gateway API platform stack
├── serving/      # Phase 3
└── telemetry/    # Phase 4
```
