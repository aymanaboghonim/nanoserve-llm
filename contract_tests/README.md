# Agent Conformance Suite (`contract_tests/`)

Decoupled, portable checks that the NanoServe gateway contract holds against a
deployment. This suite is intentionally independent of this repository's
internals — it imports only `openai` + `httpx` and reads plain env vars — so a
consumer (e.g. the Arabic Legal RAG Agent) can run it against any
endpoint (localhost or LAN) as its own acceptance gate.

## What it verifies

| File | Contract |
| --- | --- |
| `test_health_and_models.py` | `/healthz` → 200 (fast, empty body); `/v1/models` lists the served model |
| `test_chat_contract.py` | non-stream OpenAI shape (`chatcmpl-*`, content, usage, model echo); **Arabic** content round-trip; SSE stream (delta chunks, low TTFT, `finish_reason`); **raw SSE framing** (`data:` lines + `[DONE]`) |
| `test_tool_contract.py` | tool-call returns OpenAI `tool_calls` schema (function + JSON-string args); full LangGraph-style round trip with a `tool` result |
| `test_thinking_contract.py` | `enable_thinking: false` → content-only; `true` → `reasoning` trace + content (with budget) |

## Run

```bash
export NANOSERVE_CONTRACT_LIVE=1                       # gate (off => all skipped)
export NANOSERVE_BASE_URL=http://localhost:8000/v1     # default; or http://<LB>:8000/v1
export NANOSERVE_MODEL=ibm-granite/granite-4.2-3b      # default
uv run --group dev pytest contract_tests -v            # from this repo
# or:  make contract-test
```

Plain `pytest` (repo default testpaths: `tests`, `evals`) never collects this
directory — the live gate + separate path keep CI hermetic.
