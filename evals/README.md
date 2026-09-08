# Evaluation Suite (DeepEval — LLM-as-judge)

Deterministic, programmatic evaluation of the served model's responses:
endpoint **contract** tests (status, JSON shape, usage, latency) plus **quality**
tests via DeepEval's G-Eval metric, judged by the served model itself — fully
local, no torch, no external APIs.

## Prerequisites

- The `eval` dependency group:
  ```bash
  uv sync --group eval
  ```
- A live endpoint and `NANOSERVE_EVAL_LIVE=1`:
  ```bash
  export NANOSERVE_EVAL_LIVE=1
  ```

## Run

Without a live endpoint, every test here **skips** cleanly:

```bash
uv run --group eval pytest evals -v
# -> 9 skipped (e2e: requires a live endpoint; set NANOSERVE_EVAL_LIVE=1)
```

With a live endpoint (via the serving gateway — exercises Envoy + EPP):

```bash
make eval   # resolves the gateway LB, sets NANOSERVE_BASE_URL/NANOSERVE_EVAL_LIVE, runs pytest

# or manually:
export NANOSERVE_BASE_URL=http://<gateway-ip>/v1
#   e.g. export NANOSERVE_BASE_URL=$(kubectl get gateway kserve-gateway -n kserve -o jsonpath='{.status.addresses[0].value}')/v1
export NANOSERVE_EVAL_LIVE=1
uv run --group eval pytest evals -v
```

## What's asserted

| File | Checks |
| --- | --- |
| `test_endpoint_contract.py` | HTTP 200, `choices[].message.content` non-empty, valid `finish_reason`, `usage` (prompt+completion=total), model echo, `object`/`id` shape, latency < 30s |
| `test_quality.py` | G-Eval (clarity/correctness/relevance) score ≥ `NANOSERVE_EVAL_THRESHOLD` (0.5), deterministic stability of scores |

## Configuration

| Env var | Default | Meaning |
| --- | --- | --- |
| `NANOSERVE_BASE_URL` | `http://localhost:8000/v1` | Endpoint under test |
| `NANOSERVE_API_KEY` | `EMPTY` | Auth for the endpoint |
| `NANOSERVE_MODEL` | `ibm-granite/granite-4.2-3b` | Model under test |
| `NANOSERVE_EVAL_JUDGE_MODEL` | same as model | Judge model (self-judging by default) |
| `NANOSERVE_EVAL_THRESHOLD` | `0.5` | Pass threshold for G-Eval |
| `NANOSERVE_EVAL_LIVE` | `0` | `1` to run e2e tests |

## Notes

- Self-judging (judge == served model) is intentional for a single-GPU box:
  it keeps everything local. Swap `NANOSERVE_EVAL_JUDGE_MODEL` for a stronger
  model if you later host one.
- No torch/sentence-transformers needed: only LLM-as-judge metrics are used
  (DeepEval lazy-imports embeddings, so the base + eval envs stay light).
