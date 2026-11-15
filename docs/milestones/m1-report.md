# M1 completion report — walking skeleton and LLM config spine

**Date:** 2026-11-15
**Repository:** https://github.com/snmshahzaib/agentflow (**private**)
**Branch:** `main` · 12 commits · all authored by `Shahzaib Nadeem <61820487+snmshahzaib@users.noreply.github.com>`
**Status:** complete, awaiting the owner's review. **M2 has not been started.**

---

## 1. Acceptance criteria

The M1 bar from `planning/agentflow-refinement-proposal-v2.md`:

| Criterion | Result | Evidence |
|---|---|---|
| `docker compose up --build` from clean → UI loads, migrations apply, `/ready` green | **met** | 6 services build; one-shot `migrate` runs `08efb1206ecb` before API/worker start; `/ready` returns `ready: true` for database, queue and llm; nginx serves the app at :8080 |
| `POST /api/tasks` → worker claims → step row written → status reaches `awaiting_approval` | **met** | `scripts/smoke-test.sh` passes through the nginx origin: 5 step rows, `queued → running → awaiting_approval → approved` |
| Model config resolves per node from env; validation fails fast on a bad model ID | **met** | `Settings.model_for_node`; startup validation reports every problem at once naming the offending variable; covered by 20 tests |
| `/api/meta` reports resolved models | **met** | returns provider, search mode, offline flag, catalogue source and the resolved model per node |
| Verified default slugs in `.env.example` + `docs/decisions.md` | **met** | verified live against OpenRouter's catalogue (467 models enumerated) with measured prices and capability flags |
| CI green (ruff, mypy, import-linter, pytest, vitest) | **met** | 4/4 jobs pass on `d7f6c58` |

Delivered beyond the M1 bar, because it de-risked the hardest integration: the
approval gate and the resume path are wired end to end, not just the run path.

---

## 2. Commits

| Commit | Purpose |
|---|---|
| `5585e2a` | Project structure, uv lockfile, import-linter contracts, gitleaks + pre-commit, documented `.env.example` files, ADRs 001–007 |
| `6baa0c8` | Resolve `uv` explicitly in git hooks — hooks get a minimal PATH and failed on push |
| `e7b695b` | Domain types, `Settings` with startup guards, structured logging with credential redaction |
| `3247945` | GitHub Actions: secret scan, backend quality gates, advisory dependency audit |
| `6f2be58` | Make tests hermetic (ambient `DATABASE_URL` was overriding explicit test values); scope the audit |
| `f2788b3` | Upgrade past 6 known advisories in langgraph and starlette |
| `ae3f94d` | 9-table data model, initial migration, schema invariant tests |
| `49fffa1` | Provider-agnostic LLM gateway with capability negotiation and cross-vendor fallback |
| `2eadc01` | API, arq worker and LangGraph graph: durable flow from submission to approval |
| `dc2d2cd` | Frontend dashboard shell with real states and a provenance banner; frontend CI job |
| `56e11be` | Docker stack, generated API docs, measured testing docs, 3 skills + 2 review agents |
| `d7f6c58` | Fix container healthchecks, the secret-scan gate and ignore rules |

---

## 3. What works

**Durable execution.** A task is submitted (202 + `Location`, `Idempotency-Key`
honoured), enqueued to Redis, claimed by the worker under a single-flight lock,
and run through a five-node LangGraph graph that records a `workflow_steps` row
per node. The graph then calls `interrupt()` and **the job ends** — the human
pause costs no running compute. Approval writes an `approval_event` and
enqueues a resume job, which continues from the Postgres checkpoint. Verified
as a genuinely separate job with a different correlation id, so the state came
from the database rather than memory.

**Provider-agnostic LLM gateway.** One `LLMClient` port; `openai_compat.py` is
the only module that imports a vendor SDK, enforced by an import-linter
contract. Three output tiers negotiated per model (native JSON-Schema → forced
function as schema carrier → prompt-constrained JSON), with tier 3 refused
while `LLM_REQUIRE_STRUCTURED_OUTPUT` is true because schema-constrained output
is part of the prompt-injection boundary. Startup validation checks existence,
context floor, structured-output support and usable pricing against the live
catalogue, falling back to a committed snapshot. Cross-vendor fallback with
exponential backoff and full jitter; an unusable fallback is dropped rather
than taking down a working primary. Every call records the model actually
served, the tier used, tokens, latency, our cost estimate and the gateway's
reported figure.

**Honesty mechanics.** `search_mode` and `llm_provider` are recorded per task.
The UI carries a provenance banner stating when output comes from fixtures and
a stub. Node descriptions say the citation check confirms a claim points at a
retrieved source, not that it is true — asserted by three frontend tests.
Every M1 step row is marked `skeleton`.

**Operational basics.** One error envelope with correlation ids; structured JSON
logs with credential redaction; `/health` and `/ready`; session-scoped access
(another session gets 404, not 403); keyset pagination; CI-enforced layering.

## What does not work yet — stated plainly

**The five graph nodes are skeletons.** They record the timeline and prove the
durable path; they do not plan, retrieve, analyse, check citations or draft.
There are no sources, reports or claims yet. Real implementations are M2.

Not built: revision loop, export, sources/claims/report endpoints, live search,
rate limiting, demo budget enforcement, retention job, BYOK. All are scheduled
and none is stubbed in a way that could look finished.

---

## 4. Test and CI results (measured 2026-11-15)

| Gate | Result |
|---|---|
| Backend unit (`-m "not integration"`) | **140 pass** |
| Backend integration (Postgres + Redis) | **55 pass** |
| Backend total | **195 pass in 7.5 s**, 84% line coverage |
| Frontend (Vitest) | **10 pass** |
| ruff check + format | clean |
| mypy (52 files) | clean |
| import-linter | **4 contracts, all kept** |
| ESLint (`--max-warnings 0`) / `tsc -b` | clean |
| Frontend production build | succeeds (272 kB, 84 kB gzipped) |
| End-to-end smoke test | **passes** through nginx against the Docker stack |
| GitHub Actions on `d7f6c58` | **4/4 jobs pass** |

Code: 50 backend modules (3,988 lines), 11 test files (2,396 lines), 19
frontend source files, 9 documents (909 lines), 134 tracked files.

## Security scan results

| Scan | Result |
|---|---|
| gitleaks — full git history | **no leaks** |
| gitleaks — staged content | **no leaks** |
| gitleaks — working tree (CI, clean checkout) | **no leaks** |
| pip-audit — 69 packages | **no known vulnerabilities** |
| pnpm audit (high) | clean |
| Secrets in the repository | none; only `*.example` files are tracked |

Six advisories were found and fixed during M1, in packages this project
depends on for durable checkpointing: `langgraph-checkpoint-postgres`
2.0.25 → 3.2.1 (PYSEC-2026-3635, CVE-2026-115883), `langgraph-checkpoint`
2.1.2 → 4.3.2 (PYSEC-2026-2573/2574), `langgraph-sdk` 0.3.15 → 0.4.14
(PYSEC-2026-4240), and `starlette` 0.49.3 → 1.10.0 (PYSEC-2026-248/249,
2280/2281), the last requiring FastAPI 0.120 → 0.163. Security floors are now
explicit in `pyproject.toml` with the advisory IDs in a comment.

---

## 5. Defects found and fixed during M1

Thirteen, every one caught by running something rather than reading it. The
ones worth noting:

1. **Enum columns had no CHECK constraint.** SQLAlchemy's `native_enum=False`
   defaults `create_constraint` to `False`, so the schema was a bare VARCHAR
   any raw SQL could write nonsense into. A test now asserts the constraint
   exists in `pg_constraint` — and it had to be rewritten to use raw SQL,
   because going through the ORM only proved client-side validation and would
   have passed against a database with no constraint at all.
2. **Validation named a variable that does not exist.** A misconfigured
   analyse node told the developer to set `LLM_MODEL_ANALYSE`; the real
   variable is `LLM_MODEL_ANALYST`. Settings and the error messages now read
   one authoritative mapping, with a test asserting every entry matches a real
   field.
3. **The log redactor destroyed the cost ledger.** Matching "token" as a
   substring redacted `tokens_in`. Matching is now segment-based with a safe
   list.
4. **Tests were not hermetic.** `_env_file=None` disables dotenv but
   pydantic-settings still reads real environment variables, so CI's
   `DATABASE_URL` silently overrode a value a unit test passed explicitly —
   the test passed locally and failed in CI.
5. **Server defaults were not fetched at flush**, so serialising a task
   triggered lazy IO and raised `MissingGreenlet` under async SQLAlchemy.
   `eager_defaults` fixed it and cut the API suite from 65 s to 3 s.
6. **Two container healthchecks reported unhealthy while working** — `pgrep`
   is absent from `python:slim`, and `localhost` resolves to `::1` while nginx
   listened on IPv4 only. On a real platform that means restart loops.
7. **The readiness endpoint bypassed dependency injection**, making it
   untestable; and the `TaskQueue` port did not declare `ping()`, which
   readiness was already calling.
8. **My own layering model was wrong** — I had `app.graph` as a peer of
   `app.workers`, but the worker drives the graph.
9. **A whitespace-only model override** resolved to an empty model ID instead
   of falling back to the default.

---

## 6. Remaining issues

| Issue | Severity | Plan |
|---|---|---|
| `app/workers/jobs.py` has 0% unit coverage — exercised only by the live smoke test | medium | M2, where restart recovery is an acceptance criterion |
| One `type: ignore` in `graph/builder.py`: LangGraph's stubs reject an async node callable its runtime accepts | low | `warn_unused_ignores` makes it self-clearing when upstream widens the stubs; a test guards the runtime behaviour |
| Live provider path untested against a real gateway | expected | M3, with the OpenRouter key |
| No browser end-to-end test | expected | M5, one Playwright happy path |
| Default model slugs validated against the catalogue but never actually called | expected | M3 re-validates and records measured cost |
| No performance measurements | by design | none are claimed anywhere in the repository |

---

## 7. Readiness for M2

Ready. M2 implements the real nodes — planner, search adapter, analyst,
citation check and draft — on the foundations M1 proved: the LLM port with its
stub adapter, the step-persistence pattern, the durable interrupt, the
`report_claims` tables that make claim support queryable, and the `llm_calls`
ledger.

M2 needs **no API keys**: fixtures and the stub adapter carry it. Its
acceptance criteria are plan acceptance tests 1, 3, 4 and 5 — export refused
until approved, a revision creating v2 with approval history preserved, and
`docker compose restart worker` mid-run resuming with no duplicate steps or
reports.

**M2 is not started and will not be until the owner approves this report.**
