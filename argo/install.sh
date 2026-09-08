#!/usr/bin/env bash
# Argo CD full install (non-HA, single-node k3s) + app-of-apps bootstrap.
#
# Installs Argo CD 3.5 (tested against Kubernetes v1.36 — our k3s). The
# app-of-apps (argo/app-of-apps.yaml) tracks the three manifests/ bundles
# from the published GitHub remote (github.com/aymanaboghonim/nanoserve-llm,
# branch main). This script installs Argo CD only; register the repo + apply
# the app-of-apps afterwards (steps printed at the end).
#
# Usage:  bash argo/install.sh
set -euo pipefail

ARGOCD_VERSION="v3.5.2"
ARGO_NS="argocd"

# CRDs first (server-side; the install.yaml embeds them and conflicts).
# The Applicationset CRD annotation is too large to re-apply via install.yaml.
echo "==> [1/3] Installing Argo CD CRDs (${ARGOCD_VERSION})"
kubectl apply --server-side --force-conflicts \
  -k "https://github.com/argoproj/argo-cd/manifests/crds?ref=${ARGOCD_VERSION}"

echo "==> [2/3] Installing Argo CD (namespace-install, ${ARGOCD_VERSION})"
kubectl create ns "${ARGO_NS}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
# namespace-install is CRD-less and must be applied INTO the argocd ns.
curl -fsSL \
  "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_VERSION}/manifests/namespace-install.yaml" \
  | kubectl apply -n "${ARGO_NS}" -f -
kubectl wait -n "${ARGO_NS}" --for=condition=Available \
  deploy/argocd-server deploy/argocd-repo-server \
  --timeout=5m

# repo-server uses local-path (k3s default StorageClass) for its PVC — no patch.
echo "==> [3/3] Argo CD installed."
echo "---"
echo "Argo CD is up. Get the admin password:"
echo "  kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d"
echo "Port-forward the UI:  kubectl -n argocd port-forward svc/argocd-server 8080:443"
echo "Register the repo + apply the app-of-apps (public repo — no credentials):"
echo "  kubectl apply -f - <<'EOF'"
echo "  apiVersion: v1"
echo "  kind: Secret"
echo "  metadata:"
echo "    name: nanoserve-llm-repo"
echo "    namespace: argocd"
echo "    labels:"
echo "      argocd.argoproj.io/secret-type: repository"
echo "  stringData:"
echo "    type: git"
echo "    url: https://github.com/aymanaboghonim/nanoserve-llm.git"
echo "  EOF"
echo "  kubectl apply -f argo/app-of-apps.yaml   # 3 Applications (infra/serving/telemetry)"
