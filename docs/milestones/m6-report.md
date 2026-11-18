# M6 completion report — documentation, verification and portfolio readiness

**Status: complete, awaiting review.**

M6 was the milestone that checked whether the previous five told the truth. Mostly they did,
and where they did not, the gap is now a failing check rather than a paragraph.

No paid service was used and no OpenRouter call was made. Everything below runs on the
deterministic stub adapter and the committed fixture corpus.

## 1. Acceptance criteria

| # | Criterion | Status | Evidence |
|---|---|---|---|
| A | Architecture documented as implemented | **met** | `docs/architecture.md`: process model, layers, component view, the model gateway, the search layer, scheduled work, state ownership, trust boundaries |
| B | Accurate Mermaid architecture and sequence diagrams | **met** | 6 diagrams, all parsed by Mermaid's own parser in CI. Two new sequence diagrams: a task end to end, and recovery after a worker dies |
| C | Setup verified from a clean checkout | **met** | Fresh clone, `cp .env.example .env`, `docker compose up --build`: five healthy containers, smoke test passed, 419 backend tests, 7/7 eval cases — no credentials at any step |
| D | `.env.example` complete; offline demo needs no credentials | **met** | Audited mechanically: every `Settings` field is documented and no removed field lingers. `LLM_PROVIDER=stub`, `SEARCH_PROVIDER=fixture` by default |
| E | All test commands documented | **met** | `docs/testing.md` and `docs/development.md`; each command run from the clean checkout before being written down |
| F | Portfolio-quality README | **met** | Problem, solution, capabilities, architecture, stack, quick start, 9 real screenshots, demo workflow, testing, security, limitations, status |
| G | Documentation audited against implementation | **met** | `scripts/audit-docs.py`, 94 claims checked, in CI |
| H | Automated evaluation for grounding and unsupported claims | **met** | 7 cases, 5 invariants, 2 observations; 11 enforcing tests; `docs/evaluation.md` |
| I | Evidence supports the claims made | **met** | Test counts now checked mechanically; four stale or broken claims found and fixed |
| J | Bundle investigated and improved | **met** | 151 kB → 102 kB gzipped initial load, measured |
| K | Firefox coverage for the critical journey | **met** | 34 browser tests: 33 Chromium, plus the journey on Firefox |
| L | Five deployment gates preserved, not marked complete | **met** | `docs/security.md` and the README both state them as open |
| M | Paid models disabled, zero-cost approach preserved | **met** | No live call made during M6; the paid profile remains present and disabled |

## 2. What the verification found

Five real problems. None would have been caught by reading.

**A Mermaid diagram had been broken since M1.** `docs/diagrams/approval-flow.md` contained
semicolons inside sequence-diagram text, which Mermaid treats as statement separators. It had
never rendered on GitHub — it showed an error box, in a file whose entire purpose is to explain
the durable approval design. Two diagrams I wrote during M6 had the same fault. `pnpm
check:diagrams` now parses every block with Mermaid's own parser, in CI.

**The documented test commands only worked by accident of this machine.** `conftest.py` and the
dev tools default to `localhost:5432` and `localhost:6379`. On the author's machine
`localhost:5432` is a *Homebrew* Postgres holding unrelated work databases; the Compose
Postgres publishes on 5434 with a password from the untracked `.env`. Every "integration tests
pass" claim through M5 was true, but true of a database no reader could reproduce — and a
developer with a local Postgres would have got a silent connection to the wrong server rather
than an error. `scripts/with-services.sh` now derives the connection strings from
`docker compose port` and `.env`, and creates the databases if absent.

**The repository contract was three milestones stale.** `CLAUDE.md` still announced "Milestone:
M2 complete — M3 may not begin without approval" while M5 was finished.

**Test counts in `docs/testing.md` had drifted.** 408 where 419 was true, 33 where 34 was true.
Now checked mechanically against what the suites collect.

**The evaluation harness was wrong twice before it was right** — see below.

## 3. The grounding evaluation

Seven cases through the real graph, five invariants that must hold, two observations reported
without a threshold:

```
case                    src  clm   cite   grnd   uniq  unsup   util
broad_evidence            9    5   100%   100%   100%    20%    44%
single_subject            5    5   100%   100%   100%    20%    80%
scarce_evidence           1    5   100%   100%   100%    80%   100%
no_methodology            1    5   100%   100%   100%    80%   100%
off_corpus                2    5   100%   100%   100%    60%   100%
injected_evidence         5    5   100%   100%   100%    20%    80%
duplicate_pressure        5    5   100%   100%   100%    20%    80%
```

As the evidence shrinks from nine sources to one, the share of claims the system declines to
support rises from 20% to 80%, while citation integrity never leaves 100%. A claim citing `S4`
when retrieval returned one source is downgraded, not asserted.

**It measures the grounding pipeline, not model quality**, and says so in its own package
docstring, in `docs/evaluation.md` and in the README. On the offline profile the stub's claims
are fixed; what varies per case is the evidence.

Two mistakes, both found by running it:

- The first version let the stub plan each case. The stub planner returns the same four search
  queries whatever it is asked, so all seven cases retrieved an identical eight sources — seven
  cases measuring one thing. Each case now carries its own plan.
- Two metrics asserted things the report never claimed: a version line that only appears after
  a revision, and a subtask count derived from stored sources rather than from the plan. Both
  failed against correct output. The report was right; the harness was wrong.

Eleven tests enforce it, eight of which prove a metric can actually fail. A metric that cannot
fail measures nothing.

## 4. Frontend follow-up

The 151 kB gzipped bundle, measured with `rollup-plugin-visualizer`:

| | gzip |
|---|---|
| react-dom | ~101 kB |
| react-router | ~27 kB |
| our source | ~21 kB |
| TanStack Query | ~22 kB |
| unified/remark/micromark (Markdown) | the rest |

The Markdown stack is useless until a report is on screen, and the first page a visitor loads
has no report. `React.lazy` moves it to a chunk loaded on demand:

```
before   index 490.64 kB raw / 151.30 kB gzip
after    index 327.14 kB raw / 101.82 kB gzip   (-33%)
         Markdown 164.96 kB raw / 49.43 kB gzip, on demand
```

Two findings recorded rather than acted on:

- **react-router costs 27 kB gzip for two routes and one query parameter.** Poor value, but
  replacing a standard, battle-tested router with hand-rolled history handling to save that on
  an internal dashboard is the wrong trade.
- **`mdast-util-to-markdown` (16 kB) is dead weight** — it serialises Markdown and we only
  parse. Removing it means replacing `remark-gfm` with a table-only plugin, risking the one GFM
  feature reports actually depend on. It now sits in the deferred chunk, so it costs nothing on
  first load.

Firefox runs the critical journey — the spec where an engine difference would show (form
submission, polling, a real download, the rendered report). Running all 33 specs twice would
roughly double the slowest CI job to re-prove what is engine-independent by construction.

## 5. Tests and coverage

| Suite | Count | Notes |
|---|---|---|
| Backend unit | 282 | no database needed |
| Backend integration | 137 | real Postgres and Redis |
| **Backend total** | **419** | 90% line coverage, 2,699 statements, 257 uncovered |
| Frontend components | 58 | |
| Browser end-to-end | 34 | Chromium 33 + Firefox journey |
| Grounding evaluation | 7 cases | all invariants hold |
| Live provider | 20 | skipped without credentials; M3/M4 evidence preserved |
| Documentation audit | 94 claims | links, scripts, settings, test counts |
| Mermaid diagrams | 6 | all parse |

CI runs seven checks per push: secret scan, backend (lint, types, boundaries, tests, evaluation,
docs audit), frontend (lint, types, tests, build, diagrams), browser end-to-end against the real
stack, and the dependency audit.

## 6. Documentation changes

| Document | Change |
|---|---|
| `README.md` | Rewritten for a portfolio reader: problem, solution, capabilities, 9 screenshots of the real application, the evaluation result, limitations, status |
| `docs/architecture.md` | Two sequence diagrams, a model-gateway section, stale milestone header corrected |
| `docs/evaluation.md` | **New.** What the harness measures, what it does not, the cases, the metrics, the baseline |
| `docs/development.md` | Service-connection guidance, the clean-checkout verification table, the four databases |
| `docs/testing.md` | Evaluation and audit sections; counts corrected and now machine-checked |
| `docs/diagrams/approval-flow.md` | Fixed a diagram that had never rendered |
| `CLAUDE.md` | Status brought current from M2 to M5/M6 |
| `docs/screenshots/` | **New.** 9 images captured from the running application |

## 7. What is still not true

- **Not deployed.** Five gates remain open and are not marked complete: access control for live
  research, a daily spend ceiling, per-session task limits, rate limiting reviewed under real
  traffic, and hosted secret handling. Three are settings that currently do nothing.
- **No performance or load measurement.** None is claimed.
- **No screen-reader testing** with VoiceOver or NVDA.
- **No live-provider evaluation baseline.** The harness is provider-agnostic but has never run
  against a live model.
- **Retention has still never deleted a real expired task** — nothing anywhere is seven days
  old.
- **One browser engine for 33 of 34 specs.**
- **The paid model profile** remains present, disabled and untested.

## 8. Commits

```
ee683e8  perf(frontend): defer the Markdown renderer, add Firefox journey coverage
dee9164  feat(evals): grounding evaluation on deterministic fixtures
86f5c2a  docs: portfolio README, architecture diagrams, and an audit that enforces them
```

(plus this report and the reproducibility documentation)

## 9. Readiness for M7

M7 is deployment, and M6 did not soften its entry conditions. The five gates are open, the
secret-handling story for a hosted environment is unwritten, and nothing has run outside a local
Docker host. What M6 does provide is a reproducible setup verified from a clean checkout, so the
deployment work starts from a known-good baseline rather than from this machine's accidents.
