#!/usr/bin/env bash
# End-to-end smoke test against a running AgentFlow stack.
#
#   scripts/smoke-test.sh [base_url]
#
# Submits a research task, waits for the workflow to reach the human approval
# gate, approves it, and verifies the task reaches `approved`. Exercises the
# durable path: enqueue -> worker claim -> step persistence -> interrupt ->
# resume. Exits non-zero on any failure, so CI and humans get the same answer.
set -euo pipefail

BASE="${1:-http://localhost:8000}"
JAR="$(mktemp)"
trap 'rm -f "$JAR"' EXIT

say() { printf '\n== %s\n' "$1"; }
fail() { printf '\nFAILED: %s\n' "$1" >&2; exit 1; }
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print(d$1)"; }

say "readiness"
curl -sf "$BASE/ready" | json "['ready']" | grep -qi true || fail "/ready is not ready"

say "runtime metadata"
META="$(curl -sf "$BASE/api/meta")"
echo "$META" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(f\"  provider={d['llm_provider']} search={d['search_provider']} offline={d['offline_mode']}\")
print(f\"  catalogue={d['catalogue_source']} strict_schema={d['require_structured_output']}\")
for m in d['models']:
    print(f\"  {m['node']:16} -> {m['model']}\")
"

say "submit a research task"
CREATED="$(curl -sf -c "$JAR" -X POST "$BASE/api/tasks" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: smoke-1' \
  -d '{"question":"Compare three open-source workflow orchestration frameworks."}')"
TASK_ID="$(echo "$CREATED" | json "['id']")"
STATUS="$(echo "$CREATED" | json "['status']")"
echo "  task=$TASK_ID status=$STATUS"
[ "$STATUS" = "queued" ] || fail "expected status queued, got $STATUS"

say "idempotency: the same key must not create a second task"
AGAIN="$(curl -sf -b "$JAR" -c "$JAR" -X POST "$BASE/api/tasks" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: smoke-1' \
  -d '{"question":"Compare three open-source workflow orchestration frameworks."}')"
[ "$(echo "$AGAIN" | json "['id']")" = "$TASK_ID" ] || fail "idempotency key created a duplicate"
echo "  same task returned"

say "wait for the approval gate"
for _ in $(seq 1 40); do
  STATUS="$(curl -sf -b "$JAR" "$BASE/api/tasks/$TASK_ID" | json "['status']")"
  [ "$STATUS" = "awaiting_approval" ] && break
  [ "$STATUS" = "failed" ] && fail "task failed before reaching the gate"
  sleep 0.5
done
[ "$STATUS" = "awaiting_approval" ] || fail "task never reached awaiting_approval (last: $STATUS)"
echo "  status=$STATUS"

say "workflow timeline"
curl -sf -b "$JAR" "$BASE/api/tasks/$TASK_ID/steps" | python3 -c "
import json,sys
steps=json.load(sys.stdin)
for s in steps:
    print(f\"  {s['node']:16} {s['status']:10} attempt={s['attempt']} {s['duration_ms']}ms\")
assert steps, 'no steps were recorded'
print(f'  {len(steps)} steps recorded')
"

say "approving before the gate must be impossible (already past it, so expect 200)"
CODE="$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" -X POST \
  "$BASE/api/tasks/$TASK_ID/approve" -H 'Content-Type: application/json' -d '{}')"
[ "$CODE" = "200" ] || fail "approve returned $CODE"
echo "  approved"

say "double approval must be rejected"
CODE="$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" -X POST \
  "$BASE/api/tasks/$TASK_ID/approve" -H 'Content-Type: application/json' -d '{}')"
[ "$CODE" = "409" ] || fail "second approval returned $CODE, expected 409"
echo "  second approval correctly rejected with 409"

say "final state"
FINAL="$(curl -sf -b "$JAR" "$BASE/api/tasks/$TASK_ID" | json "['status']")"
[ "$FINAL" = "approved" ] || fail "expected approved, got $FINAL"
echo "  status=$FINAL"

say "session scoping: a different session must not see this task"
CODE="$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/tasks/$TASK_ID")"
[ "$CODE" = "404" ] || fail "another session got $CODE, expected 404"
echo "  other session gets 404"

printf '\nSMOKE TEST PASSED\n'
