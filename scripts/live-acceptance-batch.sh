#!/usr/bin/env bash
# Run one batch of the M3 live acceptance suite against the free tier.
#
#   scripts/live-acceptance-batch.sh 1|2|3
#
# Batches are sized against the free quota (50 requests/day on a zero-credit
# account) and documented in docs/milestones/m3-acceptance-ledger.md:
#
#   1  end-to-end workflow + usage ledger + provenance   ~4 calls
#   2  injection containment, two vendors                ~8 calls
#   3  independent criteria (fallback, budget, errors)    ~6 calls
#
# Credentials come from the gitignored .env. The key is never printed, and
# every run records into the preserved agentflow_live database, never the test
# database that integration runs reset.
set -euo pipefail

BATCH="${1:-}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"

say()  { printf '\n== %s\n' "$1"; }
fail() { printf '\nFAILED: %s\n' "$1" >&2; exit 1; }

case "$BATCH" in
  1) SELECTION="tests/test_live_openrouter.py::TestLiveWorkflow"
     LABEL="end-to-end workflow, usage ledger, provenance" ;;
  2) SELECTION="tests/test_live_openrouter.py::TestInjectionAgainstRealModels"
     LABEL="injection containment across two vendors" ;;
  3) SELECTION="tests/test_live_openrouter.py::TestStructuredOutput \
                tests/test_live_openrouter.py::TestUsageLedger \
                tests/test_live_openrouter.py::TestReliability \
                tests/test_live_openrouter.py::TestBudgetEnforcement"
     LABEL="independent criteria: structured output, fallback, budget, error paths" ;;
  *) fail "usage: $0 1|2|3" ;;
esac

KEY="$(grep -E '^LLM_API_KEY=' "$ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' || true)"
[ -n "$KEY" ] || fail "LLM_API_KEY is not set in $ROOT/.env"
[ "${KEY#__}" = "$KEY" ] || fail "LLM_API_KEY is still a placeholder"

FREE_PRIMARY="${M3_DEV_MODEL:-baidu/ernie-5.5-flash-preview:free}"
FREE_ALT="${M3_ALT_MODEL:-dots-studio/dots-3-note-preview:free}"

say "batch $BATCH — $LABEL"
echo "  primary model : $FREE_PRIMARY"
echo "  alt vendor    : $FREE_ALT"
echo "  free-only     : enforced (LLM_FREE_MODELS_ONLY=true)"
echo "  privacy       : data_collection=deny (unchanged)"

say "remaining free quota before the batch"
# OpenRouter's key endpoint reports limits and usage. It is metadata, not
# inference: it does not consume the free-model allowance.
curl -s --max-time 20 -H "Authorization: Bearer $KEY" \
  https://openrouter.ai/api/v1/key \
  | python3 -c "
import json,sys
try:
    d = json.load(sys.stdin).get('data', {})
except Exception:
    print('  (could not read key metadata)'); raise SystemExit(0)
for k in ('label','usage','limit','limit_remaining','is_free_tier','rate_limit'):
    if k in d: print(f'  {k}: {d[k]}')
" || echo "  (key metadata unavailable)"

say "running"
set +e
env LLM_PROVIDER=openrouter LLM_API_KEY="$KEY" \
    LLM_MODEL_DEFAULT="$FREE_PRIMARY" \
    LLM_FREE_MODELS_ONLY=true \
    LLM_REQUIRE_STRUCTURED_OUTPUT=true \
    LLM_DATA_COLLECTION=deny \
    LLM_MAX_ATTEMPTS=2 \
    LLM_TIMEOUT_SECONDS=180 \
    M3_FREE_ONLY=true \
    M3_DEV_MODEL="$FREE_PRIMARY" \
    M3_ALT_MODEL="$FREE_ALT" \
    REDIS_URL="${REDIS_URL:-redis://localhost:6381/0}" \
  uv run pytest -v -p no:randomly --timeout=600 -rA $SELECTION
STATUS=$?
set -e

say "outcome"
# Distinguish the three states explicitly: a quota skip is not a pass and not
# a failure, and conflating them would misreport the milestone.
echo "  pytest exit code: $STATUS"
echo "  PASSED  = criterion verified"
echo "  FAILED  = criterion not met; investigate before re-running"
echo "  SKIPPED = free-tier allowance exhausted; re-run after reset"

say "preserving evidence"
uv run python scripts/export_live_evidence.py

say "next"
echo "  update docs/milestones/m3-acceptance-ledger.md with this batch's result"
echo "  then run the next batch only if quota remains"
exit "$STATUS"
