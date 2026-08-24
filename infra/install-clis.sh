#!/usr/bin/env bash
# NanoServe Phase 2 — install kubectl, helm, yq into ~/.local/bin (no root).
# Idempotent: skips tools already present on PATH or in ~/.local/bin.
#
# Usage:  bash infra/install-clis.sh
set -euo pipefail

# --- Pin versions (x86_64 Linux) --------------------------------------------
KUBECTL_VERSION="v1.36.3"
HELM_VERSION="v3.17.3"
YQ_VERSION="v4.45.1"
ARCH="amd64"

LOCAL_BIN="${HOME}/.local/bin"
mkdir -p "${LOCAL_BIN}"

have() { command -v "$1" >/dev/null 2>&1; }

if have kubectl; then
  echo "[skip] kubectl already at $(command -v kubectl)"
else
  echo "[get]  kubectl ${KUBECTL_VERSION}"
  curl -fsSL -o "${LOCAL_BIN}/kubectl" \
    "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${ARCH}/kubectl"
  chmod +x "${LOCAL_BIN}/kubectl"
fi

if have helm; then
  echo "[skip] helm already at $(command -v helm)"
else
  echo "[get]  helm ${HELM_VERSION}"
  curl -fsSL -o /tmp/helm.tgz \
    "https://get.helm.sh/helm-${HELM_VERSION}-linux-${ARCH}.tar.gz"
  tar -xzf /tmp/helm.tgz -C /tmp
  mv "/tmp/linux-${ARCH}/helm" "${LOCAL_BIN}/helm"
  chmod +x "${LOCAL_BIN}/helm"
  rm -rf /tmp/helm.tgz /tmp/linux-${ARCH}
fi

if have yq; then
  echo "[skip] yq already at $(command -v yq)"
else
  echo "[get]  yq ${YQ_VERSION}"
  curl -fsSL -o "${LOCAL_BIN}/yq" \
    "https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_${ARCH}"
  chmod +x "${LOCAL_BIN}/yq"
fi

# Ensure ~/.local/bin is on PATH for this and future shells.
case ":$PATH:" in
  *":${LOCAL_BIN}:"*) ;;
  *) echo "export PATH=\"${LOCAL_BIN}:\$PATH\"" >> "${HOME}/.bashrc" ;;
esac
export PATH="${LOCAL_BIN}:${PATH}"

echo "---"
kubectl version --client --output=yaml 2>/dev/null | head -3 || true
helm version --short 2>/dev/null || true
yq --version 2>/dev/null || true
