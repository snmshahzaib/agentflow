# Testing

Every number on this page came from a run on 2026-11-16. Nothing here is
projected or aspirational; where coverage is missing, it says so.

## Commands

```bash
cd backend
uv run pytest                        # all 298 offline tests
uv run pytest -m "not integration"   # 190 tests, no Postgres or Redis needed
uv run pytest -m integration         # 108 tests, needs Postgres + Redis
uv run pytest -m live                # billable; skipped unless a provider key is set
uv run pytest --cov=app --cov-report=term
uv run pytest tests/test_llm_registry.py::TestStubRuntime -q   # one class
```

```bash
cd frontend
pnpm test            # 10 Vitest tests
pnpm lint            # ESLint, --max-warnings 0
pnpm typecheck       # tsc -b
```

```bash
make verify                          # lint, types, boundaries, secrets, tests
scripts/smoke-test.sh                # end-to-end against a running stack
scripts/restart-recovery-test.sh     # worker restart recovery, real Docker
```

## Results (measured 2026-11-16)

| Suite | Count | Result |
|---|---|---|
| Backend unit (`-m "not integration"`) | 190 | pass in 5.1 s |
| Backend integration (Postgres + Redis) | 108 | pass in 22.3 s |
| **Backend total (offline)** | **298** | **pass** |
| Live provider (`-m live`) | 10 | **all pass** against the free profile, $0.00 spend |
| Frontend (Vitest) | 17 | pass |
| ESLint (`--max-warnings 0`) / `tsc -b` | — | clean |
| Frontend production build | — | succeeds |
| ruff check + format | — | clean |
| mypy (64 source files) | — | clean |
| import-linter | 4 contracts | all kept |
| gitleaks (history + staged) | — | no leaks |
| pip-audit | 69 packages | no known vulnerabilities |
| `scripts/smoke-test.sh` | — | passes |
| `scripts/restart-recovery-test.sh` | — | passes against Docker |

Backend line coverage: **90%** (2,314 statements, 236 uncovered), up from 84%
at M1. `app/workers/jobs.py` went from 0% to 88% and `app/workers/locks.py`
to 100%.

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

**Locking** (`test_worker_locks.py`) — contention, release on exception,
refusing to release a lock no longer owned, the TTL, and the heartbeat that
keeps a long task's lock alive and stops when another worker takes over.

**Locking** (`test_worker_locks.py`) — a second holder is refused while the
first holds the lock; the lock is released even when the body raises, so a
crashing job cannot leave a task permanently locked; a holder cannot release
a lock it no longer owns; and the TTL is set so a crashed worker frees it.

**API** (`test_api.py`) — 202 with a `Location` header on submission;
idempotency; validation errors in the standard envelope; session scoping
(another session gets 404, not 403); the timeline exposing failed attempts;
approval only from `awaiting_approval`, with 409 otherwise; and correlation-id
echo including sanitising an over-long inbound header.

**Injection containment** (`test_injection.py`) — see `docs/security.md` for
what these do and do not establish. They confirm the red-team document is
really retrieved, that injected text cannot escape the data fence, that the
model is never given tools, and then drive a **deliberately compromised
model** that obeys the injected directive completely: it still cannot
self-approve, its fabricated citations do not resolve, its "everything is
supported" verdicts are downgraded, and the limitations section still reports
the truth.

**Frontend** (Vitest) — the provenance banner states that offline output is
not from a live model; node descriptions never claim a claim is verified as
true; the claim table says support is not a truth judgement and counts
unsupported claims; the export link is absent before approval and present
after; a retried node stays visible in the timeline; client-side validation
blocks a request rather than relying on a 422; and API errors surface their
correlation id.

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

## Known gaps

- **No browser-level end-to-end test yet.** Planned for M5 (one Playwright
  happy path), once there is a full UI to drive.
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
