#!/usr/bin/env bash
# NanoServe out-of-pod model downloader (host-cache isolation).
#
# WHY out-of-pod: downloading multi-GB weights inside the K3s pod during vLLM
# startup risks network throttling, memory spikes, and startup-probe timeouts.
# This script downloads on the HOST and materializes a flat, symlink-free
# directory at:
#
#   ${NANOSERVE_MODEL_CACHE:-$HOME/.cache/nanoserve/models}/<MODEL_ID>
#
# The static hostPath PV (manifests/serving/model-pv.yaml) points at that flat
# dir; KServe mounts it at /mnt/models via pvc://nanoserve-model-cache. The pod
# NEVER downloads over the network.
#
# Default model: ibm-granite/granite-4.2-3b-fp8 (official FP8 compressed-tensors
# checkpoint served via vLLM Marlin W8A16 on Ampere).
#
# Download modes:
#   MODE=hub (default): huggingface_hub snapshot_download into the standard HF
#     hub cache (~/.cache/huggingface/hub), then cp -aL-materialize (blob
#     symlinks break bind mounts). Works on healthy networks. On auth/repo
#     failure it falls through to MODE=curl automatically.
#   MODE=curl: parallel-range `curl` straight into the flat dir (no hub
#     staging) — the fast path for ISPs that throttle huggingface.co and for
#     mirrors that only serve byte-ranges quickly (e.g. hf-mirror.com).
#
# Mirror fallback (throttled-ISP support):
#   HF_ENDPOINT=https://hf-mirror.com make download-model
#
# Usage:
#   make download-model   # default: ibm-granite/granite-4.2-3b-fp8
#   MODEL_ID=<org>/<model> make download-model   # (alt models / rollback)
#
# Env:
#   HF_HOME / HF_HUB_CACHE   hub cache location (MODE=hub)
#   HF_TOKEN                 optional (public models need none)
#   HF_ENDPOINT              mirror base, e.g. https://hf-mirror.com
#   DOWNLOAD_MODE            hub | curl (default hub w/ curl fallback)
#   NANOSERVE_MODEL_CACHE    flat-dir root (default ~/.cache/nanoserve/models)
#   CONNS                    parallel range connections (MODE=curl, default 8)
#   FORCE=1                  re-download even if weights look cached
#
# Idempotent: if the flat dir already holds a >=MIN_WEIGHTS_BYTES
# model.safetensors the script exits 0 without touching the network.
set -euo pipefail

MODEL_ID="${MODEL_ID:-ibm-granite/granite-4.2-3b-fp8}"
HF_CACHE="${HF_HOME:-${HOME}/.cache/huggingface}"
FLAT_ROOT="${NANOSERVE_MODEL_CACHE:-${HOME}/.cache/nanoserve/models}"
DEST="${FLAT_ROOT}/${MODEL_ID}"
HF_BASE="${HF_ENDPOINT:-https://huggingface.co}"
CONNS="${CONNS:-8}"
MODE="${DOWNLOAD_MODE:-hub}"

# Model repo file set. The Granite-4.2 FP8 checkpoint is a single
# model.safetensors (4.18GB) plus config/tokenizer/chat-template and IBM's
# granite_thinking_parser.py plugin. .gitattributes/model.sig/recipe.yaml are
# intentionally skipped (not needed to serve).
FILES=(config.json chat_template.jinja granite_thinking_parser.py \
       generation_config.json special_tokens_map.json tokenizer.json \
       tokenizer_config.json merges.txt vocab.json model.safetensors)

# Weights floor: Granite FP8 is a 4.18GB single file -> 3.5GiB floor.
MIN_WEIGHTS_BYTES=$(( (1024 * 1024 * 1024 * 7) / 2 ))  # 3.5GiB

err() { echo "error: $*" >&2; }

verify_flat() { # gate: weights must be present and >=MIN_WEIGHTS_BYTES
  local size
  size="$(stat -c%s "${DEST}/model.safetensors" 2>/dev/null || echo 0)"
  if [[ "${size}" -lt "${MIN_WEIGHTS_BYTES}" ]]; then
    err "materialized model.safetensors too small (${size} bytes) — re-run (resumable)."
    exit 1
  fi
}

# --- MODE=curl helpers -----------------------------------------------------
# (HF resolve URLs 308-redirect to short-lived token-signed Xet URLs; see
# fetch_file below — each range request re-signs via -L so no single signature
# bounds the whole download.)

fetch_file() { # fetch_file <url> <outfile> — range-parallel with per-conn signing
  local url="$1" out="$2" total
  # HEAD-resolve ONCE to learn the size. NOTE: HF resolve URLs 308-redirect to
  # a SHORT-LIVED token-signed Xet/CDN URL (~10 min). We must NOT pin that
  # signed URL for the whole download — it expires mid-flight and truncates
  # every in-flight part. Instead each range request below targets the resolve
  # URL with -L -r, so curl re-signs (fresh signed URL) per connection and the
  # range is re-applied through the redirect (verified: 206 from the bridge).
  total="$(curl -sIL --max-time 30 "${url}" 2>/dev/null | awk -v IGNORECASE=1 \
    '/^content-length:/{gsub("\r",""); len=$2} END{print len}')"
  if [[ -z "${total}" || "${total}" -lt 1048576 ]]; then
    # No usable Content-Length — plain single-connection download (small files).
    curl -sL --retry 5 --retry-delay 2 --max-time 300 -o "${out}" "${url}" || return 1
    return 0
  fi
  local i start end chunk
  echo "    resolved ${total} bytes"
  rm -f "${out}".part-*
  chunk=$((total / CONNS))
  for i in $(seq 0 $((CONNS - 1))); do
    start=$((i * chunk))
    if [[ "${i}" -eq $((CONNS - 1)) ]]; then end=$((total - 1)); else end=$((start + chunk - 1)); fi
    ( curl -sL --retry 5 --retry-delay 2 --max-time 900 \
        -o "${out}.part-${i}" -r "${start}-${end}" "${url}" ) &
  done
  wait
  cat "${out}".part-* > "${out}"
  rm -f "${out}".part-*
}

download_curl() { # parallel-range fetch straight into the flat dir
  echo "==> curl mode: ${HF_BASE} (${CONNS} parallel range conns)"
  rm -rf "${DEST}"
  mkdir -p "${DEST}"
  for f in "${FILES[@]}"; do
    if [[ "${f}" == "model.safetensors" ]]; then
      echo "    downloading model.safetensors (range-parallel, resumable)..."
      fetch_file "${HF_BASE}/${MODEL_ID}/resolve/main/${f}" "${DEST}/${f}" \
        || err "model.safetensors download failed"
    else
      # Small files: plain GET; tolerate 404 (some repos omit files).
      curl -sL --retry 3 --max-time 60 -o "${DEST}/${f}" \
        "${HF_BASE}/${MODEL_ID}/resolve/main/${f}" 2>/dev/null || rm -f "${DEST}/${f}"
    fi
  done
  verify_flat
}

# --- main ------------------------------------------------------------------
echo "==> NanoServe out-of-pod model download"
echo "    model:        ${MODEL_ID}"
echo "    flat dest:    ${DEST}"
echo "    hf endpoint:  ${HF_BASE}"
echo "    mode:         ${MODE}"

# 1. Idempotency gate — flat dir already populated.
if [[ -f "${DEST}/model.safetensors" ]]; then
  SIZE="$(stat -c%s "${DEST}/model.safetensors" 2>/dev/null || echo 0)"
  if [[ "${SIZE}" -ge "${MIN_WEIGHTS_BYTES}" ]] && [[ "${FORCE:-0}" != "1" ]]; then
    echo "    ✓ weights already materialized ($(numfmt --to=iec "${SIZE}") bytes) — nothing to do."
    du -sh "${DEST}" | awk '{print "    flat dir size:", $1}'
    exit 0
  fi
  echo "    ⚠ re-downloading (FORCE=1 or partial copy)."
fi

# 2. MODE=hub: snapshot_download into the hub cache, then materialize.
#    Token-scrubbed: a stale OAuth token under HF_HOME would 401 public repos,
#    so we run anonymous (fresh temp HOME) unless HF_TOKEN is explicitly set.
if [[ "${MODE}" == "hub" ]]; then
  echo "==> Downloading to hub cache (resumable, snapshot_download)..."
  mkdir -p "${HF_CACHE}/hub"
  ANON_HOME="$(mktemp -d "${TMPDIR:-/tmp}"/hf-anon.XXXXXX)"
  if HF_HOME="${ANON_HOME}" MODEL_ID="${MODEL_ID}" HF_CACHE_DIR="${HF_CACHE}/hub" \
     uv run --group model python -c "
import os
from huggingface_hub import snapshot_download

snapshot_download(
    repo_id=os.environ['MODEL_ID'],
    cache_dir=os.environ['HF_CACHE_DIR'],
    token=os.environ.get('HF_TOKEN') or None,
)
"; then
    rm -rf "${ANON_HOME}"
    MODEL_KEY="models--${MODEL_ID//\//--}"
    SNAPSHOT=""
    if [[ -f "${HF_CACHE}/hub/${MODEL_KEY}/refs/main" ]]; then
      REV="$(tr -d '\n' < "${HF_CACHE}/hub/${MODEL_KEY}/refs/main")"
      [[ -d "${HF_CACHE}/hub/${MODEL_KEY}/snapshots/${REV}" ]] && SNAPSHOT="${HF_CACHE}/hub/${MODEL_KEY}/snapshots/${REV}"
    fi
    if [[ -z "${SNAPSHOT}" ]]; then
      SNAPSHOT="$(ls -1dt "${HF_CACHE}/hub/${MODEL_KEY}"/snapshots/*/ 2>/dev/null | head -1 || true)"
      SNAPSHOT="${SNAPSHOT%/}"
    fi
    if [[ -z "${SNAPSHOT}" || ! -d "${SNAPSHOT}" ]]; then
      err "no snapshot under ${HF_CACHE}/hub — falling back to curl mode"
      download_curl
    else
      echo "    snapshot:   ${SNAPSHOT}"
      echo "==> Materializing flat dir (symlink-deref)..."
      rm -rf "${DEST}"
      mkdir -p "${DEST}"
      cp -aL "${SNAPSHOT}/." "${DEST}/"
      verify_flat
    fi
  else
    rm -rf "${ANON_HOME}"
    echo "    ⚠ hub download failed (auth/network) — falling back to curl mode."
    download_curl
  fi
else
  download_curl
fi

echo "==> Done."
du -sh "${DEST}" | awk '{print "    flat dir size:", $1}'
echo ""
echo "    Next (hostPath PV target — manifests/serving/model-pv.yaml):"
echo "    ${DEST}"
echo ""
echo "    Apply the static PV/PVC + LLMISVC with:"
echo "    kubectl delete pvc nanoserve-model-cache -n kserve  # old local-path claim"
echo "    kubectl apply -f manifests/serving/model-pv.yaml manifests/serving/model-pvc.yaml"
echo "    make apply"
