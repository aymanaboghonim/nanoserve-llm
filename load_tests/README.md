# Load Testing (Locust)

Stress-test suite for the vLLM OpenAI-compatible endpoint (`/v1/chat/completions`).

## Prerequisites

- A live serving plane (Phase 3+) reachable at `NANOSERVE_BASE_URL`.
- The `load` dependency group:

```bash
uv sync --all-groups        # or: uv sync --group load
```

## Run

Interactive (web UI at http://localhost:8089):

```bash
uv run --group load locust -f load_tests/locustfile.py --host http://localhost:8000
```

Headless with CSV output (via the serving gateway):

```bash
make load   # resolves the gateway LB, runs locust headless 8u/1/s/2m

# or manually:
uv run --group load locust -f load_tests/locustfile.py \
  --host http://$(kubectl get gateway kserve-gateway -n kserve -o jsonpath='{.status.addresses[0].value}') \
  --headless -u 8 -r 1 -t 2m --csv .locust/run
```

Realistic stress run with a ramp -> plateau -> end-of-window spike (see Notes):

```bash
make stress  # shape: ramp 0->10u over 10s, hold 10u 60s, spike to 28u over 30s

# or manually (shape is only active with NANOSERVE_LOAD_SHAPE=1):
NANOSERVE_LOAD_SHAPE=1 uv run --group load locust -f load_tests/locustfile.py \
  --host http://localhost:8000 --headless --csv .locust/stress
```

## Configuration

All knobs are env-driven (see `.env.example`):

| Env var | Default | Meaning |
| --- | --- | --- |
| `NANOSERVE_BASE_URL` | `http://localhost:8000/v1` | API base URL |
| `NANOSERVE_MODEL` | `ibm-granite/granite-4.2-3b` | Model name in payloads |
| `NANOSERVE_API_KEY` | `EMPTY` | Bearer token |
| `NANOSERVE_LOAD_USERS` | `8` | Peak concurrent users |
| `NANOSERVE_LOAD_SPAWN_RATE` | `1` | Users spawned per second |
| `NANOSERVE_LOAD_RUN_TIME` | `1m` | Headless duration |
| `NANOSERVE_LOAD_MAX_TOKENS` | `64` | `max_tokens` per request |
| `NANOSERVE_LOAD_PROMPT` | KV-cache question | Prompt sent by every user |
| `NANOSERVE_LOAD_THINKING` | `0` | `1` enables Granite-4.2 thinking (reasoning field) |
| `NANOSERVE_LOAD_TURNS` | `3` | Multi-turn turns per session before reset |
| `NANOSERVE_LOAD_SHAPE` | `0` | `1` uses the ramp/plateau/spike `LoadTestShape` |
| `NANOSERVE_LOAD_SPIKE_USERS` | `28` | Shape: end-of-window spike user count |

## Notes

- The suite uses `FastHttpUser` (geventhttpclient) so Locust is never the
  bottleneck at high concurrency.
- Traffic mixes one-shot users (weight 6), RAG-style multi-turn agents
  (weight 40, `@seq_task` cycles with a unique long context per user), and a
  few `x-user-id` burst users (weight 3) that intentionally trip the gateway's
  per-user 20 req/min limit (HTTP 429 — recorded, not failed; watch the
  dashboard rejection panel).
- Defaults are sized for the 3B Granite AWQ model on a ~6.4GB VRAM budget
  (0.80 x 8GB; fp8 KV pool ~97K tokens) — raise `-u`/`-r` gradually while
  watching `vllm:kv_cache_usage_perc` (Phase 4) to find the throughput ceiling
  without OOM.
- Thinking is OFF by default (the template defaults it ON and thinking tokens
  starve the 64-token output budget); set `NANOSERVE_LOAD_THINKING=1` + a
  higher `max_tokens` to exercise the reasoning path under load.
- The `make stress` shape is the realistic load curve: a 10 s ramp to a
  sustained 10-user plateau, then an **end-of-window spike to 28 users** that
  actually fills the fp8 KV pool and forms a scheduler queue
  (`--max-num-seqs 16`). Steady low concurrency never does — the earlier 8u/2m
  smoke run kept `vllm:kv_cache_usage_perc` near 0% with no queueing.
- Response validity is asserted (status < 400, valid JSON, non-empty content;
  429s are recorded as expected, not failures) — malformed responses count as
  failures.
- Without a live endpoint, every request fails with `ConnectionError`; that is
  expected until Phase 3 lands.
