# M3 completion report — live OpenRouter integration (free profile)

**Date:** 2026-11-17
**Repository:** https://github.com/snmshahzaib/agentflow (**private**)
**Branch:** `main` · all commits under the repository-local identity
**Spend:** **$0.00** — confirmed by the account's `spend_usd: 0`
**Quota:** 38 of 50 free-model requests used

**Status: all ten live acceptance tests pass against the free profile.** What
that does and does not establish is set out in §6; in short, nothing here is
evidence about the paid models that would ship.

---

## 1. Configuration, unchanged from what was approved

```
LLM_PROVIDER=openrouter          LLM_FREE_MODELS_ONLY=true
LLM_MODEL_DEFAULT=baidu/ernie-5.5-flash-preview:free
LLM_FALLBACK_MODELS=dots-studio/dots-3-note-preview:free,openrouter/free
LLM_REQUIRE_STRUCTURED_OUTPUT=true   LLM_DATA_COLLECTION=deny
LLM_MAX_ATTEMPTS=2                   LLM_TIMEOUT_SECONDS=180
SEARCH_PROVIDER=fixture
```

The paid profile is preserved as commented lines in `.env` — available,
disabled. No model ID appears in application code; a pre-commit hook enforces
it. `data_collection=deny` was never relaxed, including when it was the reason
three free models were unreachable.

---

## 2. Results

| # | Criterion | Result |
|---|---|---|
| A | End-to-end live workflow | **PASSED** (batch 1, 89.6 s) |
| B | Strict structured-output validation | **PASSED** — `output_mode=json_schema`, never downgraded |
| C | Per-node model selection reaches the provider | **PASSED** |
| D | Cross-vendor fallback | **PASSED** |
| E | Budget exhaustion leaves no report | **PASSED** |
| F | Invalid model refused at startup | **PASSED** |
| G | Unreachable gateway reported, not hung | **PASSED** |
| H | Bad credential terminal, not retried | **PASSED** |
| I+J | Injection containment across two **distinct providers** | **PASSED** (batch 2, 428.9 s) |
| K | No M1/M2 regression | **PASSED** — 302 offline tests |

Batches: 1 passed, 2 passed on its third attempt (§4), 3 passed 8/8 in 152.7 s.
No criterion is outstanding. No test was skipped for quota in the final runs.

**Offline suite:** 302 tests pass (194 unit, 108 integration). ruff, mypy
across 64 files, 4 import-linter contracts, ESLint, `tsc -b`, 17 frontend
tests — all clean. gitleaks clean across tree, staged set and full history.
pip-audit reports no known vulnerabilities. `pnpm audit` reports one high
advisory with **no published fix** — picomatch ReDoS, build tooling only — now
recorded as a documented exception in `docs/security.md`, with CI failing on
any advisory not on that list.

---

## 3. Measured usage — 30 live calls, 9 tasks

| Node | calls | avg tokens in | avg tokens out | avg latency |
|---|---|---|---|---|
| `plan` | 9 | 119 | 1,194 | 34.2 s |
| `analyse` | 9 | 995 | 6,163 | 35.1 s |
| `citation_check` | 6 | 1,593 | 8,418 | 56.8 s |
| `draft` | 6 | 755 | 2,460 | 14.3 s |

Totals: 24,111 input and 131,480 output tokens, **$0.00**. Every call recorded
the requested model, the served model, the serving provider, tokens, latency,
our cost estimate, the gateway's reported cost, and the retry/fallback
outcome. Full per-call records: `m3-live-evidence.json`.

### Verified providers

Vendor identity is recorded, not inferred (ADR-012):

| Provider | Model | Calls | Full workflow? |
|---|---|---|---|
| `Baidu` | `baidu/ernie-5.5-flash-preview:free` | 22 | yes, fastest |
| `Ant Digital` | `inclusionai/ling-3.2-flash:free` | 6 | yes, very slow |
| `AtlasCloud` | `dots-studio/dots-3-note-preview:free` | 2 | **no** — HTTP 400 on `citation_check` |

The injection comparison used **Baidu** and **Ant Digital**.
`openrouter/free` was deliberately **not** counted as a vendor: it routed to
`baidu/ernie-5.5-flash-preview:free`, which would have made a meta-router
masquerade as vendor diversity.

### The cost estimate was wrong by an order of magnitude

Output averaged **4,383 tokens per call** — ≈17.5k for a four-call workflow,
against 3k budgeted. Input matched the estimate; the gap is **verbosity**,
which catalogue pricing cannot reveal. On the paid profile that implies
**≈$0.18 per run rather than ≈$0.018**.

That paid figure is itself unverified: it extrapolates free-model verbosity
onto paid pricing, and a paid model may be terser. It is labelled as such in
`model-configuration.md`. The estimate has now been wrong in both directions
(M1 too high at $0.20 from an inflated input assumption, M2 too low at $0.018
from measuring stub output), which is the argument for the ledger.

---

## 4. Defects found, all by running the system

**A false provenance claim — the most serious.** A live report contained both
*"No language model was called and no web search was performed"* and
*"Written by model(s): `baidu/ernie-5.5-flash-preview:free`"*. The notice
fired when the provider was a stub **or** search was fixtures, so a real model
over fixture sources was labelled fully offline. This is precisely the failure
the project's honesty rules exist to prevent, and no test caught it because
the assertions checked for correct strings being present and one wrong string
being absent — the wrong string that mattered was not on the list. Fixed to
describe the actual combination; four unit tests plus a live assertion now
cover it. ADR-011.

**A silent structured-output downgrade.** `output_mode` returned tier 2 for a
tools-only model even under `LLM_REQUIRE_STRUCTURED_OUTPUT=true`,
contradicting its own documented contract. Strict mode now raises; a downgrade
outside strict mode is logged rather than silent. ADR-008.

**My own script leaked a partial credential.** The batch runner printed
OpenRouter's key metadata wholesale, and its `label` field contains a
truncated form of the key. It now uses an allow-list of fields rather than a
deny-list, so the next credential-shaped field the API adds cannot leak
either. The affected log was outside the repository and has been scrubbed; no
commit ever contained it.

**Two free vendors cannot do the job, in different ways.** `AtlasCloud`
returns HTTP 400 on `citation_check` — correctly classified non-retryable, so
it failed fast rather than hammering the provider. `Ant Digital` works but
needs 43 s for a 61-token prompt, which exceeded a 600 s test budget before
the per-test budget was raised. Both are findings about free endpoints, not
defects, and both are why a free profile needs a fallback chain.

### Process failures worth recording

**I destroyed evidence.** Truncating the shared test database before a later
run deleted the rows from a successful live workflow that had cost real quota.
Live runs now use a dedicated `agentflow_live` database, and
`export_live_evidence.py` writes per-call metrics to a committed file so
evidence survives losing a database entirely.

**I reported documentation as updated when it was not.** Several patches to
`docs/model-configuration.md` silently failed to match, and I verified line
counts rather than content. The file has been rewritten and every claimed
documentation change is now verified by a content check.

---

## 5. Commits

| Commit | Purpose |
|---|---|
| `2fae1e9` | Free-model development profile; `LLM_FREE_MODELS_ONLY` hard guard; `Retry-After` honouring |
| `c0f50f1` | Report an exhausted provider allowance as a skip, not a failure |
| `a94192a` | Preserve live evidence in a dedicated database; batch the acceptance suite |
| *this* | Serving-provider column; provenance-notice fix; verified results and documentation |

---

## 6. What is verified, and what is not

**Verified against the free profile.** The complete workflow on real models;
strict native JSON-Schema output with no downgrade; per-node model selection
reaching the provider; cross-vendor fallback; budget exhaustion leaving no
report; invalid-model refusal at startup; unreachable-gateway handling;
terminal auth failure; injection containment across two genuinely distinct
providers; and the full usage ledger with real tokens, latency and served
models.

**Not verified, and not claimed.**

1. **The shipped paid profile.** `anthropic/claude-sonnet-5` with a `haiku`
   planner has never been called. Every live result here concerns free preview
   models.
2. **Cost reconciliation against a real charge.** Free calls record `$0.0` for
   both our estimate and the gateway's figure — correct, but it never
   exercises the reconciliation path.
3. **Fallback across the configured paid chain.** The mechanism is verified
   between free vendors; `openai/gpt-5.1` and `google/gemini-4-argon` are
   untested.
4. **Output quality representative of production.** Free preview models are
   materially weaker at analysis and citation checking. Nothing here is
   evidence about the quality a paid profile would produce.
5. **Injection containment remains containment, not prevention.** Two real
   models could not self-approve, could not make a fabricated citation
   resolve, and could not stop the limitations section telling the truth.
   Whether a model obeys an injected instruction is still a property of that
   model, unmeasured and unclaimed. A model that quietly biases its *prose*
   while citing real sources is not caught by any of this.
6. **Three free models remain unreachable** without relaxing
   `data_collection=deny`. Declined.

---

## 7. Readiness for M4

M4 is live Tavily search, URL safety validation, session scoping, rate
limiting, the demo budget ledger and the retention job. It needs no provider
credit beyond what exists, and the fixture provider remains the canonical
demo path.

One M3 finding carries forward: free-model latency of 14–57 s per call means
a full research task takes minutes, not seconds. Whatever M4 does about rate
limiting and the demo budget should assume a slow task, not a fast one.

**M4 is not started and will not be until the owner approves this report.**
