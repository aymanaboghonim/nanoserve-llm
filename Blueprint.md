### Blueprint: Project NanoServe

**Architecture Synopsis:** A highly constrained, end-to-end local MLOps pipeline designed to execute GenAI serving, load testing, and telemetry on 12GB system RAM and 8GB VRAM. It leverages KServe's specialized `LLMInferenceService` Custom Resource Definition (CRD) to bypass legacy predictive ML architecture and manage the vLLM engine natively.

#### Phase 1: Repository Orchestration & Tooling

The workspace acts as the declarative source of truth for both Python evaluation tooling and Kubernetes infrastructure.

* **Initialization:** Execute `uv init nanoserve-llm` to bootstrap the deterministic Python workspace.
* **Dependency Locking:** Utilize `uv add locust prometheus-api-client deepeval` to construct the testing framework, locking the environment state via `uv.lock`.
* **Directory Schema:** Establish isolated directories for `/manifests` (K3s YAMLs), `/load_tests` (Locust scripts), and `/evals` (LLM-as-a-judge pipelines).

#### Phase 2: Infrastructure Bootstrap (WSL2 & K3s)

Strict hardware fencing is required to prevent the Linux OOM killer from terminating pods during the model memory-mapping phase.

* **Host Limits:** Enforce memory constraints via a `.wslconfig` file on the Windows host, dictating strict RAM limits and a minimum 16GB swap file.
* **Minimal Cluster:** Deploy K3s explicitly passing `--disable traefik` to reclaim system memory for model loading.
* **Hardware Bridge:** Configure the `containerd` runtime inside K3s to map the NVIDIA Container Toolkit to the physical RTX 3070 GPU.

**Implementation Notes (audit, Phase 2):**

* `.wslconfig` swap **defaults to 25% of RAM** — the 16GB minimum must be set explicitly (`swap=16GB`); also add `autoMemoryReclaim=gradual` + `sparseVhd=true` (modern WSL2).
* k3s version is **pinned** (`v1.36.3+k3s1`, K8s 1.36.3 / containerd 2.3.2) instead of unpinned `curl | sh` — satisfies KServe's K8s ≥ 1.32 requirement.
* **Kubelet fencing added** (not in the draft): `system-reserved=memory=1Gi` + `eviction-hard=memory.available<1Gi` — the k8s-side half of "prevent OOM during model mmap"; host fence alone is insufficient.
* k3s **auto-detects** `nvidia-container-runtime` (creating the `nvidia` RuntimeClass); the missing piece was the **NVIDIA device plugin** DaemonSet, which provides `nvidia.com/gpu` capacity for pod scheduling.
* Traefik disabled **only**; `servicelb` (klipper-lb) is kept for the LB-type Gateway, `local-path` for model PVCs, `metrics-server` for `kubectl top`.
* **Extended scope:** the Gateway API platform stack (cert-manager + Gateway API v1.5.1 + GIE v1.5.0 + Envoy Gateway + Envoy AI Gateway) is pre-installed in Phase 2 so Phase 3 is purely KServe + the model. Validated: KServe v0.20's LLMInferenceService runs on Gateway API — no Knative/Istio.
* Declarative k3s config (`/etc/rancher/k3s/config.yaml`, versioned in `infra/k3s/`) + `infra/verify.sh` gate + shellcheck in pre-commit.

#### Phase 2b: Production Hardening, Two-Tier Routing & Observability

Adds a hardening layer on top of the Phase 2 cluster: two-tier model routing
(local vLLM / cloud DeepSeek), quota + admission control, shadow-testing,
OpenTelemetry tracing, streaming latency benchmarking, an idempotent Makefile,
and full Argo CD GitOps.

* **Two-Tier Routing:** Per-model `AIGatewayRoute` rules — local model → GIE
  `InferencePool` (k8s Service), cloud model → Envoy Gateway `Backend` +
  `AIServiceBackend` (DeepSeek). Envoy AI Gateway's `AIServiceBackend.backendRef`
  **must** be an Envoy `Backend` (CEL-validated); it cannot target a k8s Service,
  and `InferencePool` + `AIServiceBackend` refs cannot mix in one rule.
* **Traffic Control:** `QuotaPolicy` (per-user limits, model-aware), Envoy
  `BackendTrafficPolicy` `RateLimit` + `AdmissionControl` (probabilistic shedding
  when the local model saturates — protects the 4GB KV budget). `RequestBuffer`
  is **not** used (incompatible with streaming `/v1/chat/completions`).
* **Shadow Testing:** `RequestMirror` filter mirrors local-model requests to the
  Tier-2 backend (fire-and-forget — responses ignored); it is **not** failover.
* **Secrets:** DeepSeek API key in a k8s `Secret` sourced from gitignored `.env`
  (never committed); `BackendTLSPolicy` secures Tier-2 egress.
* **Observability:** Lightweight OpenTelemetry Collector (≈50-100MB) ingests
  spans from `scripts/tracer.py` (OTel); `scripts/latency_benchmark.py` measures
  streaming TTFT/ITL (correlates with Phase-4 `vllm:request_decode_time_seconds`).
* **Makefile:** Idempotent targets (`install-clis`/`bootstrap`/`verify`/`apply`/
  `benchmark`/`tracer`/`argo`), proper `.PHONY`.
* **Argo CD (full):** Non-HA single-node install (`argoproj.io/v1alpha1`
  Applications), local-path PVC, bootstrapped via app-of-apps. Argo CD 3.5 is
  tested against Kubernetes v1.36 (our k3s). Costs ~300-500MB RAM — accepted.
* **Hardening:** non-root `securityContext` (drop all caps, read-only rootfs) +
  explicit `resources.requests/limits` on every manifest.

#### Phase 3: The Serving Plane

The deployment utilizes KServe's modern dual-track strategy to avoid Knative overhead.

* **Controllers:** Install KServe utilizing the Kubernetes Gateway API rather than Knative to handle intelligent request scheduling.
* **Resource Deployment:** Apply the `LLMInferenceService` manifest targeting a quantized ~1.5B parameter model (e.g., Qwen2.5-1.5B-AWQ).
* **Engine Tuning:** Expose vLLM's OpenAI-compatible endpoint with `--gpu-memory-utilization 0.5` to rigidly cap VRAM consumption at 4GB, reserving capacity for the KV-cache.

**Implementation Notes (audit, Phase 3):**

* With an EXTERNAL `scheduler.pool.ref` (user-managed `InferencePool`), KServe
  v0.20 copies `router.route.http.spec` VERBATIM — it does NOT auto-inject a
  pool backendRef (that only happens for KServe-managed pools). The route rule
  must therefore carry the pool backendRef explicitly:
  `group: inference.networking.k8s.io`, `kind: InferencePool`, `name:
  kserve-inference-pool` (no `port` — the pool's `targetPorts[0]: 8000` defines
  it). Without it, Envoy Gateway 500s ("0 weight") and Envoy AI Gateway never
  writes `InferencePool.status.parents[].Accepted` → LLMISVC stuck at
  `InferencePoolReady=False` ("no Gateway controller has accepted it yet").
* KServe is pure control-plane: traffic flows client → Envoy Gateway → (Envoy
  AI Gateway GIE extension) → InferencePool → llm-d EPP (ext-proc, KV-cache /
  load-aware pick) → vLLM pod. Request model names must equal vLLM's
  `--served-model-name` exactly (e.g. `Qwen/Qwen2.5-1.5B-Instruct-AWQ`).
* vLLM flags must be passed via the `VLLM_ADDITIONAL_ARGS` env (the preset's NGC
  entrypoint swallows container args). WSL2 Triton JIT needs
  `LIBRARY_PATH=/usr/local/cuda-13.0/compat` so gcc links libcuda at startup.
* Shadow/mirror HTTPRoutes must not share `/v1/chat/completions` with the
  LLMISVC route, and any InferencePool backendRef needs group+kind (a bare
  Service ref 500s).

#### Phase 4: Telemetry & Observability

vLLM emits native metrics without requiring code changes or sidecar instrumentation.

* **Data Collection:** Deploy a lightweight Prometheus pod and apply a `ServiceMonitor` to scrape vLLM's `/metrics` endpoint via the KServe routing layer.
* **Critical Telemetry:** Track `vllm:kv_cache_usage_perc` (to monitor memory saturation) and `vllm:request_decode_time_seconds` (for inter-token generation latency).
* **Visualization:** Provision Grafana to render dashboards visualizing Time-To-First-Token (TTFT) and concurrent queue pressure.

**Implementation Notes (audit, Phase 4):**

* Prometheus runs as the **Prometheus Operator** (helm `kube-prometheus-stack`,
  minimal values) in a dedicated `monitoring` namespace, with two
  `ServiceMonitor`s (vLLM `/metrics`, llm-d EPP `/metrics`). The original plan
  had called for plain Prometheus with static config to save RAM; that was
  revisited in favor of the operator + ServiceMonitors once the serving plane
  proved stable. The helm values disable everything nonessential (Alertmanager,
  kube-state-metrics, node-exporter, default rules, the operator's admission
  webhooks and TLS web server — the latter two so no webhook cert Secret is
  required).
* The operator's `Prometheus` CR is reconciled into a StatefulSet with a 2Gi
  local-path PVC and 3-day retention. A stable `prometheus.monitoring.svc`
  Service fronts it (Grafana datasource + `infra/verify.sh` proxy checks depend
  on the stable DNS name).
* vLLM is scraped via `qwen-llm-kserve-workload-svc.kserve:8000/metrics`
  (same-namespace ClusterIP; no TLS).
* Grafana is served through the shared `kserve-gateway` on a hostname route:
  `http://grafana.192.168.1.2.nip.io` (nip.io wildcard → the LB address). The
  Gateway listener allows cross-namespace routes, so the `monitoring`-ns
  HTTPRoute attaches without gateway changes. Admin credentials come from a
  gitignored `.env` via `make secret-grafana` (never committed).
* Metric families verified against this vLLM build's live `/metrics` export:
  `vllm:kv_cache_usage_perc`, `vllm:num_requests_running/waiting`,
  `vllm:time_to_first_token_seconds` (histogram),
  `vllm:request_decode_time_seconds` (histogram). Dashboards use these exact
  names.

#### Phase 5: Load Generation & Evaluation

Execute continuous stress testing against the deployed engine to validate stability under load.

* **Load Testing:** Execute the Python `locust` suite against the deployed `/v1/chat/completions` endpoint, ramping up concurrent virtual users to benchmark throughput ceilings.
* **Evaluation:** Run a programmatic evaluation suite (e.g., DeepEval) using deterministic scripts to assert correctness, structural integrity, and latency thresholds of the generated responses.

**Implementation Notes (audit, Phase 5):**

* All load/eval runs target the **serving gateway** (Envoy → EPP → pool), NOT the
  pod directly — that is the real deployment path. `make eval` / `make load` /
  `make bench` resolve the LB address dynamically and default to it.
* Validation results (2026-09-07): DeepEval contract + quality suite green via
  gateway (self-judged G-Eval on the served Qwen); streaming benchmark TTFT
  ~38ms / ITL ~15ms / ~67 tok/s; Locust smoke 8 users / 2 min = **355 requests,
  0 failures**, ~3 req/s, p50 600ms / p95 720ms. No vLLM/gateway restarts.
* **Tier-2 (DeepSeek) required two missing pieces** to work end-to-end:
  1. `BackendTLSPolicy` (`gateway.networking.k8s.io/v1`, NOT `v1alpha3` — the
     Gateway API v1.5.1 CRD only serves `v1`) on the Envoy `Backend` with
     `wellKnownCACertificates: System` + SNI hostname. Without it Envoy opens
     cleartext to :443 → 503.
  2. EAG `BackendSecurityPolicy` (`type: APIKey`) referencing the Secret — and
     the Secret key MUST be named `apiKey` (EAG: "secret … does not contain key
     apiKey"). Without it the gateway forwards no `Authorization` header → 401.
  Verified: `curl` via gateway with `x-ai-eg-model: deepseek-chat` → 200.
* Known harmless EPP log line under load: "Request latency values are invalid
  for TPOT calculation" — fires when a generation ends at/just after the first
  token (very short `max_tokens`); non-fatal, request still 200.

#### Phase 6: GitOps Consolidation (Argo CD)

Once Phases 2b-5 are stable, Argo CD manages the whole cluster declaratively.

* **Bootstrap:** Argo CD installed (Phase 2b); app-of-apps Application tracks all
  `manifests/` kustomize bundles (infra, serving, telemetry).
* **Drift Detection:** Argo CD surfaces drift between Git and cluster state;
  auto-sync optional per-app.
* **Constraint:** ~300-500MB RAM for Argo control-plane — the first candidate to
  trim if the 12GB fence tightens during Phase 3 model loading.
