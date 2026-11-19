#!/usr/bin/env bash
# Abuse and failure behaviour against the **running stack**.
#
#   scripts/abuse-check.sh [BASE_URL]
#
# The unit tests cover these paths with fakes. This exercises them through
# nginx, the real API, a real Redis and a real worker, with genuine
# concurrency -- including stopping Redis mid-flight to see what a visitor
# actually gets.
#
# Runs against the offline profile, so nothing here can spend a provider
# allowance. Start the stack with scripts/e2e-stack.sh up.
set -euo pipefail

BASE="${1:-http://localhost:8080}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PASS=0
FAIL=0
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

say()  { printf '\n== %s\n' "$1"; }
ok()   { printf '  PASS  %s\n' "$1"; PASS=$((PASS + 1)); }
bad()  { printf '  FAIL  %s\n' "$1"; FAIL=$((FAIL + 1)); }
check() { if [ "$2" = "$3" ]; then ok "$1 ($2)"; else bad "$1 (expected $3, got $2)"; fi; }

QUESTION='{"question":"Compare three open-source workflow orchestration frameworks."}'

# A visitor is a cookie *string*, not a cookie jar. Concurrent curls sharing a
# jar file race to write it, so each one ends up minting its own session --
# which silently turns "one visitor submitting twelve times" into "twelve
# visitors submitting once", and every per-session limit appears not to work.
new_visitor() {
  # GET /api/tasks, not /api/meta: only the task routes depend on a session,
  # so only they mint the cookie. `|| true` because this runs under
  # `set -o pipefail`, where a grep that matches nothing would otherwise kill
  # the whole script -- silently, mid-section.
  curl -s -o /dev/null -D - --max-time 15 "$BASE/api/tasks" 2>/dev/null \
    | { grep -i '^set-cookie: af_session=' || true; } \
    | head -1 | sed -E 's/^[Ss]et-[Cc]ookie: ([^;]+).*/\1/' | tr -d '\r'
}

submit() {
  local cookie="$1"
  # The trailing newline matters: these are written to files that are later
  # counted with grep, and without it twelve results concatenate into one
  # unmatchable line.
  curl -s -o /dev/null -w '%{http_code}\n' --max-time 25 \
    -H "Cookie: $cookie" -X POST "$BASE/api/tasks" \
    -H 'Content-Type: application/json' -d "$QUESTION"
}

tasks_for() {
  curl -s --max-time 15 -H "Cookie: $1" "$BASE/api/tasks" \
    | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["items"]))'
}

say "0. the stack is in the offline profile"
MODE="$(curl -s --max-time 15 "$BASE/api/meta" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["llm_provider"], d["search_provider"])')"
if [ "$MODE" = "stub fixture" ]; then
  ok "offline profile confirmed ($MODE)"
else
  bad "expected 'stub fixture', got '$MODE' -- refusing to hammer a live deployment"
  exit 1
fi

LIMIT="$(curl -s --max-time 15 "$BASE/api/meta" >/dev/null 2>&1; echo "${E2E_RATE_LIMIT_PER_MINUTE:-200}")"

say "1. concurrent submissions from ONE session"
# Fired together, not in sequence: a limit enforced by read-then-write admits
# more than it should under concurrency.
COOKIE_A="$(new_visitor)"
[ -n "$COOKIE_A" ] || { bad "could not mint a session cookie"; exit 1; }
for i in $(seq 1 12); do submit "$COOKIE_A" > "$TMP/a-$i.code" & done
wait
cat "$TMP"/a-*.code | sort | uniq -c | sed 's/^/    /'
ACCEPTED=$({ cat "$TMP"/a-*.code | grep -c '^202$'; } || true)
SESSION_CAP="$(tasks_for "$COOKIE_A")"
SESSION_LIMIT="${DEMO_MAX_TASKS_PER_SESSION:-5}"
if [ "$ACCEPTED" -le "$SESSION_LIMIT" ]; then
  ok "12 concurrent submissions admitted $ACCEPTED, within the per-session limit of $SESSION_LIMIT"
else
  bad "admitted $ACCEPTED, over the per-session limit of $SESSION_LIMIT"
fi
if [ "$SESSION_CAP" = "$ACCEPTED" ]; then
  ok "every accepted submission produced exactly one task ($SESSION_CAP)"
else
  bad "task rows ($SESSION_CAP) do not match accepted submissions ($ACCEPTED)"
fi

say "2. a second visitor is unaffected by the first"
COOKIE_B="$(new_visitor)"
check "a fresh session can still submit" "$(submit "$COOKIE_B")" "202"
check "the second visitor sees only their own task" "$(tasks_for "$COOKIE_B")" "1"

say "3. cross-session access is refused"
FIRST_TASK="$(curl -s --max-time 15 -H "Cookie: $COOKIE_A" "$BASE/api/tasks" | python3 -c 'import json,sys; items=json.load(sys.stdin)["items"]; print(items[0]["id"] if items else "")')"
if [ -n "$FIRST_TASK" ]; then
  for path in "" "/steps" "/sources" "/claims" "/report" "/export"; do
    CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -H "Cookie: $COOKIE_B" "$BASE/api/tasks/$FIRST_TASK$path")"
    check "GET /api/tasks/{other}$path" "$CODE" "404"
  done
  CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -H "Cookie: $COOKIE_B" \
    -X POST "$BASE/api/tasks/$FIRST_TASK/approve" -H 'Content-Type: application/json' -d '{}')"
  check "POST approve on another session's task" "$CODE" "404"
else
  bad "could not find a task to probe cross-session access"
fi

say "4. one visitor cannot exhaust the deployment"
# 40 submissions from a single session, fired together. Whatever refuses them
# -- the per-session limit or the rate limiter -- the deployment must not
# accept 40 pieces of work from one visitor.
COOKIE_C="$(new_visitor)"
for i in $(seq 1 40); do submit "$COOKIE_C" > "$TMP/c-$i.code" & done
wait
cat "$TMP"/c-*.code | sort | uniq -c | sed 's/^/    /'
BURST_ACCEPTED=$({ cat "$TMP"/c-*.code | grep -c '^202$'; } || true)
BURST_REFUSED=$({ cat "$TMP"/c-*.code | grep -c '^429$'; } || true)
if [ "$BURST_REFUSED" -gt 0 ]; then
  ok "$BURST_REFUSED of 40 refused with 429"
else
  bad "40 concurrent submissions produced no 429 at all"
fi
if [ "$BURST_ACCEPTED" -le "${DEMO_MAX_TASKS_PER_SESSION:-5}" ]; then
  ok "only $BURST_ACCEPTED admitted, within the per-session limit"
else
  bad "admitted $BURST_ACCEPTED from one visitor"
fi

say "5. Redis outage: the limiter must fail OPEN, not closed"
docker compose stop redis >/dev/null 2>&1
sleep 2
COOKIE_D="$(new_visitor)"
CODE_META="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$BASE/api/meta")"
check "reads still served with Redis down" "$CODE_META" "200"
# The limiter fails open, so the request reaches the handler -- which then
# cannot queue the job and must say so cleanly.
check "submission refused with 503, not 500" "$(submit "$COOKIE_D")" "503"
BODY="$(curl -s --max-time 20 -H "Cookie: $COOKIE_D" -X POST "$BASE/api/tasks" \
  -H 'Content-Type: application/json' -d "$QUESTION")"
if echo "$BODY" | grep -q 'queue_unavailable' 2>/dev/null; then
  ok "the error carries the queue_unavailable code"
else
  bad "unexpected error body: $(echo "$BODY" | head -c 160)"
fi
if echo "$BODY" | grep -qiE 'redis|traceback|connection refused'; then
  bad "the response leaks broker detail"
else
  ok "no broker detail leaked to the client"
fi
READY="$(curl -s --max-time 20 "$BASE/ready" | python3 -c 'import json,sys; print(json.load(sys.stdin)["ready"])' 2>/dev/null || echo "unreachable")"
check "readiness reports the stack as not ready" "$READY" "False"

say "6. recovery after Redis returns"
docker compose start redis >/dev/null 2>&1
for _ in $(seq 1 30); do
  sleep 2
  if [ "$(curl -s --max-time 10 "$BASE/ready" | python3 -c 'import json,sys; print(json.load(sys.stdin)["ready"])' 2>/dev/null || echo False)" = "True" ]; then
    break
  fi
done
READY_AGAIN="$(curl -s --max-time 15 "$BASE/ready" | python3 -c 'import json,sys; print(json.load(sys.stdin)["ready"])')"
check "readiness recovers without restarting the API" "$READY_AGAIN" "True"
check "submissions work again" "$(submit "$(new_visitor)")" "202"

say "result"
printf '  %s passed, %s failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
printf '\nABUSE CHECK PASSED\n'
