#!/usr/bin/env bash
# NanoServe Phase 2 — verification gate.
# Exit code 0 = all checks pass. Prints a summary either way.
# Usage:  bash infra/verify.sh
set -uo pipefail

FAILED=0

pass() { printf '  \033[32m[PASS]\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m[FAIL]\033[0m %s\n' "$*"; FAILED=1; }
info() { printf '  \033[36m[info]\033[0m %s\n' "$*"; }
check() { # check <description> <command...>
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then pass "$desc"; else fail "$desc"; fi
}

echo "==> NanoServe Phase 2 verification"
echo

echo "--- [A] Host fencing (.wslconfig / swap) ---"
RAM_MB="$(free -m | awk '/^Mem:/{print $2}')"
SWAP_GB="$(free -g | awk '/^Swap:/{print $2}')"
# .wslconfig requests 12GB; WSL2 reports usable RAM a bit lower — accept >= ~10.7GiB.
check "WSL RAM fence ~12GB (have ${RAM_MB} MiB)" test "${RAM_MB}" -ge 11000
check "WSL swap >= 16GB (have ${SWAP_GB}G)" test "${SWAP_GB}" -ge 16
check "cgroup v2 unified hierarchy" test "$(stat -fc %T /sys/fs/cgroup)" = "cgroup2fs"

echo "--- [B] k3s cluster ---"
check "kubeconfig present" test -f "${HOME}/.kube/config"
check "node Ready" \
  bash -c 'kubectl get nodes -o jsonpath="{.items[0].status.conditions[?(@.type==\"Ready\")].status}" | grep -q True'
NODE_VERSION="$(kubectl get node -o jsonpath='{.items[0].status.nodeInfo.kubeletVersion}' 2>/dev/null)"
info "kubelet ${NODE_VERSION}"
check "traefik disabled" bash -c '! kubectl get pods -n kube-system 2>/dev/null | grep -qi traefik'
check "metrics-server present" kubectl get deploy -n kube-system metrics-server
check "kubectl top works" kubectl top node

echo "--- [C] NVIDIA bridge ---"
# containerd config is root-only; RuntimeClass + capacity are the primary proof.
# The grep runs via sudo -n opportunistically (skipped if no timestamp).
check "RuntimeClass nvidia exists" kubectl get runtimeclass nvidia
check "node capacity nvidia.com/gpu=1" \
  test "$(kubectl get node -o jsonpath='{.items[0].status.capacity.nvidia\.com/gpu}')" = "1"
if sudo -n grep -q nvidia /var/lib/rancher/k3s/agent/etc/containerd/config.toml 2>/dev/null; then
  pass "nvidia runtime in containerd config"
else
  info "containerd config root-only; runtime proven via RuntimeClass + capacity"
fi
GPU_POD="$(kubectl get pods -n gpu-test -l app=gpu-test -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)"
if [[ -n "${GPU_POD}" ]]; then
  check "GPU smoke pod nvidia-smi" kubectl logs -n gpu-test "${GPU_POD}" --tail=5 | grep -qi "RTX 3070"
else
  info "no gpu-test pod found (run: kubectl apply -k manifests/infra)"
fi

echo "--- [D] Gateway API stack ---"
check "Gateway API CRDs installed" \
  kubectl get crd gateways.gateway.networking.k8s.io >/dev/null 2>&1
check "GIE CRDs installed" \
  kubectl get crd inferencepools.inference.networking.k8s.io >/dev/null 2>&1
check "Envoy Gateway controller" kubectl get deploy -n envoy-gateway-system envoy-gateway >/dev/null 2>&1
check "Envoy AI Gateway controller" kubectl get deploy -n envoy-ai-gateway-system >/dev/null 2>&1
check "GatewayClass accepted" \
  test "$(kubectl get gatewayclass kserve-gateway-class -o jsonpath='{.status.conditions[?(@.type=="Accepted")].status}' 2>/dev/null)" = "True"
check "Gateway programmed (LB)" \
  test "$(kubectl get gateway kserve-gateway -n kserve -o jsonpath='{.status.conditions[?(@.type=="Programmed")].status}' 2>/dev/null)" = "True"
HELLO_CODE="$(kubectl get httproute hello -n gpu-test -o jsonpath='{.spec.parentRefs[0].name}' 2>/dev/null)"
info "hello HTTPRoute parent: ${HELLO_CODE}"

# WSL2 may reassign the node IP across restarts; resolve the Gateway's actual
# LB address dynamically instead of hardcoding it.
GW_ADDR="$(kubectl get gateway kserve-gateway -n kserve \
  -o jsonpath='{.status.addresses[0].value}' 2>/dev/null)"
if [[ -n "${GW_ADDR}" ]]; then
  check "hello route returns 200 via ${GW_ADDR}" \
    curl -s -m 10 -o /dev/null -w '%{http_code}' "http://${GW_ADDR}/hello" | grep -q 200
else
  info "gateway has no address yet — skipping hello route check"
fi

echo "--- [D2] Phase 2b: two-tier / traffic control / observability ---"
check "AIGatewayRoute CRD" kubectl get crd aigatewayroutes.aigateway.envoyproxy.io >/dev/null 2>&1
check "AIServiceBackend CRD" kubectl get crd aiservicebackends.aigateway.envoyproxy.io >/dev/null 2>&1
check "QuotaPolicy CRD" kubectl get crd quotapolicies.aigateway.envoyproxy.io >/dev/null 2>&1
check "BackendTrafficPolicy CRD" kubectl get crd backendtrafficpolicies.gateway.envoyproxy.io >/dev/null 2>&1
check "OTel Collector deployed" kubectl get deploy -n kserve otel-collector >/dev/null 2>&1
if kubectl get aigatewayroute kserve-ai-route -n kserve >/dev/null 2>&1; then
  info "AIGatewayRoute kserve-ai-route present (local/cloud tiering)"
else
  info "AIGatewayRoute not yet applied (Phase 2b apply)"
fi

echo "--- [D3] Phase 3: KServe serving plane ---"
check "KServe LLMIsvc CRDs" kubectl get crd llminferenceservices.serving.kserve.io >/dev/null 2>&1
check "LLMISVC controller" kubectl get deploy -n kserve llmisvc-controller-manager >/dev/null 2>&1
check "LWS controller" kubectl get deploy -n lws-system lws-controller-manager >/dev/null 2>&1
check "well-known configs present" \
  kubectl get llminferenceserviceconfig -n kserve kserve-config-llm-template >/dev/null 2>&1
check "llm-d EPP running" kubectl get deploy -n kserve llm-d-epp >/dev/null 2>&1
check "InferencePool exists" kubectl get inferencepool -n kserve kserve-inference-pool >/dev/null 2>&1
if kubectl get llmisvc qwen-llm -n kserve >/dev/null 2>&1; then
  LLM_READY="$(kubectl get llmisvc qwen-llm -n kserve \
    -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)"
  if [[ "${LLM_READY}" = "True" ]]; then
    pass "LLMInferenceService qwen-llm Ready"
  else
    info "LLMInferenceService qwen-llm present (Ready=${LLM_READY:-unknown}; model may still be downloading)"
  fi
  check "KServe model HTTPRoute Accepted" \
    test "$(kubectl get httproute qwen-llm-kserve-route -n kserve \
      -o jsonpath='{.status.parents[0].conditions[?(@.type=="Accepted")].status}' 2>/dev/null)" = "True"
else
  info "LLMInferenceService qwen-llm not applied yet"
fi
check "DeepSeek AIServiceBackend Accepted" \
  test "$(kubectl get aiservicebackend deepseek-backend -n kserve \
    -o jsonpath='{.status.conditions[?(@.type=="Accepted")].status}' 2>/dev/null)" = "True"

echo "--- [D4] Phase 3/G: Argo CD ---"
check "Argo Applications CRD" kubectl get crd applications.argoproj.io >/dev/null 2>&1
check "Argo server" kubectl get deploy -n argocd argocd-server >/dev/null 2>&1
check "Argo repo-server" kubectl get deploy -n argocd argocd-repo-server >/dev/null 2>&1

echo "--- [E] Persistence ---"
check "k3s enabled on boot" systemctl is-enabled k3s
check "systemd running" systemctl is-system-running || true

echo
if [[ ${FAILED} -eq 0 ]]; then
  echo "==> ALL CHECKS PASSED"
else
  echo "==> SOME CHECKS FAILED (exit 1)"
fi
exit ${FAILED}
