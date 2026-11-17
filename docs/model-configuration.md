# Model configuration

AgentFlow is coupled to an *interface*, never to a vendor. Which model answers a given graph
node is configuration, resolved at startup and validated against the provider catalogue.

**No model ID appears in application code.** `scripts/guard-no-model-ids.sh` runs pre-commit and
fails the commit if one does, outside the LLM adapter and its tests.

## How a model is chosen for a node

```
LLM_MODEL_<NODE>   e.g. LLM_MODEL_PLANNER
      ↓ unset
LLM_MODEL_DEFAULT
```

Nodes: `PLANNER`, `ANALYST`, `CITATION_CHECK`, `DRAFT`. (The `research` node performs no LLM
call — it queries the search port.) Changing any model is an environment change plus a restart.

## Providers

| `LLM_PROVIDER` | Behaviour |
|---|---|
| `stub` | Deterministic offline adapter keyed by `(node, sha256(prompt))`. No network, no key. **Default for tests, CI and offline development.** |
| `openrouter` | OpenAI-compatible Chat Completions against `LLM_BASE_URL`. |

Because the transport is the OpenAI-compatible shape, `LLM_BASE_URL` also points at a LiteLLM
proxy, Azure OpenAI, Together, Groq or a local vLLM server with no code change. See ADR-003.

## Selected default models

Verified against the live OpenRouter catalogue on **2026-11-15** (467 models enumerated; 302
met the baseline of native structured outputs plus a ≥200k context window). Selection criteria,
in order: **native JSON-Schema support → ≥200k context → provider reliability → price.**

| Node | Model | Context | $/1M in | $/1M out | Structured outputs | Tools |
|---|---|---|---|---|---|---|
| `planner` | `anthropic/claude-haiku-5.5` | 1,000,000 | 0.10 | 0.50 | yes | yes |
| `analyst` | `anthropic/claude-sonnet-5` | 1,000,000 | 2.00 | 10.00 | yes | yes |
| `citation_check` | `anthropic/claude-sonnet-5` | 1,000,000 | 2.00 | 10.00 | yes | yes |
| `draft` | `anthropic/claude-sonnet-5` | 1,000,000 | 2.00 | 10.00 | yes | yes |

Fallback chain (`LLM_FALLBACK_MODELS`), deliberately **cross-vendor** so that one provider's
outage degrades model quality rather than failing the research task:

| Order | Model | Context | $/1M in | $/1M out |
|---|---|---|---|---|
| 1 | `openai/gpt-5.1` | 400,000 | 1.25 | 10.00 |
| 2 | `google/gemini-4-argon` | 1,048,576 | 2.00 | 10.00 |

**Why these.** The planner only decomposes a question into ≤4 bounded subtasks, so a small fast
model is sufficient and 20× cheaper. `citation_check` stays on a frontier model because it is
the node that protects the project's honesty guarantee — accuracy there matters more than cost.
Documented alternatives: `anthropic/claude-opus-5` ($5.00/$25.00) for maximum quality, and
`openai/gpt-6-luna` ($0.10/$0.50) as a cross-vendor planner.

Prices are the catalogue's own figures at the date above, recorded for cost estimation. They are
**not** a promise about current pricing; `pricing.py` reads live catalogue values at runtime and
the gateway's reported cost is stored alongside our estimate.

## Capability validation

On startup the adapter loads the catalogue (live, with the committed snapshot
`backend/app/adapters/llm/catalogue_snapshot.json` as the offline fallback) and asserts, for
every configured node model:

1. the model exists in the catalogue;
2. `context_length` ≥ `MIN_CONTEXT_TOKENS`;
3. native structured outputs are supported — or tool calling, if
   `LLM_REQUIRE_STRUCTURED_OUTPUT=false`;
4. prompt and completion pricing are present.

A failure **aborts startup** naming the offending environment variable, rather than surfacing as
a 500 partway through a user's task. `GET /api/meta` and `GET /ready` both report the resolved
model and its capabilities per node.

## Output tiers

| Tier | Mechanism | Allowed in live mode |
|---|---|---|
| 1 | Native JSON-Schema response format | yes — the default |
| 2 | Forced tool call whose parameters are the schema | only if tier 1 is unavailable |
| 3 | Prompt-constrained JSON + one repair retry | **no** (requires `LLM_REQUIRE_STRUCTURED_OUTPUT=false`) |

Tier 3 is refused by default because schema-constrained output is layer 3 of the
prompt-injection boundary — see [`security.md`](security.md). Relaxing it to make a model work
is a security decision, not a configuration convenience.

## Cost per research task

This figure has been wrong twice, in both directions, which is worth recording.

**M1 estimated ~$0.20/task** from a generic 45k-in/15k-out agentic-workload
assumption. **M2's measured token profile was ~3k in / ~1.5k out**, because the
fixture corpus is eight short sources — suggesting ~$0.018. **M3's live runs
then measured 4,383 output tokens per call** — ≈17.5k output for a four-call
workflow, putting it back near **$0.18**.

The input estimate was accurate throughout. The variable is **model
verbosity**, which catalogue pricing cannot tell you and only a real run can.

| Profile | Per run, at measured live verbosity |
|---|---|
| **Shipped default** (haiku planner + sonnet elsewhere) | **≈$0.18** |
| All `anthropic/claude-sonnet-5` | ≈$0.21 |
| All `anthropic/claude-haiku-5.5` | ≈$0.010 |
| All `anthropic/claude-opus-5` | ≈$0.45 |
| Free profile (any verified free model) | **$0.00** |

**The paid rows remain unverified.** They extrapolate free-model verbosity onto
paid pricing, and a paid model may well be terser. The `llm_calls` ledger
records real tokens, latency, the model served, our estimate and the gateway's
reported cost per call, so the moment a paid run happens the real number is one
query away. Until then these are estimates carrying a known-shaky assumption,
not measurements.

Measured free-profile figures, per node, are in
[`milestones/m3-acceptance-ledger.md`](milestones/m3-acceptance-ledger.md).

## Free models

Verified 2026-11-16, re-confirmed by 30 live calls on 2026-11-17. Of 469
catalogue models, **17 were genuinely free** — every published pricing field
zero, not just the token prices, since a non-zero request fee would make a
nominally free model billable — and **7 of those advertised native structured
outputs**.

The catalogue flag says a model *advertises* strict JSON-Schema support.
Whether an endpoint honours it is a separate question, and the only honest way
to answer it is to ask. Probing each with a real node schema
(`backend/scripts/probe_models.py`, spend $0.000000):

| Model | Probe result |
|---|---|
| `baidu/ernie-5.5-flash-preview:free` | works — `json_schema`, 5.0 s |
| `dots-studio/dots-3-note-preview:free` | works — `json_schema`, 16.0 s |
| `inclusionai/ling-3.2-flash:free` | works — `json_schema`, 43.2 s |
| `openrouter/free` (meta-router) | works, but routed to `baidu/ernie-…:free` |
| `liquid/lfm-3-1.7b:free` | 404 — no endpoint matches our data policy |
| `nvidia/nemotron-3.5-super-vl:free` | 404 — same |
| `particle-ai/particle-1:free` | 404 — same |

### Verified providers and their failure modes

The **serving provider** is recorded per call (`llm_calls.provider_served`),
because vendor identity cannot be read off a model id: two ids can share a
host, and `openrouter/free` demonstrably routes to Baidu, so it is not a
second vendor.

| Provider | Model | Verdict under full workflow |
|---|---|---|
| `Baidu` | `baidu/ernie-5.5-flash-preview:free` | completes it; fastest of the three |
| `Ant Digital` | `inclusionai/ling-3.2-flash:free` | completes it, but very slow — 43 s for a 61-token prompt |
| `AtlasCloud` | `dots-studio/dots-3-note-preview:free` | **cannot** — HTTP 400 on `citation_check`, the largest prompt and biggest schema |

That last row is why a free profile needs a fallback chain rather than one
model, and why the chain puts the fastest reliable model first.

### The data-policy finding

Three of the seven are unreachable **because of our own privacy setting**, not
a model limitation. AgentFlow sends `provider.data_collection=deny`, and those
endpoints are free precisely because they permit training on request data, so
provider routing finds nothing eligible and returns 404.

Reaching them would mean setting `LLM_DATA_COLLECTION=allow` — relaxing a
privacy control to obtain a cheaper model. That trade was **declined**, and it
did not need making: `baidu/ernie-5.5-flash-preview:free` works with the
control intact, so the free profile keeps the paid profile's full security
posture.

### Free profile

```bash
LLM_PROVIDER=openrouter
LLM_FREE_MODELS_ONLY=true
LLM_MODEL_DEFAULT=baidu/ernie-5.5-flash-preview:free
LLM_FALLBACK_MODELS=dots-studio/dots-3-note-preview:free,openrouter/free
LLM_MAX_ATTEMPTS=2
LLM_TIMEOUT_SECONDS=180
```

`LLM_FREE_MODELS_ONLY=true` is a hard control, not a label: it refuses any
model whose published pricing is non-zero, at startup *and* before every call.
A billable request cannot be made with it set, even if a priced model is
configured by mistake. Verified across 30 live calls — the account's
`spend_usd` stayed `0`.

`LLM_MAX_ATTEMPTS` drops to 2 and the timeout rises because free endpoints are
rate-limited and slow: measured 14–57 s per call against 2–5 s for paid
models. Retrying hard into a shared limit makes it worse for everyone, so the
adapter honours a provider's `Retry-After` hint over its own backoff guess.

**What the free profile costs you.** These are preview models, materially
weaker at analysis and citation checking, with availability that varies by the
hour — one candidate was rate-limited during the probe and another cannot
complete a workflow at all. They exist so the workflow can be exercised
without spend, not so anyone can rely on their output. Structured-output
validation and every security control are identical to the paid profile.

## Changing a model

Use the `llm-model-change` skill (`.claude/skills/llm-model-change/SKILL.md`). In short:
validate the candidate against the catalogue, update `.env.example` and the table above with
the verified slug, price and capability flags, re-run the cross-model structured-output and
injection tests, and record the measured cost.
