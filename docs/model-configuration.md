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

## Cost estimate per research task

Assumes 4 subtasks, ~8 sources, 4 LLM calls, ~45k input / ~15k output tokens.

| Profile | Est. per task |
|---|---|
| **Shipped default** (haiku planner + sonnet elsewhere) | **~$0.20** |
| All `anthropic/claude-sonnet-5` | ~$0.24 |
| All `anthropic/claude-opus-5` | ~$0.60 |
| Fallback chain active (`openai/gpt-5.1`) | ~$0.21 |

These are **estimates from catalogue pricing, not measurements.** They will be replaced by
measured figures from the `llm_calls` ledger at M3, which records tokens, latency, the model
actually served, our estimate and the gateway's reported cost per call.

## Changing a model

Use the `llm-model-change` skill (`.claude/skills/llm-model-change/SKILL.md`). In short:
validate the candidate against the catalogue, update `.env.example` and the table above with
the verified slug, price and capability flags, re-run the cross-model structured-output and
injection tests, and record the measured cost.
