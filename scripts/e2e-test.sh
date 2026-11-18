#!/usr/bin/env bash
# Run the browser end-to-end suite against a freshly started offline stack.
#
#   scripts/e2e-test.sh            # start stack, run all specs, leave it up
#   scripts/e2e-test.sh --down     # tear the stack down afterwards
#   scripts/e2e-test.sh e2e/journey.spec.ts   # a single spec
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TEARDOWN=0
ARGS=()
for arg in "$@"; do
  case "$arg" in
    --down) TEARDOWN=1 ;;
    *) ARGS+=("$arg") ;;
  esac
done

scripts/e2e-stack.sh up

cleanup() {
  if [ "$TEARDOWN" = "1" ]; then
    printf '\n== tearing the stack down\n'
    scripts/e2e-stack.sh down
  fi
}
trap cleanup EXIT

printf '\n== running the browser suite\n'
cd frontend
pnpm exec playwright test "${ARGS[@]+"${ARGS[@]}"}"
