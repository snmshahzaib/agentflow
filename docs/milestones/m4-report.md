# M4 completion report — live web research integration

**Status: complete for the free profile, awaiting review.**

AgentFlow now performs real web retrieval through Tavily and produces reports
grounded in live third-party pages, with provenance that distinguishes what was
*retrieved* from what was *verified*. The approval gate, revision loop,
citation checking and Markdown export were all re-verified against live data
rather than fixtures.

Every claim below is backed by a run that happened. Where something was not
verified, it says so.

- Acceptance criteria, batch by batch: [`m4-acceptance-ledger.md`](m4-acceptance-ledger.md)
- Machine-readable evidence: [`m4-live-evidence.json`](m4-live-evidence.json)

## 1. Configuration, unchanged from what was approved

| Setting | Value | Note |
|---|---|---|
| Search provider | `tavily` | Approved in the M4 options evaluation |
| Search depth | `basic` | 1 credit per search |
| `SEARCH_MAX_PER_TASK` | `8` | Approved cap, enforced locally |
| `SEARCH_MAX_RESULTS_PER_SUBTASK` | `4` | |
| Retrieval failure policy | **fail the node, never substitute** | Approved; a failed search is never replaced with fixture data |
| Test budget | 150 credits | Approved |
| `LLM_FREE_MODELS_ONLY` | `true` | Unchanged from M3 |
| `LLM_DATA_COLLECTION` | `deny` | Unchanged from M3 |
| `LLM_REQUIRE_STRUCTURED_OUTPUT` | `true` | Unchanged; never downgraded |
| Paid model profile | present, commented out | Preserved but disabled |

No paid API call was made. Reported cost across all 45 live model calls
recorded in the live database is **$0.00**.

## 2. What was built

**A search port with two implementations.** `app/adapters/search/` holds the
`SearchProvider` protocol, the committed fixture corpus from M2, and the Tavily
provider. Which one runs is configuration; nodes cannot tell the difference,
and `SearchMode` is recorded per task so a report can state what it actually
used.

**URL handling split into two distinct jobs**, because conflating them is how
deduplication quietly breaks:

- `normalise_url` produces the canonical form used for storage and the
  `url_sha256` digest: tracking parameters stripped, fragment dropped, default
  port removed, host lowercased, trailing slash settled. Two links to the same
  page now collapse to one source.
- `validate_url` **rejects** rather than sanitises: scheme allowlist, no
  embedded credentials, no loopback, private, link-local or cloud-metadata
  addresses, no bare hostnames, 2,048-character cap.

**Per-subtask failure isolation.** One subtask's retrieval failing is partial
research, not a dead task: the failure is counted, logged with its subtask
index and error code, and disclosed in the report. Only if *every* subtask
fails does the node raise `SearchUnavailableError`, because a report grounded
in nothing is worse than no report.

**A per-task search cap.** `SEARCH_MAX_PER_TASK` is enforced in the research
node, so a runaway plan costs one task rather than the month's allowance. It
truncates with a warning rather than failing.

**Provenance derived from stored sources, not configuration.** The notice on a
report is chosen from what is actually in the database — five cases, covering
every live/fixture combination of model and sources. The live-research notice
reads:

> **Live web research — retrieved, not verified.**
> Sources below were retrieved from the public web and a language model
> produced the analysis. AgentFlow does not assess whether a retrieved page is
> accurate, current, or authoritative; it checks only whether a claim is
> supported by something a source actually says.

## 3. Results — the verified lifecycle run

Task `e51b02f4`, approved, 121 seconds, 8 model calls, 8 searches. One task
asserted from every angle the milestone required, deliberately rather than
several: re-deriving the same facts from a second run would have spent a
scarce allowance to learn nothing.

| | |
|---|---|
| Question | *Compare Apache Airflow, Prefect and Dagster for a small engineering team, focusing on operational burden.* |
| Sources | **17, all live**, `provider=tavily`, 17 distinct `url_sha256` |
| Real provider metadata | relevance scores 0.497–0.727, publication dates preserved as provider text |
| Claims | 21 supported, 2 partial, 1 unsupported |
| Citation links | **48, of which 0 resolved outside the task's own retrieved sources** |
| Model calls | 8, all `output_mode=json_schema` (tier 1), served by `Baidu` |
| Reports | v1 superseded (14,564 chars) → v2 approved (5,455 chars) |
| Export | refused before approval (`ApprovalRequiredError`), returned v2 after |

The generated limitations section of the approved report, unedited:

```
- Written by model(s): `baidu/ernie-5.5-flash-preview:free`.
- Sources retrieved via `tavily`. AgentFlow did not fetch the pages itself; it
  relies on provider-supplied excerpts, and does **not** assess whether a page
  is accurate, current or authoritative.
- Based on 17 source(s) across 4 subtask(s). Anything those sources do not
  cover is absent from this report.
- **1 claim(s) could not be supported** by any retrieved source and 0 were only
  partially supported. Treat them as unverified.
- Claim checking confirms a claim points at a retrieved source. It is **not** a
  fact check, and offers no protection against a source being wrong.
- This is version 2, after 1 reviewer revision request(s).
- Spend for this task: 8 model call(s), 43,600 token(s).
```

Two things worth noting honestly about the output quality, as distinct from the
orchestration:

- The free model, given the revision instruction *"Say more about the licensing
  boundary"*, **narrowed** the report to the licensing question and dropped the
  broader operational comparison. The revision mechanism worked exactly as
  designed; the model followed the instruction more literally than a reviewer
  would want. That is a model-quality observation, not an orchestrator defect.
- The one unsupported claim was correctly marked unsupported: the model had
  asserted a specific mixed Apache 2.0/AGPLv3/proprietary licensing claim, and
  claim checking refused to back it. The report says so in its own summary.

### Partial failure, provoked against the live provider

Task `d7f70f82`. The offline suite already covered this path with a stubbed
error; what it could not show is that a *genuine* Tavily rejection is
classified as per-subtask rather than task-killing. Three subtasks, one with a
4,125-character query that the API rejects with `400`:

```
search_completed      provider=tavily query_chars=43   results=4
search_call_failed    provider=tavily query_chars=4125 retryable=False code=adapter_error
search_subtask_failed subtask_index=1 code=adapter_error
search_completed      provider=tavily query_chars=43   results=4
node_completed        node=research duration_ms=1367
```

The 400 was correctly treated as non-retryable (no retry attempted), the
surviving subtasks produced 8 real sources, and the step row records
`partial=true`, `subtasks_failed=1` and which subtask failed.

## 4. Tests

| Suite | Result |
|---|---|
| Offline (unit + integration) | **408 passed**, 20 skipped — the skips are the live tests, skipped when no live credentials are configured |
| Unit | 274 |
| Integration | 134 |
| Live search batch (`-m "live_search and not live_models"`) | **9 passed** |
| Live lifecycle batch (`live_models`) | **1 passed** |
| Frontend (Vitest) | **17 passed** in 5 files |
| ruff, ruff format, mypy (66 files), import-linter (4 contracts) | clean |
| ESLint (`--max-warnings 0`), `tsc -b`, production build | clean |

Backend line coverage is **90%** (2,699 statements, 257 uncovered).

The commit message on `c09ac85` claimed "386 tests (273 unit, 113 integration)".
That was wrong. Before M4's remaining work the figures were 390 (271 unit, 119
integration); they are now **408 (274 unit, 134 integration)**. The history is
not being rewritten for a commit-message inaccuracy; it is corrected here.

## 5. Defects and surprises found by running it

**Two provider limits were measured, not assumed, and both differed from the
documentation.**

- `GET /usage` is itself rate-limited to roughly 10 requests per 10 minutes.
  The budget guard polled it before every test and exhausted it in 26 seconds,
  which surfaced as a spurious credit-accounting failure. It now caches for 150
  seconds and **warns rather than silently continuing** when spend is
  unreadable.
- Tavily's own error names a 1,500-character query limit, but a 1,649-character
  query was accepted in practice. An earlier version of the partial-failure
  test assumed 400 characters, sent 539, and failed because the provider
  answered it happily. The test now uses 4,125 characters, well past any
  observed tolerance, and the comment records why.

**Tavily credit consumption cannot currently be verified.** `GET /usage`
reported `plan_usage 0, remaining 1000 of 1000` both before and after roughly
60 live search requests. Either the counter lags well behind or the Researcher
plan does not meter these requests where that endpoint can see them. The
approved 150-credit budget was therefore not demonstrably approached, but the
guard meant to enforce it cannot confirm spend, and now says so at runtime
instead of implying a figure it does not have. The fallback control is the hard
per-task cap, which is enforced locally and needs no provider cooperation.

**The credit-accounting test was rewritten to cost nothing.** It originally
asserted `usage_after - usage_before == 1`, which depended on the provider's
lagging counter. It now swaps in an `httpx.MockTransport` and asserts what we
actually control — one request per query, `search_depth=basic`,
`include_raw_content=false` — at zero credit cost.

**Seven model requests were spent by mistake.** The search batch was meant to
cost zero. The test module's `pytestmark` applies `live_search` to every class
in the file, including the lifecycle test, so `-m live_search` selected the
expensive test too; it started with four requests remaining, consumed seven,
and aborted mid-run, leaving task `5ec85255` in `failed`. The suite reported it
as *skipped* rather than *passed*, which was correct, but the allowance was
gone. No acceptance criterion rests on that run — all lifecycle evidence comes
from `e51b02f4`, which completed and was approved beforehand. Fixed by giving
the lifecycle test a `live_models` marker and selecting
`-m "live_search and not live_models"` for the search batch.

**A fixture bug that the offline suite could not catch.** `live_search_deps`
was missing its local `NodeDeps` import, so the first lifecycle attempt died in
setup with a `NameError`. It cost nothing because it failed before any call,
and it was invisible until then because the search-only tests use the provider
directly and never touch that fixture.

## 6. The rest of M4's scope: rate limiting and retention

M4's exit criteria in the approved plan are not only search. Checking them
found three settings that existed in configuration and did nothing.

**Rate limiting — implemented.** `RATE_LIMIT_PER_MINUTE` was a declared
setting that nothing read. There is now a Redis fixed-window limiter
(`app/adapters/ratelimit/`) on the three write endpoints, keyed by
`session:method:path` so approving a report does not consume a submission's
allowance. Read endpoints are deliberately unlimited, because polling is how
the UI tracks progress. It **fails open** if Redis is unreachable: a limiter
that rejects requests when its own store is down turns a Redis blip into an
outage, and spend is bounded by hard per-task caps regardless (ADR-017).

Verified against the running stack in Docker, not only in tests — 25
submissions on one session:

```
202 202 202 202 202 202 202 202 202 202 202 202 202 202 202 202 202 202 202 202 429 429 429 429 429

HTTP/1.1 429 Too Many Requests
retry-after: 28
{"error":{"code":"rate_limited","message":"Too many requests. Try again in 28s.",
          "details":{"limit":20,"window_seconds":60,"retry_after_seconds":28},
          "correlation_id":"b4352bb5a7b34947815734e90f1e69c2"}}
```

**Retention — implemented.** `purge_expired` existed on `TaskService` with a
docstring reading "Wired to a schedule in M4", a `DATA_RETENTION_DAYS=7`
setting, and nothing scheduling it. It is now `purge_tasks_older_than` — a
plain function, because requiring the service would make the worker construct
a queue it has no use for — driven by a `purge_expired_tasks` job on a daily
cron at 03:17, deliberately not at startup so a restart loop cannot purge
repeatedly. Verified in the real worker container:

```
→ purge_expired_tasks()
tasks_purged  count=0 cutoff=2026-11-10T23:12:13+00:00 retention_days=7
retention_sweep_completed  removed=0 retention_days=7
← purge_expired_tasks ● 0
```

Six tests cover it, including that the cascades actually remove sources,
reports and claims — a deletion that leaves the retrieved excerpts behind has
not deleted the part that matters for privacy — and that the job is registered
and scheduled rather than merely defined.

**Three settings are declared but still NOT enforced**, and are now documented
as such in `docs/security.md`, in `.env.example` and in `Settings` itself:

| Setting | Intended control |
|---|---|
| `DEMO_DAILY_BUDGET_USD` | Daily USD ceiling across all visitors |
| `DEMO_MAX_TASKS_PER_SESSION` | Cap on tasks one visitor may submit |
| `DEMO_ACCESS_CODE` | Gate live search behind a shared code |

All three guard a *public* deployment, which does not exist yet, so they were
annotated rather than implemented — widening M4 to build deployment guards for
an environment that does not exist seemed the wrong call. **They must be
implemented or removed before the application is exposed publicly.** A setting
that looks like a safety control but is not wired up is worse than no setting,
which is why they are now labelled at every place a reader could encounter
them. Flagging for a decision on whether they belong in M7.

**A new Python advisory appeared and was fixed.** `pip-audit` began reporting
PYSEC-2026-1845 against `pytest` 8.4.2. It is a development dependency that
never ships, but CI's audit job fails on any unlisted advisory by design.
Upgraded to `pytest>=9.0.3` (resolved 9.2.0); all 408 tests pass on it and
`pip-audit --strict` is clean across 90 packages.

## 7. What is verified, and what is not

**Verified by runs recorded in `agentflow_live`:**

- Real web retrieval through Tavily, with live URLs, provider relevance scores
  and publication dates preserved.
- URL normalisation and per-task deduplication on real results.
- Citation binding to sources that were actually retrieved — 222 citation links
  across all live tasks, **0** unresolvable.
- Partial retrieval failure isolated per subtask and disclosed; total failure
  fails the node.
- Provenance notices that distinguish retrieved from verified, derived from
  stored sources rather than configuration.
- Approval gate, revision round, report versioning and Markdown export on live
  data.
- Strict tier-1 structured output throughout, with free-only and
  `data_collection=deny` unchanged.
- Per-session rate limiting on write endpoints, with `Retry-After`, verified
  against the running stack in Docker.
- The retention job registered, scheduled, and running in the real worker.

**Not verified, and why:**

- **Tavily credit metering.** The provider's usage endpoint does not reflect
  our requests (see above). Our own request count is recorded instead.
- **Total retrieval failure, live.** Covered offline. Provoking it live would
  mean revoking the key mid-run.
- **Rate-limit and `Retry-After` handling against Tavily.** The code paths
  exist and are covered offline with mocked responses; the live allowance was
  never exhausted, so the live path is unexercised.
- **The paid model profile.** Present, disabled, untested. Nothing in this
  report describes paid-model behaviour.
- **`search_depth=advanced`.** Costs more than one credit per search; not
  enabled and not measured.
- **The retention sweep has not been observed deleting a real expired task.**
  The cascades and the boundary are covered by tests on seeded rows, and the
  job was verified running in the worker, but no task in any environment is
  yet seven days old, so every live run so far has removed zero rows.
- **Public deployment.** Not started. The application runs locally in Docker
  only.

## 8. Outstanding security item

The `picomatch` advisory (GHSA-fc5q-39xc-vjpf) is still unresolved upstream:
`4.0.7` remains the latest published version and the advisory covers `<=4.0.7`,
so there is no fixed version to move to. It remains an explicit, single-entry
exception in the CI audit job, which fails on any advisory outside that list.
The exception should be removed as soon as a fixed release exists.

## 9. Readiness for M5

M4 leaves the backend doing real research. The frontend still shows the M2
surface and does not display retrieval provenance, per-source metadata or
partial-failure disclosure, which is M5's work. Retention remains unimplemented
and should be scheduled explicitly rather than carried silently.
