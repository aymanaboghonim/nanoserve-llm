#!/usr/bin/env bash
# NanoServe Phase 2 — host bootstrap (requires root).
#
# 1. Install the NVIDIA Container Toolkit (k3s auto-detects it on restart)
# 2. Install the k3s server (pinned) with the fencing config from
#    infra/k3s/config.yaml (disable traefik, kubelet eviction fencing)
# 3. Enable k3s as a systemd service so it auto-starts with WSL
#
# Usage:  sudo bash infra/bootstrap.sh
# Pre-req: kubectl/helm/yq on PATH (run bash infra/install-clis.sh first).
set -euo pipefail

K3S_VERSION="v1.36.3+k3s1"
NVIDIA_TOOLKIT_VERSION="1.20.0-1"
CONFIG_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/k3s/config.yaml"

if [[ ${EUID} -ne 0 ]]; then
  echo "error: run with sudo (this script mutates the host OS)" >&2
  exit 1
fi

echo "==> [1/4] Installing NVIDIA Container Toolkit ${NVIDIA_TOOLKIT_VERSION}"
if command -v nvidia-ctk >/dev/null 2>&1; then
  echo "     nvidia-ctk already installed; skipping"
else
  apt-get update -qq
  apt-get install -y --no-install-recommends curl ca-certificates gnupg2
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    | tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
  apt-get update -qq
  apt-get install -y \
    nvidia-container-toolkit="${NVIDIA_TOOLKIT_VERSION}" \
    nvidia-container-toolkit-base="${NVIDIA_TOOLKIT_VERSION}" \
    libnvidia-container-tools="${NVIDIA_TOOLKIT_VERSION}" \
    libnvidia-container1="${NVIDIA_TOOLKIT_VERSION}"
fi

echo "==> [2/4] Writing k3s config to /etc/rancher/k3s/config.yaml"
mkdir -p /etc/rancher/k3s
install -m 0644 "${CONFIG_SRC}" /etc/rancher/k3s/config.yaml
grep -E "disable|traefik|eviction|system-reserved" /etc/rancher/k3s/config.yaml || true

echo "==> [3/4] Installing k3s ${K3S_VERSION}"
if command -v k3s >/dev/null 2>&1 && systemctl is-active --quiet k3s; then
  echo "     k3s already running; skipping install"
else
  # `server --config` is provided by config.yaml via INSTALL_K3S_EXEC.
  INSTALL_K3S_VERSION="${K3S_VERSION}" \
  INSTALL_K3S_EXEC="server --config /etc/rancher/k3s/config.yaml" \
    curl -sfL https://get.k3s.io | sh -
fi

# Ensure the k3s unit explicitly loads our config file. The installer does not
# always bake INSTALL_K3S_EXEC into ExecStart; k3s auto-loads
# /etc/rancher/k3s/config.yaml anyway, but being explicit is deterministic
# (idempotent: skips if --config already present).
if ! grep -q -- "--config /etc/rancher/k3s/config.yaml" /etc/systemd/system/k3s.service; then
  echo "     patching k3s unit to load /etc/rancher/k3s/config.yaml explicitly"
  sed -i 's|^ExecStart=/usr/local/bin/k3s \\|ExecStart=/usr/local/bin/k3s --config /etc/rancher/k3s/config.yaml \\|' \
    /etc/systemd/system/k3s.service
  systemctl daemon-reload
fi

echo "==> [4/4] Ensuring k3s auto-starts with WSL (systemd)"
systemctl enable k3s >/dev/null 2>&1 || true

echo "---"
echo "done. Waiting for cluster readiness (up to 120s)..."
for _ in $(seq 1 60); do
  if kubectl get --raw='/readyz' >/dev/null 2>&1; then
    echo "cluster ready."
    break
  fi
  sleep 2
done
kubectl get nodes || true
echo
echo "NEXT: verify the NVIDIA runtime was auto-detected:"
echo "  grep nvidia /var/lib/rancher/k3s/agent/etc/containerd/config.toml"
echo "  kubectl get runtimeclasses"
echo "then apply the GPU stack:"
echo "  kubectl apply -k manifests/infra"
