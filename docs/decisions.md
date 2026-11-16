# Architecture Decision Records

Each record states the decision, why it was taken, what was rejected, and — where relevant —
the condition that would reverse it. Records are append-only; supersede rather than edit.

---

## ADR-001 — Hybrid process model: one codebase, two process types

**Status:** accepted (2026-11-15)

**Context.** Research jobs run for minutes and are entirely I/O-bound on LLM and search calls.
HTTP requests must stay fast. Options considered: a single-process modular monolith; this
hybrid; or four microservices (api / orchestrator / search / llm-gateway).

**Decision.** One repository and one image with two entrypoints — `uvicorn app.main:app` and
`arq app.workers.settings.WorkerSettings` — plus PostgreSQL and Redis.

**Why not a single process.** A hung or memory-hungry three-minute LLM job would take the API
down with it, and request handlers would compete with graph execution for the event loop.

**Why not microservices.** The four candidate services would all read and write the same
database: a distributed monolith with the latency and operational cost of microservices and
none of the benefits. There is no independent scaling axis other than "worker", which the
hybrid already provides, and no independent data ownership. Estimated cost of that choice:
~3–5 extra sessions of plumbing and roughly 3× the hosting bill.

**Consequence.** Module boundaries cannot be enforced by the network, so they are enforced by
`import-linter` contracts in CI (`backend/.importlinter`). A failing contract is a red build.

---

## ADR-002 — LangGraph, narrowly scoped

**Status:** accepted (2026-11-15)

**Decision.** Use LangGraph for three things only: the state graph, `interrupt()` for the human
approval pause, and `AsyncPostgresSaver` for durable resume. Do **not** use LangChain's
chat-model or agent abstractions.

**Why.** `interrupt()` plus a Postgres checkpointer is exactly the durable human-in-the-loop
primitive this product needs, and re-implementing it would be busywork. But a generic chat-model
wrapper would cost us native structured-output guarantees and per-call token/cost accounting —
both of which the honesty requirements depend on.

**Consequence.** Node functions are plain `async def node(state, deps) -> dict` with no
LangGraph imports. The orchestrator is replaceable.

**Reversal condition.** If the checkpointer conflicts with our schema or a version bump breaks
the interrupt contract, replace the runner with an in-house state machine (~120 lines). Node
code does not change.

**Verified versions:** langgraph 1.1.10, langgraph-checkpoint-postgres 2.0.25.

---

## ADR-003 — Provider-agnostic LLM gateway over an OpenAI-compatible transport

**Status:** accepted (2026-11-15), supersedes an earlier plan to use a vendor SDK directly

**Decision.** All LLM access goes through one port, `app.adapters.llm.base.LLMClient`. The
production implementation speaks the **OpenAI-compatible Chat Completions** shape with
`base_url` and model IDs supplied by environment variables. OpenRouter is the MVP default.

**Why.** It decouples the application from any single provider, and the OpenAI-compatible shape
is the widest-supported gateway contract: OpenRouter, a LiteLLM proxy, Azure OpenAI, Together,
Groq and a local vLLM server are all reachable by changing `LLM_BASE_URL` and `LLM_API_KEY`
with **no code change**. AWS Bedrock's native API is not OpenAI-compatible, so it would arrive
either as a second adapter behind the same port or — cheaper — fronted by LiteLLM, needing no
new code.

**Consequence.** Model capability is not uniform across a multi-vendor catalogue, which forces
explicit capability negotiation (ADR-004). No model ID may appear in application code; a
pre-commit hook enforces this.

---

## ADR-004 — Capability negotiation with three output tiers, and fail-fast validation

**Status:** accepted (2026-11-15)

**Decision.** At startup, load the provider's model catalogue and validate every configured
node model: it exists, its context window meets our floor, it supports native schema-constrained
output (or at least tool calling), and its pricing metadata is present. Failure aborts boot with
a message naming the offending environment variable.

Structured output is obtained in this preference order:

1. **Native JSON-Schema** response format — the default, and the only tier permitted when
   `LLM_REQUIRE_STRUCTURED_OUTPUT=true`.
2. **Forced tool call** whose parameters are the schema — for models with tools but no schema mode.
3. **Prompt-constrained JSON** with one bounded repair retry — explicitly lower reliability,
   refused in live mode, available for local/open-model experiments.

**Why.** A 500 three nodes into a user's research task is a far worse failure than a refused
boot. And tier 3 weakens layer 3 of the prompt-injection boundary (`security.md`), so the
security posture would otherwise silently depend on which model happened to be configured.

**Consequence.** A committed catalogue snapshot
(`backend/app/adapters/llm/catalogue_snapshot.json`) keeps offline and CI runs network-free.
Catalogue drift — a renamed or deprecated slug — surfaces as a loud boot failure rather than a
silent fallback.

---

## ADR-005 — Pinned model slugs, not provider aliases

**Status:** accepted (2026-11-15)

**Decision.** Configure exact model slugs (e.g. `anthropic/claude-sonnet-5`). Do not use the
provider's moving aliases (`~anthropic/claude-sonnet-latest`), which exist in the catalogue.

**Why.** An alias that silently changes model underneath a committed evaluation harness
destroys reproducibility: the eval numbers in the case study would stop meaning anything. A
pinned slug makes a model change an explicit, reviewable commit.

**Verified against the live OpenRouter catalogue on 2026-11-15** — see
[`model-configuration.md`](model-configuration.md) for the selected models, their measured
prices and capability flags.

---

## ADR-006 — Polling, not server-sent events

**Status:** accepted (2026-11-15)

**Decision.** The frontend polls task state every 1.5 s while a task is active and stops on a
terminal status.

**Why.** At this cadence SSE offers no perceptible benefit, and it adds proxy buffering,
reconnection and deployment failure modes.

**Reversal condition.** Token-level streaming of the draft as it is written — the one feature
that would actually need a push channel.

---

## ADR-007 — arq for background execution

**Status:** accepted (2026-11-15)

**Decision.** Use arq over Redis for the job queue.

**Why.** The workload is entirely async I/O (HTTP to the LLM gateway and search provider, async
Postgres). arq is async-native and small. Celery is sync-first and heavyweight for this;
RQ forks per job.

**Why not a Postgres `SKIP LOCKED` queue.** It was considered and is a good fit on its own
merits, but Redis is already wanted for single-flight task locks and rate limiting — and the
transactional-outbox pattern is the centrepiece of a sibling portfolio project, where it tells a
stronger story than it would as a second-order detail here.


---

## ADR-008 — Strict structured output refuses tier 2, rather than downgrading

**Status:** accepted (2026-11-16)

**Context.** `ModelCapabilities.output_mode` returned the strongest tier a
model supported. With `LLM_REQUIRE_STRUCTURED_OUTPUT=true` and a model that
offered tool calling but not native JSON-Schema, it quietly returned tier 2 —
contradicting the documented contract that tier 1 is *the only tier permitted*
in strict mode.

**Decision.** In strict mode, anything below native JSON-Schema raises
`ModelCapabilityError`. Outside strict mode a downgrade is allowed but is
logged as `structured_output_tier_downgraded`, so it is never silent.

**Why.** The caller asked for a guarantee. An unmet guarantee has to be an
error, not a quieter promise — particularly here, where the guarantee is part
of the prompt-injection boundary. Startup validation already refuses such a
model, so this closes the gap for a client constructed directly.

---

## ADR-009 — Live model testing is budgeted against the ledger, not against intent

**Status:** accepted (2026-11-16)

**Decision.** Live provider tests are marked `live`, skipped unless
`LLM_PROVIDER=openrouter` and a real key are set, and gated by a fixture that
sums actual `cost_usd_estimate` from the `llm_calls` table and refuses to
start once `M3_TEST_BUDGET_USD` is reached.

**Why.** A budget that exists only as a number in a plan does not bound
anything. Reading what was actually spent means a runaway retry loop or an
accidental expensive model stops the suite instead of the bill.

**Consequence.** The default test suite stays free, offline and
deterministic; CI never needs a provider key. The cost of live assurance is
opt-in and visible.
