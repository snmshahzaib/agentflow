# Testing

Every number on this page came from a run on 2026-11-18. Nothing here is
projected or aspirational; where coverage is missing, it says so.

`scripts/audit-docs.py` checks the counts on this page against what the suites
actually collect, so a stale figure fails CI rather than sitting here looking
authoritative.

## Commands

```bash
cd backend
uv run pytest                        # all 452 offline tests (20 live ones skip)
uv run pytest -m "not integration"   # 282 tests, no Postgres or Redis needed
uv run pytest -m integration         # 170 tests, needs Postgres + Redis
uv run pytest --cov=app --cov-report=term
uv run pytest tests/test_llm_registry.py::TestStubRuntime -q   # one class
```

Live tests spend a metered allowance and are selected explicitly. The markers
separate the two allowances, which matters because they are exhausted at very
different rates:

```bash
uv run pytest -m live                             # model calls (OpenRouter)
uv run pytest -m "live_search and not live_models" # search credits only, no model calls
uv run pytest -m live_models                      # BOTH allowances; 8 model calls
```

```bash
cd frontend
pnpm test            # 58 component tests (Vitest)
pnpm lint            # ESLint, --max-warnings 0
pnpm typecheck       # tsc -b
```

Browser end-to-end tests drive the **real stack** in its offline profile, so
they need it running. The runner does both:

```bash
scripts/e2e-test.sh                      # start the stack, run all 34 specs
scripts/e2e-test.sh --down               # ...and tear it down afterwards
scripts/e2e-test.sh e2e/a11y.spec.ts     # one spec
scripts/e2e-stack.sh up|down|env         # manage the stack by hand
```

```bash
make verify                          # lint, types, boundaries, secrets, tests
scripts/smoke-test.sh                # end-to-end against a running stack
scripts/restart-recovery-test.sh     # worker restart recovery, real Docker
```

## Results (measured 2026-11-18)

| Suite | Count | Result |
|---|---|---|
| Backend unit (`-m "not integration"`) | 282 | pass in 7.7 s |
| Backend integration (Postgres + Redis) | 170 | pass in 233 s |
| **Backend total (offline)** | **452** | **pass** (20 live tests skipped) |
| Live models (`-m live`, free profile) | 10 | **all pass**, $0.00 spend |
| Live search (`-m "live_search and not live_models"`) | 9 | **all pass** |
| Live full lifecycle (`-m live_models`) | 1 | **pass** — 8 model calls, 8 searches |
| Frontend components (Vitest) | 58 | pass in 4.1 s |
| Browser end-to-end (Playwright, real stack) | 34 | Chromium (33) + the critical journey on Firefox (1) |
| ESLint (`--max-warnings 0`) / `tsc -b` | — | clean |
| Frontend production build | — | succeeds |
| ruff check + format | — | clean |
| mypy (69 source files) | — | clean |
| import-linter | 4 contracts | all kept |
| gitleaks (history + staged) | — | no leaks |
| pip-audit | 90 packages | no known vulnerabilities |
| pnpm audit | — | clean; no accepted exceptions (the picomatch advisory was withdrawn upstream on 2026-11-18) |
| `scripts/smoke-test.sh` | — | passes |
| `scripts/restart-recovery-test.sh` | — | passes against Docker |
| `scripts/e2e-test.sh` | 33 | passes against Docker |

Backend line coverage: **90%** (2,699 statements, 257 uncovered).

An earlier commit message (`c09ac85`) claimed "386 tests (273 unit, 113
integration)". That was wrong; the figures above are measured.

## What the tests actually assert

Tests are organised around invariants the design depends on, not around
functions.

**Configuration** (`test_config.py`) — model resolution order per node;
fallback parsing; that `.env.example` placeholders count as unset so copying
the file verbatim fails fast rather than looking configured; that production
refuses the development session secret and a relaxed
`LLM_REQUIRE_STRUCTURED_OUTPUT`; and that every entry in the node→environment
variable mapping matches a real settings field, which is the bug that mapping
was introduced to fix.

**Database invariants** (`test_db_schema.py`) — `UNIQUE(task_id, node,
attempt)` blocks a duplicated step while allowing a genuine retry as a new
attempt; a report version cannot be written twice; a source cannot be stored
twice per task; unsupported claims are countable in SQL; deleting a task
removes its entire history, which the retention job depends on; and the enum
CHECK constraints exist in `pg_constraint` — asserted with raw SQL, because
going through the ORM would only prove SQLAlchemy's client-side validation and
would pass against a database with no constraint at all.

**LLM gateway** (`test_llm_catalogue.py`, `test_llm_adapters.py`,
`test_llm_registry.py`) — capability validation reports every problem at once
and names the environment variable at fault; a model without native schema
support is refused while strict mode is on; output tier selection; cost
estimated from catalogue pricing, with unknown pricing yielding no estimate
rather than a misleading zero; retry on a retryable failure; **cross-vendor
fallback when the primary is exhausted**; no retries on a 401 but the fallback
is still tried; an unusable fallback is dropped rather than taking down a
working primary; and the catalogue degrading to the committed snapshot when the
provider is unreachable.

**Graph units** (`test_graph.py`) — assembly and ordering (which also guards
the single `type: ignore` in `builder.py`); approval routing, including that
an absent decision ends the run rather than looping forever; the untrusted
fence, including that content containing the delimiter cannot close it; and
citation binding, including that fabricated, malformed and out-of-range labels
are dropped rather than raising.

**The workflow** (`test_workflow.py`) — runs the real graph against a real
database: every node records a step; sources are persisted, deduplicated and
labelled as fixtures; a report is written with claims and evidence links;
**unsupported claims carry no evidence link**; the report states its
provenance and limitations; the ledger records one call per LLM node and the
task counters match it; two runs of the same question produce byte-identical
reports; and a revision produces v2 with v1 superseded and both attempts
preserved in the timeline.

**Recovery and idempotency** (`test_recovery.py`) — the real arq handlers
against real Postgres and Redis: a task runs to the gate; a job for a purged
task is reported rather than raising; a second delivery is refused while the
lock is held; re-delivery after completion does not re-run anything; a
duplicate resume creates no second report; **a fresh worker resumes from the
persisted checkpoint** with no node re-running; a revision after restart
produces exactly one new version; a re-run of the draft node rewrites its
version instead of duplicating it; a failing node records a failed step; and
the orphan sweep re-queues a stalled unlocked task while leaving alone one a
live worker holds, one updated recently, and one awaiting approval.

**Locking** (`test_worker_locks.py`) — a second holder is refused while the
first holds the lock; the lock is released even when the body raises, so a
crashing job cannot leave a task permanently locked; a holder cannot release a
lock it no longer owns; the TTL is set so a crashed worker frees it; and the
heartbeat keeps a long task's lock alive and stops when another worker takes
over.

**API** (`test_api.py`) — 202 with a `Location` header on submission;
idempotency; validation errors in the standard envelope; session scoping
(another session gets 404, not 403); the timeline exposing failed attempts;
approval only from `awaiting_approval`, with 409 otherwise; and correlation-id
echo including sanitising an over-long inbound header.

**URL handling** (`test_search_urls.py`) — canonicalisation collapsing
tracking parameters, fragments, default ports, host case and trailing slashes
onto one digest, so the same page is never stored as two sources; and
rejection of unsafe schemes, embedded credentials, loopback, private,
link-local and cloud-metadata hosts, bare hostnames and over-long URLs.
Validation rejects and never repairs, which is asserted rather than assumed.

**The search provider** (`test_search_tavily.py`) — request shape at the
configured depth; malformed and unsafe entries dropped with a log rather than
stored; deduplication within one response; `Retry-After` honoured up to a cap;
HTTP status mapped onto the domain error taxonomy, with a 400 treated as
terminal and a 429 as retryable.

**Rate limiting** (`test_rate_limit.py`, and `TestRateLimiting` in
`test_api.py`) — requests allowed up to the limit then refused; a refusal
naming when to come back, never zero seconds; callers counted separately;
the counter expiring with its window so there is no state to sweep; an
unreachable Redis **failing open**; and at the API edge, a 429 carrying
`Retry-After` and the standard envelope, a refused submission writing no task
row, each endpoint having its own allowance, and read endpoints being
unlimited.

**Retention** (`test_retention.py`) — a task past the window deleted along
with its steps, sources, reports and claims, because a deletion that leaves
the retrieved excerpts behind has not deleted the part that matters; a task
inside the window untouched; the boundary tested on both sides; a sweep with
nothing to do being harmless; and that the job is actually **registered and
scheduled**, with `run_at_startup=False` so a restart loop cannot purge
repeatedly.

**Injection containment** (`test_injection.py`) — see `docs/security.md` for
what these do and do not establish. They confirm the red-team document is
really retrieved, that injected text cannot escape the data fence, that the
model is never given tools, and then drive a **deliberately compromised
model** that obeys the injected directive completely: it still cannot
self-approve, its fabricated citations do not resolve, its "everything is
supported" verdicts are downgraded, and the limitations section still reports
the truth.

**Frontend components** (Vitest) — the provenance banner states that offline
output is not from a live model; node descriptions never claim a claim is
verified as true; the claim table says support is not a truth judgement and
counts unsupported claims; the export link is absent before approval and
present after; a retried node stays visible in the timeline; client-side
validation blocks a request rather than relying on a 422; and API errors
surface their correlation id.

Added in M5: that the report renders as a document rather than as Markdown
source and can still show its exact source; that a superseded version is
readable and labelled; that the approval gate states each action's
consequence and refuses a revision with no note, moving focus to the field;
that progress names its stage and appears exactly once in the accessibility
tree; that tabs implement the roving-tabindex and arrow/Home/End pattern; and
**fifteen tests pinning the Markdown renderer's safety properties**, each of
which is a library default a future change could silently remove.

**Task-scoped queries** (`useApi.test.tsx`) — that a status change refetches
everything hanging off a task, and that the previous data stays on screen
while it does. This is the regression test for a bug the browser suite found:
polling stops when a task leaves `running`, so without the status in the query
key a completed task rendered an empty timeline and "No report yet".

## Fixtures and determinism

CI runs with `LLM_PROVIDER=stub` and `SEARCH_PROVIDER=fixture`, so the suite
makes **no network calls and needs no credentials**. The stub adapter is keyed
by `(node, sha256(prompt))`, so repeated runs produce identical output and
identical ledger rows.

An autouse fixture strips every settings field from the environment before
each test. This exists because of a real failure: `_env_file=None` disables
dotenv loading but pydantic-settings still reads real environment variables,
so the `DATABASE_URL` CI exports for integration tests silently overrode a
value a unit test had passed explicitly — the test passed locally and failed
in CI.

Integration tests run inside a transaction that is rolled back, so they cannot
leak rows into each other.

## Restart recovery, verified in Docker

`scripts/restart-recovery-test.sh` runs against the real compose stack, not
mocks:

1. a task is submitted and the worker is restarted **mid-flight**; the task
   still reaches the gate, every node completes, no `(node, attempt)` pair
   repeats, no URL is stored twice, and exactly one report version exists;
2. the worker is restarted again while the task is **parked at the gate**; the
   task survives, because its state is in PostgreSQL rather than the worker's
   memory;
3. export is refused with 409, a revision is requested, v2 is produced from a
   checkpoint written before the restart, the task is approved, and the export
   contains its limitations and provenance;
4. a second approval is rejected with 409.

A node interrupted mid-execution **does** re-run — the worker can die after a
node finishes but before LangGraph checkpoints it — and that is correct: the
re-run is recorded as a new attempt and the timeline shows both. One observed
run did exactly that (`plan` and `research` at attempt 2) and still produced
one report and eight distinct sources.

The hard-kill case was verified separately: SIGKILLing a worker left its task
stranded in `running` with 879 s left on its lock, which is what motivated the
lock heartbeat and the orphan sweep. After the fix, manufacturing that exact
state produced an `orphaned_task_requeued` log line and the task completed.

## Live provider tests

`-m live` makes **billable** calls and is skipped unless `LLM_PROVIDER=openrouter`
and a real key are set, so the default suite stays free, offline and
deterministic and CI never needs a provider key.

Three controls make running them safe:

- **a spend guard** that sums actual `cost_usd_estimate` from the `llm_calls`
  ledger before each test and refuses to start once `M3_TEST_BUDGET_USD` is
  reached. A budget that exists only in a plan bounds nothing.
- **`LLM_FREE_MODELS_ONLY`**, which refuses any model with non-zero published
  pricing at startup and before every call, checking every pricing field — a
  non-zero request fee makes a nominally free model billable.
- **a dedicated `agentflow_live` database**, never the one integration tests
  reset. Resetting the shared test database once already destroyed the record
  of a successful live run that had cost real quota to produce.

### Three outcomes, not two

A live test reports `PASSED`, `FAILED`, or `SKIPPED`. The third is distinct
and load-bearing: a test that cannot run because the day's free allowance is
spent has not found a defect, and reporting it as a failure would bury real
ones. A pytest hook converts a 429 whose body names a per-day allowance into a
skip; a plain rate limit still fails, because handling those is the adapter's
job.

### Running in batches

The free tier allows **50 requests per day** on a zero-credit account. Batches
are sized against that:

```bash
scripts/live-acceptance-batch.sh 1   # workflow, ledger, provenance   ~4 calls
scripts/live-acceptance-batch.sh 2   # injection, two vendors         ~8 calls
scripts/live-acceptance-batch.sh 3   # fallback, budget, error paths  ~6 calls
```

Each batch prints remaining quota before running, distinguishes the three
outcomes afterwards, and exports evidence to a committed file.
`docs/milestones/m3-acceptance-ledger.md` records what is verified, what
failed, what is blocked, and which acceptance criteria **cannot** be met under
a free tier at all.

## Live search tests

`-m live_search` consumes **Tavily credits** and is skipped unless
`SEARCH_PROVIDER=tavily` and a real key are set. Two batches exist because the
two allowances are exhausted at very different rates:

```bash
scripts/m4-live-batch.sh search      # ~20 search requests, ZERO model requests
scripts/m4-live-batch.sh lifecycle   # 8 search requests + 8 model requests
```

The split is load-bearing, not tidiness. Everything that can be proven without
a model call — real retrieval, canonical URLs, deduplication, provider
metadata, empty results, request shape, the per-task cap — runs through a
fixture that pairs **live search with the deterministic stub model**, so it
costs nothing from the scarcer allowance.

The `search` batch selects `-m "live_search and not live_models"`, and the
exclusion matters: the test module's `pytestmark` applies `live_search` to
every class in it, so a bare `-m live_search` also selects the full lifecycle
test. That is not hypothetical — it happened, with four model requests
remaining, and consumed seven of them before aborting. The marker and the
script both record why.

Credit accounting is asserted with an `httpx.MockTransport` rather than
against the provider's usage endpoint. That endpoint is itself rate-limited to
roughly 10 requests per 10 minutes, and it reported `0 of 1000 credits used`
after roughly 60 real searches, so it cannot settle what a run cost. The test
asserts what we control instead — one request per query, at the configured
depth, without raw content — at zero cost. See ADR-016.

`docs/milestones/m4-acceptance-ledger.md` records which criteria are verified,
by which batch, and what could not be verified at all.

## Browser end-to-end tests

34 Playwright specs drive the **real stack** — real API, real arq worker, real
Postgres and Redis — in the offline profile: the deterministic stub model
adapter and the committed fixture corpus. Nothing on the happy path is mocked,
so the journey is genuinely executed, and because the profile needs no
credentials and costs nothing the suite runs in CI on every push (ADR-018).

`scripts/e2e-stack.sh` **asserts** the profile before any test runs rather
than assuming it: a suite that silently ran against live providers would spend
a metered allowance and stop being deterministic, and several specs assert
fixture-specific labels.

| Spec | Covers |
|---|---|
| `journey.spec.ts` | The whole lifecycle: empty state, provenance, client validation, submission, all five stages reaching success, fixture-labelled sources, claim wording, rendered report, raw source, export locked, revision refused without a note, v2 produced, superseded version readable, approval, and the **downloaded Markdown** checked for its limitations and provenance |
| `navigation.spec.ts` | Deep links, reload, Back, the section in the URL, unknown paths, an invalid `view` parameter |
| `a11y.spec.ts` | axe-core WCAG 2.1 A/AA on five views, the journey driven by keyboard alone, announced validation errors, every control named |
| `responsive.spec.ts` | No horizontal page scroll at 390/834/1440 px across all sections, contained table overflow, gate placement on a phone, stacking versus side-by-side |
| `states.spec.ts` | Loading, empty-with-a-reason, aborted request with a working retry, 500 with correlation id, 429, failed task |
| `security.spec.ts` | Hostile report content in a real browser; hostile source title and URL; HttpOnly session cookie; the security headers and CSP the browser actually received; a clean journey under that policy; `rel="noopener"` on every external link |

Interception is used only in `states.spec.ts` and `security.spec.ts`, for
responses the real backend will not readily produce. The hostile payloads are
injected rather than committed: a corpus of working exploits in the repository
would be a hazard, and the backend's own injection tests already cover what
reaches the model.

### What the browser suite found

Both of these were real defects, not test friction:

- **A completed task rendered an empty UI.** The timeline, sources, claims and
  report all showed their empty states while the header correctly said
  "Awaiting approval". Polling stops the moment a task leaves `running`, so
  the last poll before that transition was the final word — and it happened
  before any of that data existed.
- **The page scrolled sideways by 7 px on a phone.** The single-column grid
  was `auto`-sized, so a long unbroken URL inside a card set the column's
  min-content width.

## Grounding evaluation

Seven cases scored against three invariants and two observations, on the
deterministic offline profile. Full description, including what it does *not*
measure: [`evaluation.md`](evaluation.md).

```bash
scripts/with-services.sh uv run python scripts/run_evals.py
```

Eleven tests in `test_evaluation.py` enforce it: eight prove each metric can
actually fail (a metric that cannot fail measures nothing), three run the
suite through the real graph.

## Abuse and failure behaviour

Two layers, because fakes and a real stack prove different things.

**In the suite** (`test_admission.py`, `test_abuse.py`, 35 tests): the four
admission gates including concurrency on separate connections, cross-session
access on all eight task routes, limits through HTTP, a dead queue, and error
responses checked for leaked configuration.

**Against the running stack** (`scripts/abuse-check.sh`, 21 checks):

```bash
scripts/e2e-stack.sh up
scripts/abuse-check.sh http://localhost:8080
```

It refuses to run against anything but the offline profile, so it cannot
hammer a live deployment. It covers concurrent submissions from one session,
isolation between sessions, burst refusal, a genuine `docker compose stop
redis` — the limiter fails open, the submission returns 503 with no broker
detail, readiness flips — and recovery without restarting the API.

It found the bug the unit tests could not: twelve simultaneous submissions
from one session were all admitted against a limit of five, because the unit
tests share a database session and that serialises them for free.

## Documentation audit

```bash
cd backend && uv run python ../scripts/audit-docs.py   # no services needed
```

Checks the claims that can be checked mechanically: every relative link
resolves, every documented script exists and is executable, `.env.example`
documents every settings field and no field that has been removed, and the
test counts quoted in the living documents match what the suites collect.

Milestone reports are deliberately exempt from the count check. Each is a
record of what was true at that milestone, and a figure that has since moved
on is history, not an error.

It found three real problems on its first run: a Mermaid diagram that had been
broken since M1 and had never rendered on GitHub, a repository contract still
claiming "M2 complete" three milestones later, and stale test counts in this
file.

## Known gaps

- **One browser engine.** The suite runs Chromium only. A second engine would
  roughly double the slowest CI job for a dashboard whose risky surface is
  Markdown rendering and ARIA semantics, neither of which is
  engine-specific.
- **No visual regression testing.** Layout is asserted by measurement (no
  horizontal scroll, relative element positions), not by screenshot
  comparison.
- **Nothing is verified against the shipped paid profile.** All live evidence
  concerns free preview models, which are materially weaker and measurably
  more verbose. It is not evidence about `anthropic/claude-sonnet-5`, and the
  paid cost estimate extrapolated from it is explicitly unverified.
- **Cost reconciliation is unexercised.** Every free call records `$0.0` for
  both our estimate and the gateway's reported figure, which is correct but
  never tests reconciling a real charge.
- **Injection tests prove containment, not prevention.** Whether a model obeys
  an injected instruction is not measured here and is not claimed anywhere.
- **The orphan sweep's timing is not tested end-to-end in CI.** Its logic is
  covered by four unit tests; the cron firing was verified by hand in Docker.
- **No performance or load measurements exist.** None are claimed anywhere in
  this repository.
- **Tavily credit consumption is not verifiable.** The provider's usage
  endpoint does not reflect our requests, so the ledger reports AgentFlow's
  own request count and says so.
- **Rate-limit and `Retry-After` handling against Tavily is covered only with
  mocked responses.** The live allowance was never exhausted, so that path is
  unexercised against the real provider.
- **Total retrieval failure is covered offline only.** Provoking it live would
  mean revoking the key mid-run.
- **Three demo-safety settings are declared but not enforced**
  (`DEMO_DAILY_BUDGET_USD`, `DEMO_MAX_TASKS_PER_SESSION`, `DEMO_ACCESS_CODE`).
  They are listed as unimplemented in `docs/security.md` and must be wired up
  or removed before any public deployment.
