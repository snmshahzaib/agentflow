#!/usr/bin/env bash
# Run one batch of the M4 live web-research acceptance suite.
#
#   scripts/m4-live-batch.sh search|lifecycle
#
# Two batches, sized against the two independent allowances:
#
#   search     retrieval behaviour only -- Tavily credits, ZERO model quota
#   lifecycle  run/approve/revise/export -- ~8 model calls + ~6 credits
#
# The split is deliberate. OpenRouter's free allowance is 50 requests/day and
# recovers slowly, while Tavily's 1,000 credits are comparatively plentiful, so
# everything that can be proven without a model call is proven without one.
#
# Both credentials come from the gitignored .env and are never printed.
# Evidence lands in the preserved agentflow_live database.
set -euo pipefail

BATCH="${1:-}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"

say()  { printf '\n== %s\n' "$1"; }
fail() { printf '\nFAILED: %s\n' "$1" >&2; exit 1; }

case "$BATCH" in
  search)
    # "not live_models" is load-bearing. The test module applies live_search to
    # every class in it, so a bare "-m live_search" also selects the full
    # lifecycle test and spends the LLM allowance this batch is meant to avoid.
    SELECTION=(-m "live_search and not live_models")
    LABEL="live retrieval, normalisation, dedup, metadata (no model calls)" ;;
  lifecycle)
    SELECTION=(tests/test_live_search.py::TestLiveResearchLifecycle)
    LABEL="full lifecycle: search -> report -> revise -> approve -> export" ;;
  *) fail "usage: $0 search|lifecycle" ;;
esac

read_env() { grep -E "^$1=" "$ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' || true; }

KEY="$(read_env LLM_API_KEY)"
SKEY="$(read_env SEARCH_API_KEY)"
[ -n "$KEY"  ] || fail "LLM_API_KEY is not set in $ROOT/.env"
[ -n "$SKEY" ] || fail "SEARCH_API_KEY is not set in $ROOT/.env"
[ "${KEY#__}"  = "$KEY"  ] || fail "LLM_API_KEY is still a placeholder"
[ "${SKEY#__}" = "$SKEY" ] || fail "SEARCH_API_KEY is still a placeholder"

FREE_PRIMARY="${M3_DEV_MODEL:-baidu/ernie-5.5-flash-preview:free}"

say "batch $BATCH — $LABEL"
echo "  primary model   : $FREE_PRIMARY"
echo "  free-only       : enforced (LLM_FREE_MODELS_ONLY=true)"
echo "  privacy         : data_collection=deny (unchanged)"
echo "  structured      : strict (LLM_REQUIRE_STRUCTURED_OUTPUT=true)"
echo "  search provider : tavily, depth=basic (1 credit/search)"
echo "  per-task cap    : SEARCH_MAX_PER_TASK=8 (approved)"
echo "  credit budget   : 150 (enforced by search_budget_guard)"

say "allowances before the batch"
# Both are metadata endpoints: neither consumes the allowance it reports.
curl -s --max-time 20 -H "Authorization: Bearer $KEY" \
  https://openrouter.ai/api/v1/key \
  | python3 -c "
import json,sys
try: d = json.load(sys.stdin).get('data', {})
except Exception: print('  (could not read key metadata)'); raise SystemExit(0)
# Allow-list only: OpenRouter's 'label' carries a truncated form of the key.
for k in ('free_model_daily_requests', 'limit_remaining', 'usage', 'is_free_tier'):
    if k in d: print(f'  openrouter {k}: {d[k]}')
" || echo "  (openrouter metadata unavailable)"
SEARCH_API_KEY="$SKEY" uv run python scripts/search_usage.py 2>&1 | sed 's/^/  /' \
  || echo "  (tavily usage unavailable -- it rate-limits /usage to 10 per 10 min)"

say "running"
set +e
env LLM_PROVIDER=openrouter LLM_API_KEY="$KEY" \
    LLM_MODEL_DEFAULT="$FREE_PRIMARY" \
    LLM_FREE_MODELS_ONLY=true \
    LLM_REQUIRE_STRUCTURED_OUTPUT=true \
    LLM_DATA_COLLECTION=deny \
    LLM_MAX_ATTEMPTS=2 \
    LLM_TIMEOUT_SECONDS="${LLM_TIMEOUT_SECONDS:-600}" \
    M3_FREE_ONLY=true \
    M3_DEV_MODEL="$FREE_PRIMARY" \
    SEARCH_PROVIDER=tavily \
    SEARCH_API_KEY="$SKEY" \
    SEARCH_DEPTH=basic \
    SEARCH_MAX_PER_TASK=8 \
    REDIS_URL="${REDIS_URL:-redis://localhost:6381/0}" \
  uv run pytest -v -p no:randomly -rA --timeout=2400 "${SELECTION[@]}"
STATUS=$?
set -e

say "outcome"
echo "  pytest exit code: $STATUS"
echo "  PASSED  = criterion verified"
echo "  FAILED  = criterion not met; investigate before re-running"
echo "  SKIPPED = an allowance was exhausted; re-run after it recovers"

say "preserving evidence"
uv run python scripts/export_live_evidence.py

exit "$STATUS"
