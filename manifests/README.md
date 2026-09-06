# Manifests (K3s / KServe infrastructure)

Declarative Kubernetes source of truth. Populated phase by phase.

## Schema

| Subdirectory | Phase | Contents | Status |
| --- | --- | --- | --- |
| `infra/` | 2 | K3s bootstrap + GPU bridge + Gateway API platform stack | ✅ Populated |
| `serving/` | 2b/3 | Two-tier routing, quota/admission, OTel, DeepSeek Tier-2; KServe `LLMInferenceService` | ✅ 2b/3 populated (LLMISVC Ready; gateway 200 via InferencePool) |
| `telemetry/` | 4 | Prometheus + Grafana (plain, static scrape; no Operator) | ✅ Populated (Phase 4) |

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
| `backend-deepseek.yaml` | Envoy `Backend` + `BackendTLSPolicy` (System CA) for DeepSeek egress TLS |
| `backendsecuritypolicy-deepseek.yaml` | EAG `BackendSecurityPolicy` (APIKey) — injects the DeepSeek key upstream |
| `aiservicebackend-deepseek.yaml` | Envoy AI Gateway backend wrapper (model names) |
| `quotapolicy.yaml` | Per-user token quota on the Tier-2 backend |
| `backendtrafficpolicy.yaml` | RateLimit + AdmissionControl (protects local model) — **disabled** (orphaned) |
| `request-mirror-shadow.yaml` | RequestMirror shadow-test of Tier 2 (fire-and-forget) — **disabled** |
| `otel-collector.yaml` | OpenTelemetry Collector (trace ingest) |
| `secret-deepseek.yaml` | DeepSeek key template — applied via `make secret` (gitignored); key MUST be `apiKey` |

> **Disabled:** `request-mirror-shadow.yaml` is intentionally left out of
> `kustomization.yaml` and `make apply` during the Phase 3 validation — its
> `/v1/chat/completions` path overlaps the LLMISVC-generated route and, with a
> bare Service backendRef, made Envoy Gateway 500 that path. The pool backendRef
> in the file is already GIE-correct (`group`+`kind`) — re-enable for the
> shadow/mirror phase. `backendtrafficpolicy.yaml` targets the deleted shadow
> route (orphaned) and caps 60 req/min — re-enable only with the shadow route.

## Phase 4 (`telemetry/`)

Plain Prometheus + Grafana (no Prometheus Operator / ServiceMonitor — deliberate
deviation from the Blueprint wording to save RAM on the 12GB box; the two scrape
jobs are static). Scrapes vLLM `/metrics` and the llm-d EPP.

| File | Purpose |
| --- | --- |
| `namespace.yaml` | `monitoring` namespace |
| `prometheus-config.yaml` | Scrape config: `vllm` job → `qwen-llm-kserve-workload-svc:8000`, `llm-d-epp` job → `llm-d-epp:9090` |
| `prometheus.yaml` | Prometheus deploy (pinned), 2Gi local-path PVC (3d retention), `Service prometheus:9090` |
| `grafana-datasource.yaml` | Provisioned Prometheus datasource (uid `prometheus`) |
| `grafana-dashboards-provider.yaml` | Provisioning provider → mounted dashboard JSON |
| `grafana-dashboards.yaml` | `NanoServe vLLM` dashboard: KV-cache %, queue pressure, TTFT p50/p95, decode p95 |
| `grafana.yaml` | Grafana deploy (pinned, read-only rootfs, no dashboard PVC), `Service grafana:3000` |
| `grafana-secret.yaml` | Admin credentials — applied via `make secret-grafana` (gitignored) |
| `grafana-route.yaml` | HTTPRoute on `kserve-gateway` → `grafana.192.168.1.2.nip.io` |

```bash
make telemetry        # kubectl apply -k manifests/telemetry
make secret-grafana   # apply the admin Secret from gitignored .env
# UI: http://grafana.192.168.1.2.nip.io  (or kubectl -n monitoring port-forward svc/grafana 3000:3000)
```

## Conventions

- Every YAML is validated by pre-commit (`check-yaml`) before commit.
- Resource limits must respect the hardware fence: 12GB RAM / 8GB VRAM, 4GB
  KV-cache budget (see `src/nanoserve_llm/config.py`).

## Layout

```
manifests/
├── README.md
├── infra/        # Phase 2: GPU bridge + Gateway API platform stack
├── serving/      # Phases 2b/3: two-tier routing + KServe LLMInferenceService
└── telemetry/    # Phase 4: Prometheus + Grafana (plain)
```
