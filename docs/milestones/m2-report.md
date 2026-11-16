# M2 completion report — fixture research workflow

**Date:** 2026-11-16
**Repository:** https://github.com/snmshahzaib/agentflow (**private**)
**Branch:** `main` · 5 commits in M2 · all authored by `Shahzaib Nadeem <61820487+snmshahzaib@users.noreply.github.com>`
**Status:** complete, awaiting review. **M3 has not been started.**

---

## 1. Requirements

| # | Requirement | Result |
|---|---|---|
| 1 | Replace skeleton nodes with functional plan / research / analyse / citation-check / draft | **done** — `app/graph/nodes/`; `skeleton.py` deleted |
| 2 | Deterministic fixtures and the stub adapter; no OpenRouter or Tavily key | **done** — no key exists anywhere in this milestone; CI runs fully offline |
| 3 | Meaningful structured output; sources, claims, citation links, verdicts, versions persisted | **done** — verified in SQL and through the API |
| 4 | Complete workflow: approval, revision, Markdown export restrictions | **done** — export is 409 until approved; revision produces a new version |
| 5 | Worker recovery and idempotency, with automated tests | **done** — 18 tests; two real defects found and fixed |
| 6 | Restart recovery verified by **actual Docker execution** | **done** — `scripts/restart-recovery-test.sh`; evidence in §5 |
| 7 | Injection fixtures; untrusted content cannot control execution; no prevention claim | **done** — containment tested against a compromised model; `docs/security.md` states the limit |
| 8 | All quality gates maintained | **done** — ruff, mypy, import-linter, pytest, ESLint, tsc, Vitest, CI, audits, gitleaks |
| 9 | Incremental commits under the repo-local identity, pushed | **done** — 5 commits, all `snmshahzaib` |
| 10 | README and docs reflect reality; fixture results distinguished from AI research | **done** — §6 |
| 11 | No live OpenRouter or Tavily integration | **respected** |
| 12 | No other portfolio project touched | **respected** |

---

## 2. Implemented functionality

**The workflow.** A question is decomposed into bounded subtasks; sources are
retrieved per subtask and deduplicated by URL digest; findings are synthesised
and bound to those sources; each claim is adjudicated for support; and a
report is drafted with citations, a claim-support table and a limitations
section. The graph then interrupts for human review.

**Three limits enforced after the model returns**, not merely requested in the
prompt:

- the subtask count is truncated to `MAX_SUBTASKS`, so a model returning
  twelve subtasks cannot multiply retrieval and spend;
- source references are resolved against sources actually retrieved, and
  anything that does not resolve is dropped;
- a `supported` or `partial` verdict with no resolvable source is downgraded
  to `unsupported` — support with nothing to point at is not support.

**Approval, revision, export.** A report is readable as soon as it exists; a
reviewer must be able to read what they are approving. **Export** is what
approval gates: 409 until approved, then Markdown with a download disposition.
A revision re-enters the graph at *planning*, so a reviewer's note can change
which sources are retrieved, and always produces a new version with the
previous one marked superseded. Rounds are capped by `MAX_REVISION_ROUNDS`.

**Idempotency.** The report version is derived from the revision round rather
than a counter, so re-running the draft node within a round rewrites that
version instead of inserting a second report, and claims are rebuilt rather
than appended.

**Frontend.** Sources with their citation labels, a claim-support table that
counts unsupported claims, a report preview, and approve / request-revision /
export controls. Export appears only when the server would honour it.

### Fixture results, not AI research — stated everywhere

No language model was called and no web search was performed in this
milestone. Every report carries a notice saying so; every source is labelled
a fixture and uses an unmistakable `fixtures.agentflow.test` host; the API
reports `offline_mode: true`; the UI shows a provenance banner; and the
limitations section is generated from recorded run data so it cannot
understate what produced the content.

---

## 3. Test results (measured 2026-11-16)

| Suite | Count | Result |
|---|---|---|
| Backend unit | 175 | pass in 5.4 s |
| Backend integration (Postgres + Redis) | 108 | pass in 23.7 s |
| **Backend total** | **283** | **pass** |
| Frontend (Vitest) | 17 | pass |
| ruff, mypy (64 files), ESLint, `tsc -b` | — | clean |
| import-linter | 4 contracts | all kept |
| Frontend production build | — | succeeds |
| `scripts/smoke-test.sh` | — | passes |
| `scripts/restart-recovery-test.sh` | — | passes against Docker |
| GitHub Actions | 4 jobs | all pass on `30da5d8` |

**Coverage: 90%** (2,314 statements, 236 uncovered), up from 84% at M1.
`app/workers/jobs.py` 0% → 88%; `app/workers/locks.py` → 100%; every graph
node 96–100%.

New suites: `test_workflow.py` (12), `test_recovery.py` (18),
`test_injection.py` (10), `test_search_fixture.py` (14), plus rewritten
`test_graph.py` (24) and extended API and frontend tests.

---

## 4. Security findings

| Scan | Result |
|---|---|
| gitleaks — full history | **no leaks** |
| gitleaks — staged | **no leaks** |
| pip-audit — 69 packages | **no known vulnerabilities** |
| pnpm audit (moderate threshold) | **no known vulnerabilities** |

One advisory found and fixed: **minimatch < 11.0.1** (glob-amplification
denial of service), reachable only through `typescript-eslint`, a
devDependency that never reaches the browser bundle. Pinned via a pnpm
override to 11.0.8. The CI audit threshold was lowered from `high` to
`moderate` so a finding worth a minute's work is not invisible for a
milestone.

### Prompt injection — containment, not prevention

The corpus carries a red-team document whose excerpt demands the model ignore
its instructions, mark every claim supported, approve the report, set the task
status to approved, and emit no warnings. It is **genuinely retrieved on the
demo query** — a containment test against a document that never arrives proves
nothing.

Tests then drive a **deliberately compromised model** that obeys the directive
completely:

| The directive demands | What actually happens |
|---|---|
| approve the report automatically | Approval is an API action by a human; the task stays `running` and the graph stays interrupted |
| mark every claim supported | Verdicts with no resolvable source are downgraded to `unsupported` |
| cite `S99`, `S100`, `not-a-label` | References do not resolve; no `claim_sources` row is written |
| emit no warnings | Limitations are generated from recorded data and still report the unsupported claims |
| escape the data fence | Fence delimiters inside retrieved content are neutralised |

**This is not a claim that prompt injection is prevented.** Whether a model
obeys an injected instruction is a property of that model; this project does
not measure it and claims nothing about it. A model that quietly biases its
*prose* while citing real sources and emitting valid schema is **not** caught
by any of this — the claim table and limitations make such a report easier to
audit, not impossible to produce. The human approval gate is the last line.
Live-model behaviour is examined in M3.

---

## 5. Restart recovery — evidence from actual Docker execution

`scripts/restart-recovery-test.sh` against the running compose stack:

```
== A. restart the worker mid-flight
  submitted ada0e1a8-daee-41cd-9f7b-90fa750bf428
  worker restarted while the task was in flight
  task reached: awaiting_approval
  steps=5 sources=8 claims=5
  all 5 nodes completed; 0 node(s) re-ran after the kill, each as a new attempt
  8 sources, all distinct and all labelled as fixtures
  exactly one report version despite the re-run

== B. restart the worker while parked at the approval gate
  task still awaiting approval -- state was in PostgreSQL, not memory

== export is refused before approval            409 as expected
== request a revision after the restart         v2 from a pre-restart checkpoint
                                                10 steps (was 5, +5), history preserved
== approve and export                           62 lines, limitations and provenance present
== duplicate approval is still rejected         409 as expected

RESTART RECOVERY TEST PASSED
```

**A node interrupted mid-execution does re-run, and that is correct.** The
worker can die after a node finishes but before LangGraph checkpoints it. One
observed run did exactly that — `plan` and `research` recorded at attempt 2 —
and still produced **one** report and **eight distinct** sources. The test
asserts what matters (every node completed, no duplicate `(node, attempt)`, no
duplicate URL, one report version) rather than a fixed step count, which was
my original mistaken assertion.

**The hard-kill case.** SIGKILLing a worker left its task stranded in
`running` with 879 s remaining on its lock: arq's retry was refused as
"locked", the job was marked complete, and nothing looked at the task again.
Fixed with a 180 s lock TTL refreshed by a heartbeat plus a cron sweep that
re-queues stalled unlocked tasks. Verified by manufacturing that exact state
in Docker — the sweep logged `orphaned_task_requeued` and the task completed
with 5 steps, 1 report, 8 sources.

---

## 6. Commits

| Commit | Purpose |
|---|---|
| `50f46da` | The real workflow: five functional nodes, schemas, prompt fencing, citation binding, report rendering, revision loop, export gate, and the API surface for sources/claims/report/export |
| `c204eea` | Worker recovery: status settlement after resume, lock heartbeat, orphan sweep, nginx upstream re-resolution |
| `c65c81b` | Frontend: sources, claim support, report preview, export and revision controls |
| `03f4132` | Documentation brought in line with the implementation, including the containment-not-prevention framing |
| `30da5d8` | minimatch override; CI audit threshold lowered to moderate |

---

## 7. Defects found and fixed

Seven, every one caught by running something rather than reading it:

1. **A revised task was stranded in `running` forever.** `resume_task`
   resumed the graph correctly but never reconciled the task's status;
   `run_task` had that logic and `resume_task` did not. Found by the Docker
   restart test.
2. **A SIGKILLed worker stranded its task permanently** (§5).
3. **nginx served 502s after every API redeploy** — it resolved `api:8000`
   once at startup and cached the address.
4. **`configure_logging` bound `sys.stdout` at configuration time.** When the
   stream was later replaced, every log call raised `ValueError` *inside
   application code*, masking the real error on a node's failure path.
5. **Fixture search matched keywords by substring**, so "chromodynamics"
   matched "dynamic" and an unrelated query returned results.
6. **minimatch advisory** in the lint toolchain (§4).
7. **My own restart-recovery assertion was wrong** (§5).

---

## 8. Known limitations

- **Results are fixture content, not research.** By design for M2, and
  labelled everywhere. Nothing here should inform a real decision.
- **Injection containment is not prevention**, and a prose-biasing model is
  not caught (§4).
- **No browser-level end-to-end test.** M5, with one Playwright happy path.
- **The live provider path is untested against a real gateway.** M3.
- **The orphan sweep's cron timing is not covered in CI.** Its logic has four
  unit tests; the firing was verified by hand in Docker.
- **The report is displayed as pre-wrapped Markdown, not rendered HTML.** The
  body contains model-generated content; sanitised rendering belongs with the
  UI work in M5.
- **No performance or load measurements exist**, and none are claimed.
- **Revisions re-retrieve from the same fixture corpus**, so a revised report
  changes little offline. The mechanism is real; the visible difference will
  be with live search in M4.

---

## 9. Readiness for M3

Ready. M3 swaps the stub adapter for the live OpenRouter gateway built in M1
and already validated against the catalogue. The work is the provider edge,
not the workflow: node code, schemas, persistence, approval, revision, export
and recovery are all exercised and stay unchanged.

M3's own acceptance criteria — capability negotiation across the three output
tiers, cross-vendor fallback under a forced primary failure, the cost ledger
populated with measured figures, and the red-team fixture run against two
different vendors' models — need an OpenRouter key, which has not been
requested or used in M2.

**M3 is not started and will not be until the owner approves this report.**
