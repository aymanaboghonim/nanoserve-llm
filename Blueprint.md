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

#### Phase 3: The Serving Plane

The deployment utilizes KServe's modern dual-track strategy to avoid Knative overhead.

* **Controllers:** Install KServe utilizing the Kubernetes Gateway API rather than Knative to handle intelligent request scheduling.
* **Resource Deployment:** Apply the `LLMInferenceService` manifest targeting a quantized ~1.5B parameter model (e.g., Qwen2.5-1.5B-AWQ).
* **Engine Tuning:** Expose vLLM's OpenAI-compatible endpoint with `--gpu-memory-utilization 0.5` to rigidly cap VRAM consumption at 4GB, reserving capacity for the KV-cache.

#### Phase 4: Telemetry & Observability

vLLM emits native metrics without requiring code changes or sidecar instrumentation.

* **Data Collection:** Deploy a lightweight Prometheus pod and apply a `ServiceMonitor` to scrape vLLM's `/metrics` endpoint via the KServe routing layer.
* **Critical Telemetry:** Track `vllm:kv_cache_usage_perc` (to monitor memory saturation) and `vllm:request_decode_time_seconds` (for inter-token generation latency).
* **Visualization:** Provision Grafana to render dashboards visualizing Time-To-First-Token (TTFT) and concurrent queue pressure.

#### Phase 5: Load Generation & Evaluation

Execute continuous stress testing against the deployed engine to validate stability under load.

* **Load Testing:** Execute the Python `locust` suite against the deployed `/v1/chat/completions` endpoint, ramping up concurrent virtual users to benchmark throughput ceilings.
* **Evaluation:** Run a programmatic evaluation suite (e.g., DeepEval) using deterministic scripts to assert correctness, structural integrity, and latency thresholds of the generated responses.
