# Agent Integration Contract — NanoServe Gateway API

**Status:** v1.0 (2026-09-08) · **Platform:** single-node K3s/KServe/vLLM (IBM
Granite 4.2-3B AWQ INT4, RTX 3070 8GB) · **Consumers:** downstream agents
(e.g. the Arabic Legal RAG Agent — FastAPI + LangGraph, Python 3.12 asyncio).

This document is the **authoritative contract** for consuming NanoServe as an
external API platform. It is deliberately decoupled from this repository's
internals: a consumer needs only a base URL, the model id, and the OpenAI SDK.
The machine-readable spec lives at [`contracts/openapi.yaml`](../contracts/openapi.yaml);
the portable conformance suite lives at [`contract_tests/`](../contract_tests/).

---

## 1. Access model (configurable base URL)

| Surface | URL |
| --- | --- |
| OpenAI-compatible API | `http://localhost:8000/v1` (same host) · `http://<gateway-LB>:8000/v1` (LAN) |
| Liveness probe | `http://<host>:8000/healthz` — note: at the **root**, not under `/v1` |

`<gateway-LB>` is the Envoy Gateway load-balancer address (resolved from the
cluster; it can change when the host restarts). Configure it once in the agent
as an environment/constructor value — the contract itself is host-agnostic.

**Auth:** none required. vLLM's default API key is `EMPTY`; the OpenAI SDK
sends `Authorization: Bearer EMPTY` by default — leave it as-is.

---

## 2. Client setup (AsyncOpenAI)

```python
from openai import AsyncOpenAI

client = AsyncOpenAI(
    base_url="http://localhost:8000/v1",  # or the LAN LB URL
    api_key="EMPTY",  # vLLM default; no real auth
    timeout=120.0,  # generous: shared 8GB GPU, queueing
    max_retries=2,
)

MODEL = "ibm-granite/granite-4.2-3b"  # EXACT served name (see /v1/models)
```

> Do **not** send an `x-user-id` header: the gateway rate-limits per that
> header (20 req/min → 429) and it exists only as a load-test lever. The agent
> should rate-limit **client-side** (e.g. `aiolimiter`) instead.

---

## 3. Health gating

Probe before issuing work and after idle gaps; treat non-200 as "not ready"
(retry with backoff rather than failing hard):

```python
import httpx


async def platform_ready(base: str) -> bool:
    async with httpx.AsyncClient(timeout=5.0) as c:
        r = await c.get(f"{base}/healthz")  # root URL, NOT /v1
        return r.status_code == 200  # body is EMPTY — status is the contract
```

Contract: 200 **with an empty body** (newer vLLM `/health` drops the text).
Answered well under 5 s (local baseline ~60 ms) — no buffering.

> **SDK note (important, applies to all samples below):** the OpenAI SDK
> rejects `chat_template_kwargs` as a named argument. Pass it via
> `extra_body` (the SDK's passthrough):
> `extra_body={"chat_template_kwargs": {"enable_thinking": False}}`.
> When calling the API **raw** (curl/httpx), `chat_template_kwargs` is a
> plain top-level JSON field — the server accepts it either way.

---

## 4. Chat completions

### 4.1 Non-stream

```python
resp = await client.chat.completions.create(
    model=MODEL,
    messages=[
        {"role": "system", "content": "أنت مساعد قانوني..."},  # Arabic is first-class
        {"role": "user", "content": "ما هي مواد القانون...؟"},
    ],
    max_tokens=1024,
    temperature=0.0,  # deterministic legal output
    extra_body={"chat_template_kwargs": {"enable_thinking": False}},  # ← explicit (see §5)
)
msg = resp.choices[0].message
answer = msg.content  # final answer text
reasoning = msg.reasoning  # present ONLY when thinking enabled
```

Response is a standard OpenAI `chat.completion`: `id`, `choices[0].message`,
`choices[0].finish_reason` (`stop` | `length`), `usage` (prompt/completion/
total tokens), `model` echoes the request model.

### 4.2 Streaming (SSE) — LangGraph token streaming

```python
stream = await client.chat.completions.create(
    model=MODEL,
    messages=[...],
    stream=True,
    temperature=0.0,
    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
)
async for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        yield chunk.choices[0].delta.content
```

Wire `AsyncOpenAI`'s async stream directly into your `astream_events`/SSE
response. First-token latency locally is ~60 ms (idle); the gateway does **not**
buffer (no RequestBuffer policy). Terminator chunk carries
`choices[0].finish_reason`; the stream ends after `data: [DONE]`.

### 4.3 Tool calling (OpenAI schema, `qwen3_coder` parser)

```python
tools = [
    {
        "type": "function",
        "function": {
            "name": "search_law_articles",
            "description": "Search Saudi legal articles by keyword.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search terms (Arabic OK)."},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }
]

resp = await client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user", "content": "ابحث عن مواد التعويض"}],
    tools=tools,
    tool_choice="auto",
    temperature=0.0,
    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
)
tc = resp.choices[0].message.tool_calls[0]
name, args_json = tc.function.name, tc.function.arguments  # arguments is a JSON string
```

Standard LangGraph `bind_tools` round-trip works: return the assistant
`tool_calls` message, append a `{"role": "tool", "tool_call_id": tc.id, "content": result}`
message, call again. `arguments` is a **JSON string** — parse before use.
Tool-call content assertions: type `function`, valid JSON args.

---

## 5. Thinking (Granite 4.2 reasoning) — request-controlled

The model's chat template **defaults `enable_thinking=true`**. A thinking trace
is genuinely useful for complex legal reasoning but it **consumes the
`max_tokens` budget** and can leave `content` empty on short generations.

| Mode | Request | Response |
| --- | --- | --- |
| Content-only (default for this consumer) | `chat_template_kwargs: {"enable_thinking": False}` | `content` = answer; no `reasoning` |
| With reasoning | `chat_template_kwargs: {"enable_thinking": True}` | `content` = final answer **and** `reasoning` = think trace |

Rules of thumb for the agent:
- Always send the explicit `chat_template_kwargs` — never rely on the default.
- Simple retrieval / tool calls: thinking off (fast, deterministic).
- Ambiguous multi-article or synthesis questions: thinking on; allow
  `max_tokens >= 256` headroom above the expected answer length.
- The answer the user should see is always `content`. Treat `reasoning` as
  optional chain-of-thought (some agents surface it in a "thinking" UI block;
  never pass it to a judge as the answer).
- (This build names the field `reasoning`. Older Granite/vLLM builds used
  `reasoning_content` — if you must support both, read whichever is present.)

---

## 6. Sampling guidance

- General chat: `temperature 1.0, top_p 0.95` (IBM's Granite 4.2 prescription).
- Deterministic structured output / tool-call routing / evals:
  `temperature 0.0`.

---

## 7. Errors and resilience

| Code | Meaning | Agent behavior |
| --- | --- | --- |
| `400` | Malformed request or validation (OpenAI `{"error": {...}}`). | Fix payload; log body for debugging. |
| `429` | **Only** if `x-user-id` sent (>20 req/min). | Don't send the header; client-side `aiolimiter`. |
| `503` | Admission-control shedding while the local engine recovers (KV saturation). | Exponential backoff retry (the OpenAI SDK `max_retries` handles it). |
| `length` finish | Output hit `max_tokens`. | Raise `max_tokens` or shorten context. |
| `timeout` | Shared 8 GB GPU under queue — long completions legitimately take seconds. | `timeout >= 120s` on the client; SSE per-chunk idle ~60s is safe. |

**Client-side resilience checklist:** ① health-gate before long jobs, ②
`aiolimiter` (e.g. 2–5 req/s — far below any platform limit), ③ retry 503/429
with backoff, ④ timeouts as above, ⑤ if a completion returns `content == null`
with thinking on, retry once with thinking off.

---

## 8. Capacity envelope (this hardware — an 8 GB RTX 3070)

- Context window: **32,768 tokens** total (prompt + completion + thinking).
- Decode concurrency: engine caps at **16 simultaneous** sequences; beyond that
  requests queue (healthy, just slower). A single agent issuing 1–5 req/s with
  ≤ a few concurrent streams is far inside the envelope.
- Measured (2026-09-08, via gateway): streaming TTFT ~60 ms / ITL ~23 ms /
  ~43 tok/s (idle); at deep saturation (44 concurrent users, 3K-token
  contexts) the platform served 2,288 requests with **0 failures**; peak KV
  cache 51%, worst-case p95 latency ~44 s (queue, not errors).
- Practical guidance: keep per-request prompts (retrieved chunks + history +
  thinking) comfortably under ~24K tokens so answers have headroom; if you
  need long multi-turn history, summarize/trim rather than pushing the full
  window.

---

## 9. Optional cloud tier (Tier-2 Gemini)

The local model is the default. Requests carrying the header
`x-ai-eg-model: gemini-3.5-flash-lite` are routed to Google AI Studio
(Gemini 3.5 Flash Lite) through the same `/v1/chat/completions` surface —
useful as a fallback for very long contexts or when the local box is busy.
The agent can implement a simple header-based failover: try local; on 503,
retry once with the Gemini header.

---

## 10. Conformance

Run the portable suite against any endpoint (this repo or a fresh checkout —
it depends only on `openai` + env vars):

```bash
export NANOSERVE_CONTRACT_LIVE=1
export NANOSERVE_BASE_URL=http://localhost:8000/v1   # default
export NANOSERVE_MODEL=ibm-granite/granite-4.2-3b    # default
uv run --group dev pytest contract_tests -v          # from this repo
# or in the repo:  make contract-test
```

Passing the suite against a deployment is the definition of "the contract
holds". Version bumps to this doc or the OpenAPI spec are the changelog — pin
the commit/tag you validated against in the agent project.
