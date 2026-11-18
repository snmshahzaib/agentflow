#!/usr/bin/env bash
# Bring the stack up in the **offline profile** for browser end-to-end tests.
#
#   scripts/e2e-stack.sh up     # start and wait until ready
#   scripts/e2e-stack.sh down   # stop the containers started here
#   scripts/e2e-stack.sh env    # print the overrides, for debugging
#
# The overrides matter. E2E tests must be deterministic and free, so the
# model adapter is the stub and search is the committed fixture corpus. The
# per-node model overrides are cleared too: a real .env names OpenRouter
# models, which are not in the stub catalogue, and the startup guard would
# (correctly) refuse to boot.
#
# Rate limiting is raised for the duration. The real default is 20 writes a
# minute per session, and a spec that drives several tasks through approval
# and revision legitimately exceeds that; the limiter has its own tests.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FRONTEND_URL="http://localhost:${FRONTEND_PORT:-8080}"
API_URL="http://localhost:${API_PORT:-8000}"

offline_env() {
  echo "LLM_PROVIDER=stub"
  echo "SEARCH_PROVIDER=fixture"
  echo "LLM_API_KEY="
  echo "SEARCH_API_KEY="
  echo "LLM_MODEL_DEFAULT="
  echo "LLM_MODEL_PLANNER="
  echo "LLM_MODEL_ANALYST="
  echo "LLM_MODEL_CITATION_CHECK="
  echo "LLM_MODEL_DRAFT="
  echo "LLM_FREE_MODELS_ONLY=false"
  echo "RATE_LIMIT_PER_MINUTE=${E2E_RATE_LIMIT_PER_MINUTE:-200}"
}

compose_offline() {
  # shellcheck disable=SC2046
  env $(offline_env | xargs) docker compose "$@"
}

case "${1:-up}" in
  env)
    offline_env
    ;;

  down)
    compose_offline down
    ;;

  up)
    printf '== starting the stack in the offline profile\n'
    compose_offline up -d --build

    printf '== waiting for readiness\n'
    for _ in $(seq 1 60); do
      if curl -fsS --max-time 5 "$API_URL/ready" >/dev/null 2>&1 \
         && curl -fsS --max-time 5 "$FRONTEND_URL" >/dev/null 2>&1; then
        break
      fi
      sleep 2
    done

    curl -fsS --max-time 10 "$API_URL/ready" >/dev/null \
      || { printf 'FAILED: the API never became ready\n' >&2; docker compose logs api | tail -40 >&2; exit 1; }
    curl -fsS --max-time 10 "$FRONTEND_URL" >/dev/null \
      || { printf 'FAILED: the frontend never became reachable\n' >&2; exit 1; }

    # Assert the profile rather than assume it. A suite that silently ran
    # against live providers would spend a metered allowance and stop being
    # deterministic, and the tests assert fixture-specific labels.
    META="$(curl -fsS --max-time 10 "$API_URL/api/meta")"
    python3 - "$META" <<'PY'
import json
import sys

meta = json.loads(sys.argv[1])
problems = [
    f"{key}={meta[key]!r}"
    for key, want in (("llm_provider", "stub"), ("search_provider", "fixture"), ("offline_mode", True))
    if meta[key] != want
]
if problems:
    sys.exit("FAILED: the stack is not in the offline profile: " + ", ".join(problems))
print(f"  offline profile confirmed: llm={meta['llm_provider']} search={meta['search_provider']}")
PY

    printf '\n== ready\n  frontend : %s\n  api      : %s\n' "$FRONTEND_URL" "$API_URL"
    ;;

  *)
    printf 'usage: %s up|down|env\n' "$0" >&2
    exit 2
    ;;
esac
