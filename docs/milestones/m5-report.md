# M5 completion report — review interface and browser verification

**Status: complete, awaiting review.**

The dashboard is now built around the decision a reviewer has to make, and the
claim that it works is backed by 33 Playwright specs that drive the real stack
rather than by screenshots. Four real defects were found by those tests, one of
them a security problem that had been invisible since M1.

No paid service was used. No OpenRouter call was made: the browser suite runs
against the deterministic stub adapter and the committed fixture corpus, which
is also what makes it free to run in CI on every push.

## 1. What was built

**Addressability.** `/tasks/:taskId?view=report|sources|claims|workflow`. The
task id is a path segment and the open section a query parameter, so a reviewer
can link a colleague to exactly what they are looking at, the Back button
works, and a reload no longer drops the selection — it previously lived in
component state (ADR-020).

**The report reads as a document.** Markdown is rendered, so the claim-support
table is a real table and citations are real links, with the exact export text
one click away. A reviewer approving a document should be able to see precisely
what they are approving. Earlier versions stay readable through a version
switcher using the `?version=` parameter the backend has always accepted, and a
superseded version says so while open.

**Rendering is treated as rendering hostile input** (ADR-019). The report is
model-written text quoting retrieved web pages. No raw HTML is parsed, an
explicit `http`/`https`/`mailto` allowlist means a `javascript:` or `data:`
href cannot become clickable, and every link carries
`rel="noopener noreferrer"`. The same allowlist is applied to stored source
URLs, where an unsafe one is shown as text and labelled rather than dropped.

**Progress is named.** "Stage 2 of 5: Retrieve sources" rather than a spinner,
in a polite live region.

**The gate states its consequences.** Approving ends the run and unlocks
export; requesting a revision re-plans the research and names the version it
will produce. A revision still requires a note, and now moves focus to the
field when one is missing. The gate sits above the tabbed evidence, not below
it — on a phone it previously sat several screens below the report it applied
to.

**States say why.** Skeletons shaped like the content they replace and carrying
their message as an accessible name; empty sections that explain they are empty
because a step has not run, not because something broke; errors with the
correlation id a user can quote and a retry that performs a real request.

## 2. Tests

| Suite | Result |
|---|---|
| Backend (unit + integration) | **408 passed**, 20 skipped — unchanged by M5 |
| Frontend components (Vitest) | **58 passed** (was 17) |
| Browser end-to-end (Playwright) | **33 passed** |
| axe-core WCAG 2.1 A/AA, five views | **zero violations** |
| ruff, ruff format, mypy (69 files), import-linter (4 contracts) | clean |
| ESLint (`--max-warnings 0`), `tsc -b`, production build | clean |
| `scripts/smoke-test.sh`, `scripts/restart-recovery-test.sh` | pass against Docker |
| `pip-audit --strict`, `pnpm audit` | clean, no accepted exceptions |

The browser suite drives the **real stack** — real API, real arq worker, real
Postgres and Redis — in the offline profile. Nothing on the happy path is
mocked (ADR-018). `scripts/e2e-stack.sh` asserts the profile before any test
runs, because a suite that silently ran against live providers would spend a
metered allowance and stop being deterministic.

| Spec | Tests | Covers |
|---|---|---|
| `journey.spec.ts` | 1 | The whole lifecycle in one run: empty state, provenance, client validation, submission, five stages reaching success, fixture-labelled sources, claim wording, rendered report, raw source, export locked, revision refused without a note, v2, superseded version readable, approval, and the **bytes of the downloaded Markdown** |
| `a11y.spec.ts` | 8 | axe on five views; the journey driven by keyboard alone; announced validation; every control named |
| `states.spec.ts` | 7 | Loading, empty-with-a-reason, aborted request with a working retry, 500 with correlation id, 429, failed task |
| `security.spec.ts` | 6 | Hostile report content in a real engine; hostile source title and URL; HttpOnly cookie; served security headers; a clean run under CSP; `rel="noopener"` |
| `responsive.spec.ts` | 6 | No horizontal page scroll at 390/834/1440 px across all sections; contained table overflow; gate placement; stacking |
| `navigation.spec.ts` | 5 | Deep links, reload, Back, section in the URL, unknown paths, invalid `view` |

Interception is used only in `states.spec.ts` and `security.spec.ts`, for
responses the real backend will not readily produce. Hostile payloads are
injected by the tests rather than committed: a corpus of working exploits in
the repository would be a hazard, and the backend's own injection tests already
cover what reaches the model.

A new CI job runs the suite on every push, with container logs and the HTML
report uploaded on failure.

## 3. Four defects found by running it in a browser

**A completed task rendered an empty UI.** The timeline, sources, claims and
report all showed their empty states while the header correctly read "Awaiting
approval". Polling stops the moment a task leaves `running`, so the last poll
before that transition was the final word — and it happened before any of that
data existed. The task's status is now part of the query key for everything
scoped to a task, so arriving at the gate is itself a refetch, with
`keepPreviousData` so the key change does not flash a loading state over
content already on screen. Two unit tests pin it.

This is the clearest argument for ADR-018: a mocked test would have returned
the data the real client never fetched.

**The security headers were configured but not served.** nginx *replaces*
inherited `add_header` directives as soon as a block declares one of its own.
The cache-control block for `/index.html` declared one, and a request for `/`
is answered by that block through an internal rewrite — so
`X-Content-Type-Options`, `X-Frame-Options` and `Referrer-Policy` were absent
from exactly the pages that mattered, while looking correct in the config file
since M1. Found by reading the headers the browser actually received. Fixed,
and a test now asserts them from the response rather than the configuration.

A Content-Security-Policy was added at the same time, with no `unsafe-inline`
and no `unsafe-eval` — affordable because the bundle is self-contained. A
browser test drives the whole journey under it and fails on any violation,
because a policy the application violates is a policy someone eventually
loosens.

**The page scrolled sideways by 7 px on a phone.** The single-column grid was
`auto`-sized, so a long unbroken URL inside a card set the column's min-content
width. `grid-cols-1` clamps it with `minmax(0,1fr)` — which is exactly why the
`lg` template already did.

**The report fought the page's heading outline.** A report's own `#` was
rendered as an `h1` inside a page that already had one, putting nine `h2`s in a
single panel. Headings are now shifted down so a report nests under the task it
belongs to. This is a real navigation problem for anyone moving by heading, and
it surfaced because a test tried to address "the task heading" and found nine
candidates.

### Two process notes worth recording

**A diagnostic spec with an unused import broke the Docker image build.**
`tsc -b` covers the e2e directory, the image build runs `tsc -b && vite build`,
and compose kept serving the previous bundle — so a correct fix appeared not to
work. The image build fails closed; compose does not.

**The frontend image was installing Playwright and downloading a browser.**
Into a production image that will never run a test. The build then failed on
the resulting flaky download. Now `--ignore-scripts`.

## 4. What is verified, and what is not

**Verified by runs recorded above:**

- The complete journey through a browser, ending in the exported Markdown's
  actual bytes.
- Zero axe-core WCAG 2.1 A/AA violations on the empty dashboard and all four
  detail sections.
- The journey operable by keyboard alone, including the skip link, submission,
  arrow-key section switching and approval.
- No horizontal page scroll at three viewports across every section.
- Hostile report content neither executing nor becoming clickable, in a real
  browser engine.
- Security headers and a strict CSP reaching the browser, with the application
  running clean under the policy.
- No backend regression: 408 tests unchanged.

**Not verified, and why:**

- **One browser engine.** Chromium only. A second would roughly double the
  slowest CI job for a dashboard whose risky surface is Markdown rendering and
  ARIA semantics, neither of which is engine-specific.
- **No screen-reader testing with an actual screen reader.** axe-core and the
  ARIA assertions check the semantics are present and correct; they do not
  establish that the experience is good with VoiceOver or NVDA. That is a
  manual exercise and has not been done.
- **No visual regression testing.** Layout is asserted by measurement, not
  screenshot comparison.
- **No performance or load measurement.** The bundle is 151 kB gzipped, up from
  85 kB, for `react-markdown`, `remark-gfm` and the router. No other
  performance figure is claimed anywhere.
- **Live-model behaviour in the UI is unexercised since M4.** OpenRouter's free
  allowance is spent, and M5 deliberately avoided consuming it. The UI paths
  are provider-agnostic, and the live/fixture distinction is driven by data the
  backend already returns per source — but no browser test has watched a live
  run.
- **The paid model profile** remains present, disabled and untested.

## 5. Security follow-up

The three unenforced demo settings remain unenforced and are now documented as
a numbered **deployment gate** in `docs/security.md`, alongside the two items
that are not settings:

| # | Blocker | State |
|---|---|---|
| 1 | Access control for live research | **not implemented** |
| 2 | Daily spend ceiling across all visitors | **not implemented** |
| 3 | Per-session task limit | **not implemented** |
| 4 | Request rate limiting | implemented, needs review under real traffic |
| 5 | Secret handling for a hosted environment | not reviewed |

**The application must not be made publicly accessible until all five are
addressed.** It has never run anywhere but a local Docker host.

The `picomatch` advisory tracked since M2 was **withdrawn upstream on
2026-11-18**. `pnpm audit` is clean with `picomatch` 4.0.7 still installed, and
the CI exception was removed rather than left in place — an exception list is
only safe while something keeps checking it. There are now no accepted audit
exceptions in either ecosystem.

## 6. Readiness for M6

The UI is complete for the MVP's scope and verified in a browser. What M6
inherits: no screen-reader pass, one browser engine, no visual regression
safety net, and a UI whose live-provider paths have never been watched running
live. Retention is implemented but has still never deleted a genuinely expired
task, because nothing in any environment is seven days old yet.
