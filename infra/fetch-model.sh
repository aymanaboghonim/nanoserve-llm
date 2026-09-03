#!/usr/bin/env bash
# NanoServe model cache downloader.
#
# HuggingFace is pathologically slow from this host (~1 KB/s), so model
# weights are fetched from Modelscope (which mirrors Qwen et al. and sustains
# ~100+ KB/s) into a LOCAL CACHE. The KServe storage-initializer then mounts
# this cache (pvc://) instead of re-downloading over HF in the pod.
#
# Reproducible: idempotent — if the model files already exist in the cache,
# this exits 0 without downloading. Run `bash infra/fetch-model.sh` to
# populate/refresh ~/.cache/nanoserve/models/<model>.
#
# Usage:
#   bash infra/fetch-model.sh                          # default model
#   MODEL_ID=Qwen/Qwen2.5-1.5B-Instruct-AWQ bash infra/fetch-model.sh
set -euo pipefail

MODEL_ID="${MODEL_ID:-Qwen/Qwen2.5-1.5B-Instruct-AWQ}"
REVISION="${REVISION:-master}"
CACHE_ROOT="${NANOSERVE_MODEL_CACHE:-${HOME}/.cache/nanoserve/models}"
DEST="${CACHE_ROOT}/${MODEL_ID}"
BASE_URL="https://modelscope.cn/models/${MODEL_ID}/resolve/${REVISION}"

echo "==> NanoServe model fetch"
echo "    model:   ${MODEL_ID}"
echo "    source:  Modelscope (HF is throttled on this host)"
echo "    dest:    ${DEST}"

# If the safetensors weights already exist AND match the expected size,
# treat as cached. (A partial download must NOT be treated as complete.)
EXPECTED_SIZE="${EXPECTED_SIZE:-1614553840}"  # Qwen2.5-1.5B-Instruct-AWQ model.safetensors
if [[ -f "${DEST}/model.safetensors" ]]; then
  ACTUAL_SIZE="$(stat -c%s "${DEST}/model.safetensors" 2>/dev/null || echo 0)"
  if [[ "${ACTUAL_SIZE}" -ge "${EXPECTED_SIZE}" ]]; then
    echo "    ✓ model weights already cached (${ACTUAL_SIZE} bytes) — nothing to do."
    du -sh "${DEST}" | awk '{print "    cache size:", $1}'
    exit 0
  else
    echo "    ⚠ partial download (${ACTUAL_SIZE}/${EXPECTED_SIZE} bytes) — resuming..."
  fi
fi

mkdir -p "${DEST}"

echo "==> Fetching file manifest..."
FILES="$(
  timeout 20 curl -s \
    "https://modelscope.cn/api/v1/models/${MODEL_ID}/repo/files?Revision=${REVISION}&Recursive=true" \
  | python3 -c "
import json,sys
d=json.load(sys.stdin)
for f in d.get('Data',{}).get('Files',[]):
    p=f.get('Path','')
    if not p.startswith('.') and p != 'README.md':
        print(p)
"
)"

echo "==> Downloading ${MODEL_ID} (resumable; Ctrl-C safe — re-run to continue)..."
for f in ${FILES}; do
  mkdir -p "${DEST}/$(dirname "${f}")"
  echo "    ${f}"
  # --continue resumes partial downloads; --tries for transient failures.
  wget -q --continue --tries=5 --timeout=30 \
    -O "${DEST}/${f}" "${BASE_URL}/${f}"
done

echo "==> Done."
du -sh "${DEST}" | awk '{print "    cache size:", $1}'
echo "    Cache is loaded. Point the LLMInferenceService storageUri at:"
echo "    pvc://nanoserve-model-cache/$(basename "${MODEL_ID}")"
