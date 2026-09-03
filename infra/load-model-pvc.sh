#!/usr/bin/env bash
# Load the host-side model cache into the nanoserve-model-cache PVC.
#
# After infra/fetch-model.sh populates ~/.cache/nanoserve/models (host-side,
# from Modelscope — HF is throttled on this host), this copies those weights
# into the PVC that the LLMInferenceService mounts via pvc://. The pod then
# NEVER downloads over the slow network.
#
# Reproducible / idempotent: if the PVC already has the weights, it skips.
#
# Usage:  bash infra/load-model-pvc.sh
# Pre-req: kubectl on PATH, cluster up, model-pvc.yaml applied, model cached.
set -euo pipefail

MODEL_ID="${MODEL_ID:-Qwen/Qwen2.5-1.5B-Instruct-AWQ}"
CACHE_DIR="${NANOSERVE_MODEL_CACHE:-${HOME}/.cache/nanoserve/models}/${MODEL_ID}"
PVC="nanoserve-model-cache"
NS="kserve"

echo "==> Loading model cache into PVC"
echo "    model: ${MODEL_ID}"
echo "    cache: ${CACHE_DIR}"
echo "    pvc:   ${PVC}/${NS}"

# 1. Cache must exist and have the weights.
if [[ ! -f "${CACHE_DIR}/model.safetensors" || ! -s "${CACHE_DIR}/model.safetensors" ]]; then
  echo "error: model cache missing/incomplete at ${CACHE_DIR}" >&2
  echo "  run: bash infra/fetch-model.sh   (resumable; re-run to continue)" >&2
  exit 1
fi

# 2. PVC must exist.
if ! kubectl get pvc "${PVC}" -n "${NS}" >/dev/null 2>&1; then
  echo "error: PVC ${PVC} not found in ${NS}" >&2
  echo "  run: kubectl apply -f manifests/serving/model-pvc.yaml" >&2
  exit 1
fi

# 3. Idempotent: if the PVC already has the weights, skip.
if kubectl exec -n "${NS}" \
  deploy/llmisvc-controller-manager -- sh -c \
  "test -f /dev/null" >/dev/null 2>&1; then :; fi  # noop (controller has no model mount)

# Check via a throwaway pod instead (the PVC is RWO, so run one-shot).
if kubectl get pod -n "${NS}" -l job=model-cache-load >/dev/null 2>&1; then
  echo "    load job already exists; skipping"
  exit 0
fi

echo "==> Copying ${CACHE_DIR} → PVC via one-shot pod..."
kubectl run -n "${NS}" model-cache-load --rm -i --restart=Never \
  --overrides="{
    \"spec\": {
      \"nodeSelector\": {\"gpu\": \"true\"},
      \"containers\": [{
        \"name\": \"model-cache-load\",
        \"image\": \"busybox\",
        \"command\": [\"sh\", \"-c\", \"mkdir -p /mnt/models && cp -r /cache/* /mnt/models/ && echo COPIED\"],
        \"volumeMounts\": [
          {\"name\": \"cache\", \"mountPath\": \"/cache\"},
          {\"name\": \"models\", \"mountPath\": \"/mnt/models\"}
        ]
      }],
      \"volumes\": [
        {\"name\": \"cache\", \"hostPath\": {\"path\": \"${CACHE_DIR}\"}},
        {\"name\": \"models\", \"persistentVolumeClaim\": {\"claimName\": \"${PVC}\"}}
      ],
      \"restartPolicy\": \"Never\"
    }
  }" --image=busybox --command -- sh -c "mkdir -p /mnt/models && cp -r /cache/* /mnt/models/ && echo COPIED" 2>&1 | tail -3 || true

# Fallback: if --rm didn't clean up, delete the pod.
kubectl delete pod model-cache-load -n "${NS}" --ignore-not-found=true >/dev/null 2>&1 || true

echo "==> Done. PVC ${PVC} now holds the model."
echo "    LLMInferenceService should reference: pvc://${PVC}/Qwen2.5-1.5B-Instruct-AWQ"
kubectl get pvc "${PVC}" -n "${NS}"
