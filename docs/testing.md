# Testing

Every number on this page came from a run on 2026-11-15. Nothing here is
projected or aspirational; where coverage is missing, it says so.

## Commands

```bash
cd backend
uv run pytest                        # all 195 tests
uv run pytest -m "not integration"   # 140 tests, no Postgres or Redis needed
uv run pytest -m integration         # 55 tests, needs Postgres + Redis
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
make verify                 # lint, types, import boundaries, secrets, tests
scripts/smoke-test.sh       # end-to-end against a running stack
```

## Results (measured 2026-11-15)

| Suite | Count | Result |
|---|---|---|
| Backend unit (`-m "not integration"`) | 140 | pass |
| Backend integration (Postgres + Redis) | 55 | pass |
| **Backend total** | **195** | **pass in 10.6 s** |
| Frontend (Vitest) | 10 | pass in 1.9 s |
| ESLint (`--max-warnings 0`) | — | clean |
| `tsc -b` | — | clean |
| ruff check + format | — | clean |
| mypy (52 source files) | — | clean |
| import-linter | 4 contracts | all kept |
| gitleaks (tree + full history) | — | no leaks |
| pip-audit | 69 packages | no known vulnerabilities |
| End-to-end smoke test | — | pass |

Backend line coverage: **84%** (1,599 statements, 251 uncovered).

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

**Graph** (`test_graph.py`) — the graph compiles with every node wired in the
documented order (which also guards the single `type: ignore` in
`builder.py`: LangGraph's stubs reject an async node callable its runtime
accepts, so a stub change fails loudly); a node records a step and advances
the task in one transaction; re-running a node records a new attempt rather
than overwriting history; and the research node performs no model call.

**Locking** (`test_worker_locks.py`) — a second holder is refused while the
first holds the lock; the lock is released even when the body raises, so a
crashing job cannot leave a task permanently locked; a holder cannot release
a lock it no longer owns; and the TTL is set so a crashed worker frees it.

**API** (`test_api.py`) — 202 with a `Location` header on submission;
idempotency; validation errors in the standard envelope; session scoping
(another session gets 404, not 403); the timeline exposing failed attempts;
approval only from `awaiting_approval`, with 409 otherwise; and correlation-id
echo including sanitising an over-long inbound header.

**Frontend** (Vitest) — the provenance banner states that offline output is
not from a live model; node descriptions never claim a claim is verified as
true; a retried node stays visible in the timeline; client-side validation
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

## Known gaps

- **`app/workers/jobs.py` has 0% unit coverage.** The arq job handlers are
  exercised only by `scripts/smoke-test.sh` against a live stack. Covering
  them needs a fake arq context and checkpointer; it belongs with M2, where
  restart recovery is an acceptance criterion.
- **No browser-level end-to-end test yet.** Planned for M5 (one Playwright
  happy path), once there is a full UI to drive.
- **The live provider path is untested against a real gateway.** Every
  gateway test drives the real adapter logic against a fake transport. A
  genuine OpenRouter call is M3, which is also where the injection red-team
  test runs against two different vendors' models.
- **No performance or load measurements exist.** None are claimed anywhere in
  this repository.
