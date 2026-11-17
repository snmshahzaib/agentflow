# M3 live acceptance ledger

**M3 is not complete.** This ledger is the authoritative record of which live
acceptance criteria are verified, which failed, and which are blocked by the
free-tier quota. It is updated per batch and committed, so evidence accumulates
across days rather than living in a database that a routine test reset can
destroy.

**Profile under test:** OpenRouter free tier · `LLM_FREE_MODELS_ONLY=true` ·
`LLM_REQUIRE_STRUCTURED_OUTPUT=true` · `LLM_DATA_COLLECTION=deny` ·
`SEARCH_PROVIDER=fixture`. Paid profile preserved but disabled.

**Quota:** 50 free-model requests per day on a zero-credit account
(`X-RateLimit-Limit: 50`, body `free-models-per-day`).

---

## Status as of 2026-11-16

| # | Criterion | Status | Calls | Evidence |
|---|---|---|---|---|
| A | End-to-end live workflow | **demonstrated, test not green** | 4 | Full 5-node run completed on `baidu/ernie-5.5-flash-preview:free`; per-node metrics captured (below). The automated assertion failed first on a harness bug (since fixed) and then on quota, so the *test* has never gone green. |
| B | Strict structured-output validation | **passed** | 12 | Catalogue probe: 3 free models returned schema-valid output at the native `json_schema` tier. Spend $0.000000. |
| C | Per-node model selection reaches the provider | **blocked — quota** | 1 | — |
| D | Cross-vendor fallback | **blocked — quota** | 2 | — |
| E | Budget exhaustion leaves no report | **blocked — quota** | 2 | — |
| F | Invalid model refused at startup | **blocked — quota** | 0 | Needs a live catalogue fetch only; no inference call. |
| G | Unreachable gateway reported, not hung | **blocked — quota** | 0 | No request reaches the provider. |
| H | Bad credential is terminal, not retried | **blocked — quota** | 1 | — |
| I | Injection containment, vendor 1 | **blocked — quota** | 4 | — |
| J | Injection containment, vendor 2 | **blocked — quota** | 4 | — |
| K | No M1/M2 regression | **passed** | 0 | 298 offline tests pass (190 unit, 108 integration). |
| — | Containment firing on a real model | **observed** | — | `verdicts_downgraded_without_sources downgraded=2` during the run in A: two claims the model marked supported cited nothing resolvable and were downgraded. |

### Measured per-node usage from the run in A

Model: `baidu/ernie-5.5-flash-preview:free`, served as requested. Search:
fixture corpus. Total 4 calls, 19,768 tokens, **$0.000000**.

| Node | tokens in | tokens out | latency |
|---|---|---|---|
| `plan` | 121 | 1,407 | 7.9 s |
| `analyse` | 854 | 8,902 | 35.0 s |
| `citation_check` | 1,181 | 3,022 | 10.4 s |
| `draft` | 566 | 3,715 | 16.9 s |

**Caveat on this row.** These figures were read from the database at the time
and are reproduced from that output. The rows themselves no longer exist: the
live run had written into the shared test database, which was then truncated
before a later run. That is why live runs now use a dedicated
`agentflow_live` database and why `scripts/export_live_evidence.py` writes
evidence to a committed file after each batch.

**Finding worth carrying into any paid run.** Output tokens were roughly six
times the pre-run estimate (≈17k against ≈3k budgeted). This model is verbose.
On the paid profile that would imply ≈$0.18 per run rather than the ≈$0.018
estimated from catalogue pricing — a figure that must be re-measured, not
carried over, if the paid profile is ever enabled.

---

## Batch plan

Each criterion's call cost is known, so the batches are sized against the
50/day quota rather than guessed. Successful-path total is **19 calls**; with
one retry allowed per flaky call, **≈25**.

| Batch | Criteria | Calls | Rationale |
|---|---|---|---|
| 1 | A + B + K | 4 | One workflow run yields the end-to-end result, the usage ledger, provenance labelling and tier-1 confirmation together. |
| 2 | I + J | 8 | The two-vendor injection comparison, run together so it is same-day. |
| 3 | C + D + E + F + G + H | 6 | Independent, self-contained criteria. |
| — | retry reserve | ~7 | For flaky free endpoints. |
| | **total** | **≈25** | Fits one day's quota. |

**The whole suite fits in a single day.** Today's exhaustion was not the
suite: it was a 12-model compatibility probe plus roughly six full workflow
runs during debugging, plus retries. The probe does not need repeating — its
results are recorded above — and the debugging runs were one-off.

So: **run all three batches on the first day after reset**, in order, checking
remaining quota between them. Spreading across days is the fallback if a batch
fails and the retry reserve is spent, not the plan.

---

## Can the suite be split across days without compromising quality?

Mostly yes, with two hard constraints and one honest caveat.

**Must stay atomic.** Batch 1's criteria all read the same task's rows — the
workflow result, the usage ledger, the provenance text. Splitting them would
mean either paying for another workflow run or asserting against a previous
day's run whose model may since have changed. Keep them in one batch.

**Should stay same-day.** The injection pair (I, J) is a *comparison* between
two vendors. Taking vendor 1 on Monday and vendor 2 on Thursday weakens the
claim that containment is model-independent, because an intervening model
update becomes an alternative explanation for any difference.

**Splits cleanly.** C, D, E, F, G and H are self-contained: each creates its
own task and asserts on its own result. Any of them can run on any day with no
loss of meaning.

**The real cost of splitting is coherence, not correctness.** Evidence
gathered across days establishes that each criterion passed *at some point*,
not that the system was green *as a whole at one moment*. Mitigation: whichever
day completes the plan, re-run batch 1 (4 calls) as a same-day canary, so the
report contains one coherent end-to-end demonstration dated with the report
itself.

**The sharper risk is free-model volatility.** These are preview models on a
free tier; one of the seven candidates was already rate-limited during the
probe, and a model can be withdrawn between days. `model_served` is recorded
per call and the model id is pinned per criterion in this ledger, so a change
becomes visible rather than silently invalidating earlier evidence.

---

## Acceptance criteria that cannot be met under the free tier

Stated now rather than discovered at the end:

1. **Paid-model verification of the shipped profile.** The approved default is
   `anthropic/claude-sonnet-5` with a `haiku` planner. It cannot be exercised
   without credits. Any acceptance evidence gathered here is evidence about
   free preview models, not about what would ship.
2. **Cost-tracking accuracy against real billing.** Every free call records
   `cost_usd_estimate` and `cost_usd_reported` as `0.0`, which is correct but
   does not exercise the reconciliation between our catalogue-derived estimate
   and a gateway-reported charge. That path stays unverified.
3. **Fallback across *paid* vendors.** Cross-vendor fallback can be shown
   between two free vendors, which is a genuine test of the mechanism. It does
   not demonstrate the configured paid chain (`openai/gpt-5.1`,
   `google/gemini-4-argon`).
4. **Output quality representative of the shipped profile.** Free preview
   models are materially weaker at analysis and citation checking. Nothing in
   this ledger should be read as evidence of the quality a paid profile would
   produce.
5. **Three free models are unreachable at all** without relaxing
   `data_collection=deny`. That trade was declined; see `docs/security.md`.
