# Manifests (K3s / KServe infrastructure)

Declarative Kubernetes source of truth. Populated phase by phase.

> Deployed + verified 2026-09-08: LLMISVC (granite-llm) Ready, gateway chat 200
> + `/healthz` on :8000, Tier-2 Gemini 200, Prometheus/Grafana UP (see repo
> `infra/verify.sh`).

## Schema

| Subdirectory | Phase | Contents | Status |
| --- | --- | --- | --- |
| `infra/` | 2 | K3s bootstrap + GPU bridge + Gateway API platform stack | ✅ Populated |
| `serving/` | 2b/3/4 | Two-tier routing, quota/admission, host-cache PV, OTel, Gemini Tier-2; KServe `LLMInferenceService` | ✅ Populated (LLMISVC Ready; gateway 200 via InferencePool) |
| `telemetry/` | 4 | Prometheus Operator (ServiceMonitors) + Grafana | ✅ Populated (Phase 4) |

## Phase 2 (`infra/`)

| File | Purpose |
| --- | --- |
| `device-plugin.yaml` | NVIDIA device plugin DaemonSet → `nvidia.com/gpu` capacity |
| `gpu-test-pod.yaml` | Smoke pod: `runtimeClassName: nvidia` + `nvidia.com/gpu: 1` |
| `kustomization.yaml` | Bundle for `kubectl apply -k manifests/infra` |
| `gateway-stack.sh` | cert-manager + Gateway API/GIE/llm-d CRDs (pinned remote manifests) + Envoy (AI) Gateway + GatewayClass/Gateway |
| `gateway/gateway.yaml` | `GatewayClass` + `Gateway` (LB, ports 80 + 8000) |
| `gateway/hello.yaml` | Hello-world HTTPRoute (validates the stack) |

Host-level (non-manifest) config lives in `infra/`: `wslconfig.example`,
`k3s/config.yaml`, `install-clis.sh`, `bootstrap.sh`, `verify.sh`.

## Phase 2b/4 (`serving/`)

| File | Purpose |
| --- | --- |
| `aigatewayroute.yaml` | Two-tier per-model routing (local InferencePool / cloud AIServiceBackend) |
| `backend-gemini.yaml` | Envoy `Backend` (`generativelanguage.googleapis.com:443`) + `BackendTLSPolicy` (System CA, SNI) |
| `backendsecuritypolicy-gemini.yaml` | EAG `BackendSecurityPolicy` (APIKey) — injects the Gemini key upstream |
| `aiservicebackend-gemini.yaml` | EAG backend wrapper — `schema.name: OpenAI` + `schema.prefix: /v1beta/openai` |
| `quotapolicy.yaml` | Per-user token quota on the Tier-2 backend (`gemini-3.5-flash-lite`) |
| `backendtrafficpolicy-local.yaml` | `AdmissionControl` on the LOCAL Tier-1 route (KV-cache protection under load) |
| `backendtrafficpolicy-rate-limit.yaml` | Per-`x-user-id` 20 req/min global rate limit on the local route (429 lever for the load suite) |
| `cors-policy.yaml` | Gateway-level permissive CORS (agent web clients) |
| `healthz-route.yaml` | `/healthz` → vLLM `/health` via URLRewrite (agent probe) |
| `model-pv.yaml` | Static hostPath PV → host-side flat model dir (`make download-model`) |
| `model-pvc.yaml` | Claim statically bound to `nanoserve-model-cache-pv` (pvc:// mount) |
| `request-mirror-shadow.yaml` | RequestMirror shadow-test of Tier 2 (fire-and-forget) — **disabled** |
| `otel-collector.yaml` | OpenTelemetry Collector (trace ingest) |
| `secret-gemini.yaml` | Gemini key template — applied via `make secret` (gitignored); key MUST be `apiKey` |

> **Disabled:** `request-mirror-shadow.yaml` is intentionally left out of
> `kustomization.yaml` and `make apply` during serving-plane validation — its
> `/v1/chat/completions` path overlaps the LLMISVC-generated route and, with a
> bare Service backendRef, made Envoy Gateway 500 that path. The pool backendRef
> in the file is already GIE-correct (`group`+`kind`) — re-enable for the
> shadow/mirror phase (retarget the mirror Service to the Tier-2 backend).

## Phase 4 (`telemetry/`)

Prometheus **Operator** (helm, minimal values) + Grafana. The operator-managed
Prometheus scrapes via `ServiceMonitor`s — vLLM `/metrics` and the llm-d EPP.
Everything nonessential is disabled in the helm values (Alertmanager,
kube-state-metrics, node-exporter, default rules) to fit the 12GB box.

| File | Purpose |
| --- | --- |
| `namespace.yaml` | `monitoring` namespace |
| `prometheus-operator-values.yaml` | Helm values for kube-prometheus-stack (operator + Prometheus CR only) |
| `servicemonitor-vllm.yaml` | ServiceMonitor → `granite-llm-kserve-workload-svc:8000` (vLLM) |
| `servicemonitor-epp.yaml` | ServiceMonitor → `llm-d-epp:9090` (metrics port) |
| `prometheus-service.yaml` | Stable `prometheus.monitoring.svc` → operator-managed Prometheus pods |
| `grafana-datasource.yaml` | Provisioned Prometheus datasource (uid `prometheus`) |
| `grafana-dashboards-provider.yaml` | Provisioning provider → mounted dashboard JSON |
| `grafana-dashboards.yaml` | `NanoServe vLLM` dashboard: KV-cache %, queue pressure, TTFT p50/p95, decode p95 |
| `grafana.yaml` | Grafana deploy (pinned, read-only rootfs, no dashboard PVC), `Service grafana:3000` |
| `grafana-secret.yaml` | Admin credentials — applied via `make secret-grafana` (gitignored) |
| `grafana-route.yaml` | HTTPRoute on `kserve-gateway` → `grafana.<gateway-LB>.nip.io` |

```bash
make prometheus-operator # helm: kube-prometheus-stack (operator + Prometheus CR)
make telemetry           # kubectl apply -k manifests/telemetry (ServiceMonitors + Grafana)
make secret-grafana      # apply the admin Secret from gitignored .env
# UI: http://grafana.<gateway-LB>.nip.io  (or: kubectl -n monitoring port-forward svc/grafana 3000:3000)
```

> The ServiceMonitors carry `release: prometheus-operator` (the chart's
> `serviceMonitorSelector`) so the operator-managed Prometheus picks them up.

## Conventions

- Every YAML is validated by pre-commit (`check-yaml`) before commit.
- Resource limits must respect the hardware fence: 12GB RAM / 8GB VRAM
  (~6.4GB vLLM budget at util 0.80; see `src/nanoserve_llm/config.py`).

## Layout

```
manifests/
├── README.md
├── infra/        # Phase 2: GPU bridge + Gateway API platform stack
├── serving/      # Phases 2b/3: two-tier routing + KServe LLMInferenceService
└── telemetry/    # Phase 4: Prometheus Operator + Grafana (ServiceMonitors)
```
