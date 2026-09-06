#!/usr/bin/env bash
# NanoServe model cache downloader.
#
# HuggingFace's plain CDN is throttled on this host (~1 KB/s), but the
# token-signed AWS Xet-bridge URL is fast (~400 KB/s/conn) AND parallelizes
# (~1.5 MB/s aggregate). This fetches model weights from HF via the signed URL
# with parallel range connections into a LOCAL CACHE. KServe mounts this cache
# (pvc://) instead of re-downloading in the pod.
#
# Reproducible / idempotent: if model.safetensors already matches the expected
# size, this exits 0 without downloading. Partial downloads are resumed.
#
# Token: read from $HF_TOKEN (export it in your shell, or put HF_TOKEN=... in
# a gitignored .env). NEVER commit the token.
#
# Usage:
#   export HF_TOKEN=hf_xxx   # in your shell (never in the repo)
#   bash infra/fetch-model.sh
set -euo pipefail

MODEL_ID="${MODEL_ID:-Qwen/Qwen2.5-1.5B-Instruct-AWQ}"
CACHE_ROOT="${NANOSERVE_MODEL_CACHE:-${HOME}/.cache/nanoserve/models}"
DEST="${CACHE_ROOT}/${MODEL_ID}"
HF_BASE="https://huggingface.co/${MODEL_ID}/resolve/main"
CONNS="${CONNS:-8}"   # parallel range connections

echo "==> NanoServe model fetch"
echo "    model:  ${MODEL_ID}"
echo "    source: HuggingFace (token-signed Xet-bridge, ${CONNS} parallel conns)"
echo "    dest:   ${DEST}"

if [[ -z "${HF_TOKEN:-}" ]]; then
  echo "error: HF_TOKEN not set (export HF_TOKEN=hf_xxx in your shell first)." >&2
  exit 1
fi

mkdir -p "${DEST}"

# Expected size of the main weights file (bytes). Update if the model changes.
EXPECTED_SIZE="${EXPECTED_SIZE:-1614553840}"

# Idempotent: if the weights exist AND match the expected size, we're done.
if [[ -f "${DEST}/model.safetensors" ]]; then
  ACTUAL="$(stat -c%s "${DEST}/model.safetensors" 2>/dev/null || echo 0)"
  if [[ "${ACTUAL}" -ge "${EXPECTED_SIZE}" ]]; then
    echo "    ✓ model weights already cached (${ACTUAL} bytes) — nothing to do."
    du -sh "${DEST}" | awk '{print "    cache size:", $1}'
    exit 0
  else
    echo "    ⚠ partial (${ACTUAL}/${EXPECTED_SIZE}) — re-downloading full file via parallel conns."
  fi
fi

echo "==> Fetching signed URL (token)..."
SIGNED="$(
  curl -sI -H "Authorization: Bearer ${HF_TOKEN}" "${HF_BASE}/model.safetensors" \
  | grep -i "^location:" | sed 's/^[Ll]ocation: //' | tr -d '\r'
)"
if [[ -z "${SIGNED}" ]]; then
  echo "error: could not obtain signed URL (token invalid?)" >&2
  exit 1
fi
echo "    signed URL OK (${#SIGNED} chars)"

echo "==> Downloading model.safetensors (${CONNS} parallel range conns)..."
rm -f "${DEST}/model.safetensors.part-"*
CHUNK=$((EXPECTED_SIZE / CONNS))
for i in $(seq 0 $((CONNS - 1))); do
  START=$((i * CHUNK))
  END=$((i == CONNS - 1 ? EXPECTED_SIZE - 1 : START + CHUNK - 1))
  ( curl -s --retry 5 --retry-delay 2 \
      -o "${DEST}/model.safetensors.part-${i}" \
      -r "${START}-${END}" "${SIGNED}" ) &
done
wait
cat "${DEST}"/model.safetensors.part-* > "${DEST}/model.safetensors"
rm -f "${DEST}/model.safetensors.part-"*

# Verify the stitched file size.
if [[ "$(stat -c%s "${DEST}/model.safetensors")" -lt "${EXPECTED_SIZE}" ]]; then
  echo "error: stitched size mismatch — re-run to retry." >&2
  exit 1
fi
echo "    ✓ model.safetensors complete ($(stat -c%s "${DEST}/model.safetensors") bytes)"

echo "==> Fetching config/tokenizer files..."
for f in config.json generation_config.json tokenizer.json tokenizer_config.json \
         merges.txt vocab.json; do
  curl -s --retry 3 -o "${DEST}/${f}" "${HF_BASE}/${f}" || true
done

echo "==> Done."
du -sh "${DEST}" | awk '{print "    cache size:", $1}'
echo "    Load into the PVC next:"
echo "    bash infra/load-model-pvc.sh"
echo "    (LLMInferenceService already references pvc://nanoserve-model-cache)"
