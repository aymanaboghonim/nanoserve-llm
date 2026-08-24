#!/usr/bin/env bash
# Gateway API platform stack installer (Phase 2, extended scope).
# Installs: cert-manager, Gateway API CRDs, GIE CRDs, llm-d CRDs, Envoy
# Gateway, Envoy AI Gateway, then applies the GatewayClass/Gateway.
#
# CRD manifests are applied from pinned upstream releases (matching KServe
# v0.20's own install scripts / kserve-deps.env) rather than vendored, to keep
# the repo lean:
#   - Gateway API v1.5.1          standard-install.yaml
#   - GIE (Inference Extension) v1.5.0  v1-manifests.yaml
#   - llm-d router v0.9.0         manifests.yaml
#
# Runs against the local k3s cluster. Needs kubectl + helm on PATH and the
# cluster healthy. Idempotent (helm upgrade --install; kubectl apply).
#
# Usage:  bash manifests/infra/gateway-stack.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

GATEWAY_API_VERSION="v1.5.1"
GIE_VERSION="v1.5.0"
LLMD_VERSION="v0.9.0"

GATEWAY_API_CRDS="https://github.com/kubernetes-sigs/gateway-api/releases/download/${GATEWAY_API_VERSION}/standard-install.yaml"
GIE_CRDS="https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GIE_VERSION}/v1-manifests.yaml"
LLMD_CRDS="https://github.com/llm-d/llm-d-router/releases/download/${LLMD_VERSION}/manifests.yaml"

echo "==> [1/6] cert-manager"
helm repo add jetstack https://charts.jetstack.io >/dev/null 2>&1 || true
helm repo update jetstack >/dev/null
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.18.0/cert-manager.crds.yaml
helm upgrade --install cert-manager jetstack/cert-manager \
  --namespace cert-manager --create-namespace \
  --set crds.enabled=false \
  --wait --timeout 5m

echo "==> [2/6] Gateway API ${GATEWAY_API_VERSION} + GIE ${GIE_VERSION} + llm-d ${LLMD_VERSION} CRDs"
kubectl apply -f "${GATEWAY_API_CRDS}"
kubectl apply -f "${GIE_CRDS}"
kubectl apply -f "${LLMD_CRDS}"
kubectl wait --for=condition=Established \
  crd/gateways.gateway.networking.k8s.io \
  crd/gatewayclasses.gateway.networking.k8s.io \
  crd/inferencepools.inference.networking.k8s.io \
  --timeout=90s || true

echo "==> [3/6] Envoy Gateway"
helm repo add envoy-gateway https://helm.envoyproxy.io >/dev/null 2>&1 || true
helm repo update envoy-gateway >/dev/null
helm upgrade --install eg envoy-gateway/gateway-helm \
  --namespace envoy-gateway-system --create-namespace \
  --wait --timeout 5m

echo "==> [4/6] Envoy AI Gateway (GIE provider)"
helm repo add envoy-ai-gateway https://envoyproxy.github.io/ai-gateway >/dev/null 2>&1 || true
helm repo update envoy-ai-gateway >/dev/null
helm upgrade --install eag envoy-ai-gateway/ai-gateway-helm \
  --namespace envoy-ai-gateway-system --create-namespace \
  --set config.envoyGatewayControllerName=gateway.envoyproxy.io/gatewayclass-controller \
  --wait --timeout 5m

echo "==> [5/6] GatewayClass + Gateway"
kubectl apply -k "${HERE}/gateway"

echo "==> [6/6] Hello-world route"
kubectl apply -f "${HERE}/gateway/hello.yaml"

echo "---"
echo "Verification:"
echo "  kubectl get gatewayclass kserve-gateway-class"
echo "  kubectl get gateway -n kserve"
echo "  kubectl get httproute -n gpu-test"
GATEWAY_ADDR="$(kubectl get gateway -n kserve kserve-gateway -o jsonpath='{.status.addresses[0].value}' 2>/dev/null || true)"
if [[ -n "${GATEWAY_ADDR}" ]]; then
  echo "  curl -s http://localhost:${GATEWAY_ADDR}/hello"
fi
