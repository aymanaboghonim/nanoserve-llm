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

## Configuration

All knobs are env-driven (see `.env.example`):

| Env var | Default | Meaning |
| --- | --- | --- |
| `NANOSERVE_BASE_URL` | `http://localhost:8000/v1` | API base URL |
| `NANOSERVE_MODEL` | `Qwen/Qwen2.5-1.5B-Instruct-AWQ` | Model name in payloads |
| `NANOSERVE_API_KEY` | `EMPTY` | Bearer token |
| `NANOSERVE_LOAD_USERS` | `8` | Peak concurrent users |
| `NANOSERVE_LOAD_SPAWN_RATE` | `1` | Users spawned per second |
| `NANOSERVE_LOAD_RUN_TIME` | `1m` | Headless duration |
| `NANOSERVE_LOAD_MAX_TOKENS` | `64` | `max_tokens` per request |
| `NANOSERVE_LOAD_PROMPT` | KV-cache question | Prompt sent by every user |

## Notes

- Defaults are sized for a ~1.5B AWQ model on a 4GB KV-cache budget — raise
  `-u`/`-r` gradually while watching `vllm:kv_cache_usage_perc` (Phase 4) to find
  the throughput ceiling without OOM.
- Response validity is asserted (status < 400, valid JSON, non-empty content) —
  malformed responses are counted as failures.
- Without a live endpoint, every request fails with `ConnectionError`; that is
  expected until Phase 3 lands.
