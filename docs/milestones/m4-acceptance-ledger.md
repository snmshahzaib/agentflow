# M4 live acceptance ledger

Live web research is verified against two independent, metered allowances, so
the suite is split into batches sized to each one:

| Batch | Command | Spends | Cost per run |
|---|---|---|---|
| `search` | `scripts/m4-live-batch.sh search` | Tavily credits only | ~20 search requests, **zero** model requests |
| `lifecycle` | `scripts/m4-live-batch.sh lifecycle` | both allowances | 8 search requests, 8 model requests |

The split is deliberate. OpenRouter's free allowance is 50 requests per day on
a zero-credit account and recovers slowly, while the Tavily allowance is
comparatively plentiful, so every property that can be proven without a model
call is proven without one. Only the lifecycle batch needs both.

Credentials come from the gitignored `.env`; neither key is printed. All live
runs record into the preserved `agentflow_live` database, never the test
database that integration runs reset.

## Status as of 2026-11-17

| # | Criterion | Status | Evidence |
|---|---|---|---|
| A | Real web retrieval | **PASSED** | Lifecycle batch, task `e51b02f4`. 17 source rows, all `is_fixture=false`, `provider=tavily`, pointing at live third-party URLs. |
| B | URLs are stored normalised | **PASSED** | `search` batch. Every stored URL equals `normalise_url(url)`; tracking parameters, fragments, default ports and host case are removed before storage. |
| C | Deduplication across subtasks | **PASSED** | Lifecycle batch: 17 sources, 17 distinct `url_sha256`. `search` batch asserts uniqueness and contiguous ranks within a response. |
| D | Provider metadata preserved | **PASSED** | `search` batch, and task `e51b02f4` stores real Tavily relevance scores (0.497–0.727) and publication dates. |
| E | Citation support over real sources | **PASSED** | Task `e51b02f4`: 48 claim→source links, **0** resolving to anything outside the task's own retrieved sources. 21 supported, 2 partial, 1 unsupported. |
| F | Partial retrieval failure is partial, not fatal | **PASSED** | `search` batch, task `d7f70f82`. A genuine non-retryable Tavily `400` on one subtask; the other two still produced live evidence. Step output records `partial=true`, `subtasks_failed=1`, and which subtask failed. |
| G | Total retrieval failure is fatal | **PASSED** | Offline suite (`SearchUnavailableError` when every subtask fails). Not provoked live: forcing it would mean revoking the key mid-run. |
| H | Provenance distinguishes retrieved from verified | **PASSED** | Task `e51b02f4` carries *"Live web research — retrieved, not verified"* and states AgentFlow "does not assess whether a retrieved page is accurate, current, or authoritative". |
| I | Approval gate still holds on live data | **PASSED** | Task `e51b02f4`. Export before approval raised `ApprovalRequiredError`; after approval it returned v2. |
| J | Revision on live data | **PASSED** | Task `e51b02f4`. v1 → `superseded`, v2 created, task returned to `awaiting_approval`; 8 model calls across both rounds. |
| K | Markdown export | **PASSED** | Task `e51b02f4`, v2, 5,455 characters including the provenance notice, claim-support table, 17 sources and generated limitations. |
| L | Search depth and per-task cap as approved | **PASSED** | `search` batch. `search_depth=basic` (1 credit), `include_raw_content=false`, one request per query, `SEARCH_MAX_PER_TASK=8` — asserted via `httpx.MockTransport` at zero credit cost. |
| M | Strict structured output, no downgrade | **PASSED** | All 8 live calls on task `e51b02f4` recorded `output_mode=json_schema` (tier 1). |
| N | Free-only and privacy controls unchanged | **PASSED** | `LLM_FREE_MODELS_ONLY=true`, `LLM_DATA_COLLECTION=deny`; reported cost $0.00 across all 8 calls. |
| O | Rate limiting on write endpoints | **PASSED** | 25 submissions against the running stack: 20×202 then 5×429 with `Retry-After: 28` and the standard envelope. Keyed per session and per endpoint; reads unlimited; fails open. |
| P | Retention sweep scheduled and running | **PASSED** | `purge_expired_tasks` executed in the worker container with a correct 7-day cutoff; 6 tests cover cascades, both sides of the boundary, and that the cron is registered with `run_at_startup=False`. Not yet observed deleting a real expired task — nothing is 7 days old. |
| Q | No M1–M3 regression | **PASSED** | 408 offline tests pass (274 unit, 134 integration), 20 live tests skipped when unconfigured; coverage 90%. |

## Allowance accounting

**OpenRouter free model requests.** Measured from the key metadata endpoint,
which is metadata and does not itself consume the allowance:

| Point in the day | `free_model_daily_requests` |
|---|---|
| Before M4 live work | `used 38, limit 50, remaining 12` |
| After the lifecycle batch | `used 43, limit 50, remaining 7` |
| After the search batch | `used 53, limit 50, remaining 0` |

The lifecycle batch cost 5 counted requests for 8 recorded calls, so
OpenRouter's counter and our ledger do not agree one-for-one; our ledger counts
completions, and the discrepancy is not something we can see into. The jump
from 46 to 53 is explained under *Allowance spent by mistake* below.

**Tavily credits.** Not confirmable from the provider. `GET /usage` reported
`plan_usage 0, remaining 1000 of 1000` both before and after roughly 60 live
search requests, so either the counter lags well behind or the Researcher
plan does not meter these requests in a way that endpoint exposes. What can be
stated is AgentFlow's own count, taken from the step rows in the live
database: **64 search requests issued through the graph** (24 of them against
the fixture provider in earlier milestones, 40 against Tavily), plus a handful
of provider-level requests in the search batch that do not create task rows.
The approved 150-credit budget was therefore not demonstrably approached, but
the guard that enforces it **cannot currently verify spend**, and says so at
runtime rather than claiming a figure:

```
UserWarning: Tavily usage is unreadable (likely its own rate limit);
proceeding under the per-task search cap.
```

The fallback control is the hard per-task cap (`SEARCH_MAX_PER_TASK=8`), which
is enforced locally and does not depend on the provider's accounting.

Two provider limits were measured rather than assumed:

- `GET /usage` is itself rate-limited to roughly 10 requests per 10 minutes.
  The budget guard originally polled it before every test and exhausted it in
  26 seconds; it now caches for 150 seconds.
- A search query is rejected with `400 Query is too long. Max query length is
  1500 characters.` — but a 1,649-character query was accepted in practice, so
  the stated limit is not the enforced one. The partial-failure test uses
  4,125 characters, well past any observed tolerance.

## Allowance spent by mistake

The search batch was meant to cost zero model requests. It cost seven.

The test module sets `pytestmark = [pytest.mark.live_search, ...]`, which
applies `live_search` to **every** class in the file — including the full
lifecycle test. Selecting `-m live_search` therefore selected the expensive
test too. It started with four model requests remaining, consumed seven,
exhausted the allowance mid-run and aborted, leaving task `5ec85255` in
`failed` with 7 recorded calls. The suite reported it as *skipped* rather than
*passed*, which is correct, but the requests were already gone.

The lifecycle criteria in the table above are evidenced by task `e51b02f4`,
which completed and was approved **before** this happened; no criterion rests
on the aborted run.

Fixed by giving the lifecycle test its own `live_models` marker and selecting
`-m "live_search and not live_models"` for the search batch, with the reason
recorded at both the marker and the script.

## Re-running after the allowance resets

The lifecycle batch is the only one that needs model requests, and it needs
eight. Nothing in M4 requires re-running it: its evidence is preserved in
`agentflow_live` and exported to `m4-live-evidence.json`. To reproduce:

```bash
scripts/m4-live-batch.sh search      # any time; no model requests
scripts/m4-live-batch.sh lifecycle   # needs >= 8 free model requests
```
