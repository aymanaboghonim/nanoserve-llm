#!/usr/bin/env bash
# Argo CD full install (non-HA, single-node k3s) + app-of-apps bootstrap.
#
# Uses the official install.yaml (cluster-admin, same-cluster deploy) which is
# tested against Kubernetes v1.36 (Argo CD 3.5) — our k3s.
# repo-server uses the local-path StorageClass for its PVC.
#
# Usage:  bash argo/install.sh
set -euo pipefail

ARGOCD_VERSION="v3.5.2"
ARGO_NS="argocd"

echo "==> [1/4] Installing Argo CD CRDs (${ARGOCD_VERSION})"
kubectl apply --server-side --force-conflicts \
  -k "https://github.com/argoproj/argo-cd/manifests/crds?ref=${ARGOCD_VERSION}"

echo "==> [2/4] Installing Argo CD (non-HA, ${ARGOCD_VERSION})"
kubectl apply -f \
  "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_VERSION}/manifests/install.yaml"
kubectl wait --namespace "${ARGO_NS}" \
  --for=condition=Available \
  deployment/argocd-server deployment/argocd-repo-server \
  --timeout=5m

echo "==> [3/4] Ensuring repo-server PVC uses local-path"
# install.yaml ships a PVC without storageClass; local-path is the default
# StorageClass in k3s, so no patch is needed.

echo "==> [4/4] Bootstrapping app-of-apps"
kubectl apply -f argo/app-of-apps.yaml

echo "---"
echo "Argo CD is up. Get the admin password:"
echo "  kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d"
echo "Port-forward the UI:  kubectl -n argocd port-forward svc/argocd-server 8080:443"
