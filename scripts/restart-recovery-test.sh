#!/usr/bin/env bash
# Worker restart recovery against the real Docker stack.
#
#   scripts/restart-recovery-test.sh [base_url]
#
# Proves the durable-execution claim with actual process death, not mocks:
#
#   A. a task submitted and then hit by `docker compose restart worker`
#      mid-flight still completes exactly once -- no duplicate steps, no
#      duplicate reports;
#   B. a task parked at the approval gate survives a worker restart and can
#      still be revised and approved afterwards, because its state lives in
#      PostgreSQL rather than in the worker's memory.
#
# Exits non-zero on any failure.
set -euo pipefail

BASE="${1:-http://localhost:8080}"
JAR="$(mktemp)"
trap 'rm -f "$JAR"' EXIT

say()  { printf '\n== %s\n' "$1"; }
fail() { printf '\nFAILED: %s\n' "$1" >&2; exit 1; }
field() { python3 -c "import json,sys; print(json.load(sys.stdin)$1)"; }
count() { python3 -c "import json,sys; print(len(json.load(sys.stdin)))"; }

submit() {
  curl -sf -b "$JAR" -c "$JAR" -X POST "$BASE/api/tasks" \
    -H 'Content-Type: application/json' \
    -d "{\"question\":\"$1\"}" | field "['id']"
}

status_of() { curl -sf -b "$JAR" "$BASE/api/tasks/$1" | field "['status']"; }

wait_for() { # task, status, attempts
  local t="$1" want="$2" tries="${3:-80}" s=""
  for _ in $(seq 1 "$tries"); do
    s="$(status_of "$t")"
    [ "$s" = "$want" ] && { echo "$s"; return 0; }
    [ "$s" = "failed" ] && fail "task $t failed while waiting for $want"
    sleep 0.5
  done
  fail "task $t never reached $want (last: $s)"
}

counts() { # task -> "steps sources claims"
  local t="$1"
  printf '%s %s %s' \
    "$(curl -sf -b "$JAR" "$BASE/api/tasks/$t/steps"   | count)" \
    "$(curl -sf -b "$JAR" "$BASE/api/tasks/$t/sources" | count)" \
    "$(curl -sf -b "$JAR" "$BASE/api/tasks/$t/claims"  | count)"
}

say "stack health"
curl -sf "$BASE/api/meta" >/dev/null || fail "API is not reachable at $BASE"
docker compose ps --format '{{.Service}} {{.Status}}' | sed 's/^/  /'

# ---------------------------------------------------------------------------
say "A. restart the worker mid-flight"
# ---------------------------------------------------------------------------
TASK_A="$(submit 'Compare three open-source workflow orchestration frameworks for a small team.')"
echo "  submitted $TASK_A"
# Restart immediately: the job is either queued or part-way through a node.
docker compose restart worker >/dev/null 2>&1
echo "  worker restarted while the task was in flight"

STATUS="$(wait_for "$TASK_A" awaiting_approval 120)"
echo "  task reached: $STATUS"

read -r STEPS SOURCES CLAIMS <<<"$(counts "$TASK_A")"
echo "  steps=$STEPS sources=$SOURCES claims=$CLAIMS"

# A node interrupted mid-execution is re-run, so the step count may exceed
# five: the worker can die after a node finished but before LangGraph
# checkpointed it. That is correct retry behaviour, and the timeline records
# both attempts rather than hiding one. What must hold is that re-execution
# is SAFE -- no duplicate report, no duplicate source, every node completed.
[ "$STEPS" -ge 5 ]   || fail "expected at least 5 steps, got $STEPS"
[ "$SOURCES" -ge 3 ] || fail "expected sources to be retrieved, got $SOURCES"
[ "$CLAIMS" -ge 3 ]  || fail "expected claims to be recorded, got $CLAIMS"

curl -sf -b "$JAR" "$BASE/api/tasks/$TASK_A/steps" | python3 -c "
import json, sys
steps = json.load(sys.stdin)
nodes = {'plan', 'research', 'analyse', 'citation_check', 'draft'}
done = {s['node'] for s in steps if s['status'] == 'succeeded'}
missing = nodes - done
assert not missing, f'nodes never completed: {sorted(missing)}'
pairs = [(s['node'], s['attempt']) for s in steps]
assert len(pairs) == len(set(pairs)), 'duplicate (node, attempt) rows exist'
retried = len(steps) - len(nodes)
print(f'  all 5 nodes completed; {retried} node(s) re-ran after the kill, each as a new attempt')
" || fail "step history is inconsistent"

curl -sf -b "$JAR" "$BASE/api/tasks/$TASK_A/sources" | python3 -c "
import json, sys
sources = json.load(sys.stdin)
urls = [s['url'] for s in sources]
assert len(urls) == len(set(urls)), 'the same URL was stored twice'
assert all(s['is_fixture'] for s in sources), 'a source is not labelled as a fixture'
print(f'  {len(urls)} sources, all distinct and all labelled as fixtures')
" || fail "sources were duplicated by the re-run"

VERSIONS="$(curl -sf -b "$JAR" "$BASE/api/tasks/$TASK_A/report" | field "['version']")"
[ "$VERSIONS" -eq 1 ] || fail "expected a single report version, got v$VERSIONS"
echo "  exactly one report version despite the re-run"

# ---------------------------------------------------------------------------
say "B. restart the worker while parked at the approval gate"
# ---------------------------------------------------------------------------
docker compose restart worker >/dev/null 2>&1
sleep 6
echo "  worker restarted while the task waited for a human"
[ "$(status_of "$TASK_A")" = "awaiting_approval" ] \
  || fail "the parked task did not survive the restart"
echo "  task still awaiting approval -- state was in PostgreSQL, not memory"

say "export is refused before approval"
CODE="$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" "$BASE/api/tasks/$TASK_A/export")"
[ "$CODE" = "409" ] || fail "export returned $CODE before approval, expected 409"
echo "  409 as expected"

say "request a revision after the restart"
curl -sf -b "$JAR" -X POST "$BASE/api/tasks/$TASK_A/request-revision" \
  -H 'Content-Type: application/json' \
  -d '{"feedback":"Please say more about the licensing boundary."}' >/dev/null
wait_for "$TASK_A" awaiting_approval 120 >/dev/null

V2="$(curl -sf -b "$JAR" "$BASE/api/tasks/$TASK_A/report" | field "['version']")"
[ "$V2" -eq 2 ] || fail "expected report v2 after revision, got v$V2"
echo "  revision produced v2 from a checkpoint written before the restart"

read -r STEPS2 _ _ <<<"$(counts "$TASK_A")"
[ "$STEPS2" -ge $((STEPS + 5)) ] \
  || fail "a revision should add 5 steps; had $STEPS, now $STEPS2"
echo "  $STEPS2 steps recorded (was $STEPS, +5 for the revision); history preserved"

say "approve and export"
curl -sf -b "$JAR" -X POST "$BASE/api/tasks/$TASK_A/approve" \
  -H 'Content-Type: application/json' -d '{}' >/dev/null
wait_for "$TASK_A" approved 60 >/dev/null

EXPORTED="$(curl -sf -b "$JAR" "$BASE/api/tasks/$TASK_A/export")"
echo "$EXPORTED" | grep -q '## Limitations'      || fail "export is missing its limitations section"
echo "$EXPORTED" | grep -q 'not live AI research' || fail "export does not declare fixture provenance"
echo "  exported $(echo "$EXPORTED" | wc -l | tr -d ' ') lines, limitations and provenance present"

say "duplicate approval is still rejected"
CODE="$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" -X POST \
  "$BASE/api/tasks/$TASK_A/approve" -H 'Content-Type: application/json' -d '{}')"
[ "$CODE" = "409" ] || fail "second approval returned $CODE, expected 409"
echo "  409 as expected"

printf '\nRESTART RECOVERY TEST PASSED\n'
