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

## Status as of 2026-11-17 — all batches verified

Run against the free profile: `LLM_FREE_MODELS_ONLY=true`,
`LLM_REQUIRE_STRUCTURED_OUTPUT=true`, `LLM_DATA_COLLECTION=deny`,
`SEARCH_PROVIDER=fixture`. **Total spend $0.00**, confirmed by the key's
`spend_usd: 0`. Quota used 38 of 50.

| # | Criterion | Status | Evidence |
|---|---|---|---|
| A | End-to-end live workflow | **PASSED** | Batch 1, 89.6 s. Task reached `awaiting_approval` through the real arq handler; report v1 (9,086 chars) with claims and evidence links. |
| B | Strict structured-output validation | **PASSED** | Batch 3. Schema-valid output and `output_mode=json_schema` confirmed; never downgraded. |
| C | Per-node model selection reaches the provider | **PASSED** | Batch 3. An override changed which model the gateway served. |
| D | Cross-vendor fallback | **PASSED** | Batch 3. Unreachable primary fell through to a working model on another vendor. |
| E | Budget exhaustion leaves no report | **PASSED** | Batch 3. A 2-call budget aborted the workflow with no report written. |
| F | Invalid model refused at startup | **PASSED** | Batch 3. Catalogue validation refused a non-existent model before any task ran. |
| G | Unreachable gateway reported, not hung | **PASSED** | Batch 3. Normalised to a domain error rather than hanging. |
| H | Bad credential is terminal, not retried | **PASSED** | Batch 3. A 401 was not retried. |
| I+J | Injection containment across two **distinct providers** | **PASSED** | Batch 2, 428.9 s. Two full workflows, one per vendor; the test asserts the providers that actually served them are disjoint. |
| K | No M1/M2 regression | **PASSED** | 302 offline tests pass. |

### Providers actually verified

Vendor diversity was confirmed from the gateway's reported serving provider,
not inferred from model ids — a new `llm_calls.provider_served` column exists
for exactly this, because two model ids can be hosted by one company and a
meta-router can route both to the same backend.

| Provider | Model | Live calls |
|---|---|---|
| `Baidu` | `baidu/ernie-5.5-flash-preview:free` | 22 |
| `Ant Digital` | `inclusionai/ling-3.2-flash:free` | 6 |
| `AtlasCloud` | `dots-studio/dots-3-note-preview:free` | 2 |

The injection comparison used **Baidu** and **Ant Digital**.
`openrouter/free` was *not* used as a vendor: it routed to
`baidu/ernie-5.5-flash-preview:free`, so counting it would have been a
meta-router masquerading as a second vendor.

### Two vendor-2 candidates failed before one worked

Recorded because the failures are findings about free endpoints, not defects:

- **`dots-studio/dots-3-note-preview:free` (AtlasCloud)** — HTTP 400 *"bad
  request"* on the `citation_check` node, the largest prompt and biggest
  schema. `plan` and `analyse` succeeded first. Correctly classified
  non-retryable, so the node failed rather than hammering the provider.
- **`inclusionai/ling-3.2-flash:free` (Ant Digital)** — first attempt
  exceeded a 600 s test budget. Measured 43 s for a 61-token prompt. It
  passed on a second attempt once the per-test budget was raised to 2400 s;
  the full two-vendor comparison took 428.9 s.

### Measured per-node usage across all 30 live calls

| Node | calls | avg tokens in | avg tokens out | avg latency |
|---|---|---|---|---|
| `plan` | 9 | 119 | 1,194 | 34.2 s |
| `analyse` | 9 | 995 | 6,163 | 35.1 s |
| `citation_check` | 6 | 1,593 | 8,418 | 56.8 s |
| `draft` | 6 | 755 | 2,460 | 14.3 s |

Totals: 9 tasks, 30 calls, 24,111 input and 131,480 output tokens, **$0.00**.
Full per-call records are in `m3-live-evidence.json`.

### The cost estimate was wrong, and by how much

Output averaged **4,383 tokens per call** — a four-call workflow is ≈17.5k
output tokens, against the 3k budgeted from catalogue pricing. On the shipped
paid profile that implies **≈$0.18 per run, not ≈$0.018**: an order of
magnitude higher.

The cause is verbosity, not prompt size: input matched the estimate closely.
Whether a paid model is equally verbose is **unknown and must be measured, not
assumed** — this figure is evidence about free preview models. The estimate in
`model-configuration.md` is labelled accordingly.

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
