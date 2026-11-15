---
name: llm-model-change
description: Change, add or re-validate an LLM model or provider for AgentFlow — verify capabilities and pricing against the live catalogue, update the committed snapshot and documentation with measured values, and re-run the cross-model tests. Use when swapping a node's model, adding a fallback, changing LLM_BASE_URL, or when a model ID stops resolving.
---

# Change a model or provider

AgentFlow never hardcodes a model ID. A model change is an environment change
plus verification — never a code edit outside `app/adapters/llm/`.

## 1. Verify the candidate against the live catalogue

```bash
curl -s https://openrouter.ai/api/v1/models \
  | python3 -c "
import json,sys
want = 'VENDOR/MODEL-ID'
for m in json.load(sys.stdin)['data']:
    if m['id'] == want:
        sp = m.get('supported_parameters', [])
        print('context    :', m['context_length'])
        print('structured :', 'structured_outputs' in sp)
        print('tools      :', 'tools' in sp)
        print('\$/1M in    :', float(m['pricing']['prompt']) * 1e6)
        print('\$/1M out   :', float(m['pricing']['completion']) * 1e6)
        break
else:
    print('NOT IN CATALOGUE')
"
```

Selection criteria, in order: **native structured outputs → ≥200,000 context →
provider reliability → price.** Reject any model with no usable pricing (a
negative price marks a dynamic routing entry). Pin an exact slug: moving
aliases such as `~vendor/model-latest` are rejected by ADR-005, because a model
that silently changes underneath the evaluation harness destroys
reproducibility.

## 2. Update configuration and the snapshot

- `.env.example` — the variable, with the verified context window and price in
  the comment, and the verification date.
- `backend/app/adapters/llm/catalogue_snapshot.json` — add the model's entry so
  offline and CI runs can validate against it.
- `docs/model-configuration.md` — the model table and the cost estimate, with
  the date the prices were observed.
- `docs/decisions.md` — only if the choice reflects a decision, not a routine
  bump.

## 3. Confirm validation accepts it

```bash
cd backend
LLM_PROVIDER=stub LLM_MODEL_DEFAULT='VENDOR/MODEL-ID' \
  uv run python -c "
import asyncio
from app.adapters.llm.registry import build_llm_runtime
from app.core.config import Settings
rt = asyncio.run(build_llm_runtime(Settings(_env_file=None, app_env='test')))
print(rt.catalogue_source, {n.value: m for n, m in rt.models.items()})
"
```

A bad model ID must fail **at startup**, naming the environment variable. If it
does not, capability validation has a gap — fix that before shipping the model
change.

## 4. Re-run the tests that depend on model behaviour

```bash
uv run pytest tests/test_llm_catalogue.py tests/test_llm_registry.py \
              tests/test_llm_adapters.py -q
```

`test_documented_default_prices_match_the_snapshot` fails if the documentation
and the snapshot disagree — that is the drift guard, so fix the docs rather
than the test.

## 5. For a live model, measure rather than estimate

Run one real task and read the ledger:

```sql
SELECT node, model_requested, model_served, output_mode,
       tokens_in, tokens_out, latency_ms,
       cost_usd_estimate, cost_usd_reported, attempt_outcome
FROM llm_calls ORDER BY created_at DESC LIMIT 10;
```

Replace estimates in `docs/model-configuration.md` with measured figures and
say they are measured. Never publish a projected number as a measurement.

## Changing provider entirely

`LLM_BASE_URL` plus `LLM_API_KEY` is the whole change for any OpenAI-compatible
endpoint (LiteLLM proxy, Azure OpenAI, Together, Groq, local vLLM). AWS Bedrock
is not OpenAI-compatible: it needs either a second adapter behind the same
`LLMClient` port or a LiteLLM proxy in front, as recorded in ADR-003.
