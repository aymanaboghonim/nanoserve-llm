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
RAM_GB="$(free -g | awk '/^Mem:/{print $2}')"
SWAP_GB="$(free -g | awk '/^Swap:/{print $2}')"
check "WSL RAM fence >= 12GB (have ${RAM_GB}G)" test "${RAM_GB}" -ge 12
check "WSL swap >= 16GB (have ${SWAP_GB}G)" test "${SWAP_GB}" -ge 16
check "cgroup v2 unified hierarchy" test "$(stat -fc %T /sys/fs/cgroup)" = "cgroup2fs"

echo "--- [B] k3s cluster ---"
check "kubeconfig present" test -f "${HOME}/.kube/config"
check "node Ready" kubectl get nodes -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' | grep -q True
NODE_VERSION="$(kubectl get node -o jsonpath='{.items[0].status.nodeInfo.kubeletVersion}' 2>/dev/null)"
info "kubelet ${NODE_VERSION}"
check "traefik disabled" bash -c '! kubectl get pods -n kube-system 2>/dev/null | grep -qi traefik'
check "metrics-server present" kubectl get deploy -n kube-system metrics-server >/dev/null 2>&1
check "kubectl top works" kubectl top node >/dev/null 2>&1

echo "--- [C] NVIDIA bridge ---"
check "nvidia runtime in containerd config" \
  grep -q nvidia /var/lib/rancher/k3s/agent/etc/containerd/config.toml 2>/dev/null
check "RuntimeClass nvidia exists" kubectl get runtimeclass nvidia >/dev/null 2>&1
check "node capacity nvidia.com/gpu=1" \
  test "$(kubectl get node -o jsonpath='{.items[0].status.capacity.nvidia\.com/gpu}')" = "1"
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

echo "--- [E] Persistence ---"
check "k3s enabled on boot" systemctl is-enabled k3s >/dev/null 2>&1
check "systemd running" systemctl is-system-running >/dev/null 2>&1 || true

echo
if [[ ${FAILED} -eq 0 ]]; then
  echo "==> ALL CHECKS PASSED"
else
  echo "==> SOME CHECKS FAILED (exit 1)"
fi
exit ${FAILED}
