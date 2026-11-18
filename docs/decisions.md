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


---

## ADR-010 — A free-model development profile, with no relaxed controls

**Status:** accepted (2026-11-16)

**Context.** Live verification was wanted before the OpenRouter account had
purchased credits. Of 469 catalogue models, 17 are genuinely free and 7 of
those advertise native structured outputs.

**Decision.** Add a free development profile selected purely through
environment variables — no code path, no branch, no special case — plus
`LLM_FREE_MODELS_ONLY`, which refuses any model with non-zero published
pricing at startup *and* before every call.

**Why a hard control rather than a convention.** "Do not make paid calls" is
a constraint that should not depend on whoever edits `.env` next being
careful. Checking every pricing field also means a model with zero token
prices but a non-zero request fee cannot slip through.

**What was explicitly not done.** Three free models are unreachable because
AgentFlow sends `data_collection=deny` and those endpoints are free precisely
because they permit training on request data. Reaching them would mean
relaxing a privacy control to get a cheaper model. That trade was declined;
`baidu/ernie-5.5-flash-preview:free` works with the control intact. Nothing
about structured-output validation or security was weakened for the free
profile.

**Consequence.** Free endpoints are slow (8–35 s per call measured, against
2–5 s paid) and rate-limited, so `LLM_MAX_ATTEMPTS` drops to 2, the timeout
rises, and the adapter honours a provider's `Retry-After` hint over its own
backoff. The free profile is for exercising the workflow without spend; its
output quality is not comparable to the paid profile and is not presented as
though it were.


---

## ADR-011 — The provenance notice describes the actual combination, not a guess

**Status:** accepted (2026-11-17)

**Context.** A live run against a free model produced a report containing both
*"No language model was called and no web search was performed"* and
*"Written by model(s): `baidu/ernie-5.5-flash-preview:free`"*. The notice fired
when the provider was a stub **or** search was fixtures, so a real model
reasoning over fixture sources was labelled fully offline.

**Decision.** Four combinations, three notices, one silence: fully offline;
real model over fixture sources; stub text over real sources; and nothing at
all when both are live.

**Why it matters more than it looks.** This project's central promise is that
it never overstates what produced a result. A report claiming no model was
called while a model wrote it is the exact failure the honesty rules exist to
prevent — and it was *understating* rather than overstating, which is why no
existing test caught it. Four pure unit tests now cover the combinations, and
the live test asserts the live-model-over-fixtures wording directly.

**How it was found.** By reading the output of the first live run, not by a
test. Worth remembering: the assertions were checking for the presence of
correct strings and the absence of one wrong string, and the wrong string that
mattered was not on the list.

---

## ADR-012 — Vendor identity is recorded, not inferred from model ids

**Status:** accepted (2026-11-17)

**Decision.** `llm_calls.provider_served` records the company the gateway
reports as having served each call, alongside `model_requested` and
`model_served`.

**Why.** A two-vendor claim cannot be supported by model ids. Two ids can be
hosted by the same company, and a meta-router can route both to one backend —
`openrouter/free` demonstrably routes to `baidu/ernie-5.5-flash-preview:free`,
so treating it as a second vendor would have been a false claim. The
injection test now asserts that the providers which actually served the two
runs are disjoint, and fails loudly if the gateway reports none.

**Consequence.** Three distinct providers were verified by observation —
`Baidu`, `Ant Digital` and `AtlasCloud` — along with the finding that
AtlasCloud cannot complete a workflow at all (HTTP 400 on the largest schema).
That is the kind of fact a fallback chain exists for, and it would have been
invisible without recording the provider.

---

## ADR-013 — Retrieval is AgentFlow's job, and the URL is never dereferenced

**Status:** accepted (2026-11-17)

**Decision.** The search provider returns titles, URLs and provider-supplied
excerpts, and AgentFlow uses exactly those. It does not fetch the pages. The
model is given no tools and no network access, so the only text it ever sees is
text we requested deliberately.

**Why.** Fetching a URL chosen by a search provider — or worse, by a model —
is a server-side request forgery primitive. Not fetching removes the class of
problem rather than mitigating it: there is no request to restrict, no
redirect chain to follow, no internal address to reach. The cost is that
excerpts are shorter and shallower than full page text, which is a quality
trade rather than a safety one.

**Rejected.** Fetching pages ourselves for better excerpts, behind an egress
allowlist. That reintroduces the whole SSRF surface — redirects, DNS
rebinding, IPv6 mapping, metadata endpoints — for a quality improvement we can
live without at this scale.

**Reversal condition.** If excerpt quality becomes the limiting factor on
report usefulness, fetching could be added as a separate, sandboxed egress
service with its own allowlist and no access to the application network. It
would be a new component, not a new capability of this one.

---

## ADR-014 — Normalisation and validation are separate operations

**Status:** accepted (2026-11-17)

**Decision.** `normalise_url` produces the canonical form used for storage and
the `url_sha256` digest. `validate_url` **rejects** a URL outright. They are
different functions with different return contracts, and validation never
sanitises.

**Why.** Conflating them breaks in both directions. A validator that quietly
"fixes" a URL turns a hostile input into an accepted one, which is how
allowlist bypasses happen. A normaliser that rejects would drop usable sources
over a stray tracking parameter. Keeping them apart also makes deduplication
correct: two links to the same page differing only in `utm_source`, fragment,
default port or host case collapse to one digest, so the same page is not
cited twice as two sources.

**Consequence.** Stored URLs are canonical, and the deduplication guarantee is
a property of the digest rather than of whichever code path happened to insert
the row. The live run confirmed it: 17 sources, 17 distinct digests.

---

## ADR-015 — A failed search fails its subtask; it is never substituted

**Status:** accepted (2026-11-17)

**Decision.** When retrieval fails for a subtask, that subtask produces no
sources and the failure is recorded. It is never backfilled from the fixture
corpus or any other substitute. If **every** subtask fails, the node raises
`SearchUnavailableError` and the task fails.

**Why.** Substituting fixture data for a failed live search would make a
report that looks live but is not, which is the one thing the provenance rules
exist to prevent. Partial evidence is honest and disclosed; fabricated
evidence is neither. Failing the whole task on one subtask's failure would be
the opposite error — the surviving subtasks may carry most of the answer, and
discarding them helps nobody.

**Consequence.** Reports disclose partial research in their limitations
section, with the count of failed subtasks. The step row records which subtask
failed and its error code, so a reader of the timeline never has to infer why
the evidence is thinner than the plan.

---

## ADR-016 — The provider's usage endpoint is not trusted as the budget control

**Status:** accepted (2026-11-17)

**Decision.** The hard control on search spend is the local per-task cap
(`SEARCH_MAX_PER_TASK`). The provider's usage endpoint is consulted for
reporting, cached for 150 seconds, and when it cannot be read the guard
**warns and proceeds** rather than either blocking or claiming a figure.

**Why.** Both failure modes were observed. The endpoint is itself rate-limited
to roughly 10 requests per 10 minutes, and polling it before every test
exhausted it in 26 seconds — a budget guard that takes the whole suite down is
not a control, it is an outage. It also reported `0 of 1000 credits used` after
roughly 60 real search requests, so its number cannot be relied on as a
measure of spend either.

**Consequence.** Credit consumption is reported as *AgentFlow's own request
count*, taken from the step rows, and the ledger says plainly that
provider-side confirmation was not obtainable. The guard that cannot verify
spend says so at runtime instead of implying a figure it does not have.

---

## ADR-017 — Rate limiting is keyed by session and fails open

**Status:** accepted (2026-11-17)

**Decision.** A Redis fixed-window limiter guards the three write endpoints,
keyed by `session:method:path`. If Redis is unreachable the limiter allows the
request.

**Why.** Session, not IP: the session cookie already scopes everything a
visitor can reach, while IP is shared behind NAT and trivially rotated.
Per-path, so that approving a report does not consume a submission's
allowance. Fixed window rather than a token bucket because the counter is one
key that Redis expires itself — no state to sweep, no clock to coordinate —
and the burst it admits at a window boundary does not matter for a guard whose
job is to stop sustained hammering.

Failing open is the deliberate part. A limiter that rejects requests when its
own backing store is down converts a Redis blip into an outage, and the
provider allowance is protected regardless by hard per-task and per-session
ceilings. The limiter is a convenience control, not the last line of defence.

**Consequence.** Read endpoints are deliberately unlimited: polling is how the
UI tracks progress, and limiting it would break the thing it is there to
support.

---

## ADR-018 — Browser tests run against the real stack, not a mocked API

**Status:** accepted (2026-11-18)

**Decision.** The Playwright suite drives the real API, the real arq worker,
real Postgres and real Redis, in the offline profile: the deterministic stub
model adapter and the committed fixture corpus. Nothing on the happy path is
mocked. Interception is reserved for responses the real backend will not
readily produce — a dead API, a 429, a 500, hostile report content — and each
one says why at the point of use.

**Why.** A mocked browser test asserts that the UI renders our own
assumptions about the API. The expensive failures are the ones where the two
disagree, and the first run of this suite proved the point: it found a bug
where a completed task rendered an empty timeline and "No report yet", which
no amount of mocking would have surfaced because the mock would have returned
the data the real client never fetched.

The offline profile is what makes this affordable. The stub adapter and
fixture corpus need no credentials and cost nothing, so the suite runs in CI
on every push rather than nightly against a metered provider.

**Consequence.** `scripts/e2e-stack.sh` **asserts** the profile before any
test runs, rather than assuming it: a suite that silently ran against live
providers would spend a metered allowance and stop being deterministic, and
several tests assert fixture-specific labels. The cost is that CI's slowest
job now builds images and waits for a worker, about a minute.

---

## ADR-019 — Report Markdown is rendered, and treated as hostile input

**Status:** accepted (2026-11-18)

**Decision.** The report is rendered to HTML with `react-markdown`, with raw
HTML disabled, an explicit http/https/mailto URL allowlist, and
`rel="noopener noreferrer"` on every link. The exact Markdown source stays one
click away. Heading levels are shifted down so the report nests under the page
outline.

**Why.** Through M4 the report was shown in a `<pre>`, with a comment saying
proper rendering belonged in M5. A reviewer deciding whether to approve a
document needs to read it: the claim-support table is the densest and most
decision-relevant part of a report, and as pipe-delimited text it is close to
unreadable. Keeping the raw view is what makes rendering safe to adopt —
approval is a judgement about an artefact, and the artefact that gets exported
must be inspectable exactly as it will be exported.

The content is model-written text quoting retrieved pages, so it is treated as
attacker-influenced rather than as content we authored. Rejected: rendering
with `rehype-raw` plus a sanitiser. Allowing HTML and then removing the
dangerous parts is a larger surface than never parsing HTML at all.

**Consequence.** Fifteen component tests and four browser tests pin the
behaviour, because every property above is a library default that a future
change could silently remove. The bundle grows by about 65 kB gzipped.

---

## ADR-020 — The open section lives in the URL, and sections are tabs

**Status:** accepted (2026-11-18)

**Decision.** `/tasks/:taskId?view=report|sources|claims|workflow`. The task
id is a path segment and the open section a query parameter; switching
sections replaces rather than pushes history. The sections are ARIA tabs with
the full keyboard pattern.

**Why.** Selection used to be component state, so a reload lost it and there
was nothing to send a colleague. Research review is collaborative — "look at
the unsupported claim in this report" needs a link.

Tabs rather than one long column because the reviewer's task is to read a
report and decide, and the supporting evidence is deep. Stacked, the primary
task became a scrolling exercise, and on a phone the approval controls sat
several screens below the report they applied to. The approval gate therefore
sits *outside* the tabs: it is the reason the page exists and must not be
something a reviewer has to go and find.

Switching sections replaces history because walking back through four tab
changes one Back press at a time is not what a reader wants; returning to the
task list is.

**Consequence.** Deep links, reload and the Back button all work, and are
tested. An invalid `view` parameter falls back to the report rather than
rendering nothing, because a URL is something people edit by hand.
