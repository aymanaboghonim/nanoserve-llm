# Manifests (K3s / KServe infrastructure)

Declarative Kubernetes source of truth. Populated phase by phase.

## Schema

| Subdirectory | Phase | Contents | Status |
| --- | --- | --- | --- |
| `infra/` | 2 | K3s bootstrap + GPU bridge + Gateway API platform stack | ✅ Populated |
| `serving/` | 2b/3 | Two-tier routing, quota/admission, OTel, DeepSeek Tier-2; later KServe `LLMInferenceService` | ✅ 2b populated (3 planned) |
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

## Phase 2b (`serving/`)

| File | Purpose |
| --- | --- |
| `aigatewayroute.yaml` | Two-tier per-model routing (local InferencePool / cloud AIServiceBackend) |
| `backend-deepseek.yaml` | Envoy `Backend` + `BackendTLSPolicy` for DeepSeek egress |
| `aiservicebackend-deepseek.yaml` | Envoy AI Gateway backend wrapper (model names) |
| `quotapolicy.yaml` | Per-user token quota on the Tier-2 backend |
| `backendtrafficpolicy.yaml` | RateLimit + AdmissionControl (protects local model) |
| `request-mirror-shadow.yaml` | RequestMirror shadow-test of Tier 2 (fire-and-forget) |
| `otel-collector.yaml` | OpenTelemetry Collector (trace ingest) |
| `secret-deepseek.yaml` | DeepSeek key template — applied via `make secret` (gitignored) |

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
