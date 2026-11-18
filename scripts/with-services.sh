#!/usr/bin/env bash
# Run a command against the Compose-managed Postgres and Redis.
#
#   scripts/with-services.sh uv run pytest
#   scripts/with-services.sh uv run pytest -m integration
#   scripts/with-services.sh uv run python scripts/run_evals.py
#
# Why this exists. The integration tests and dev tools need Postgres and
# Redis, and the obvious defaults ("localhost:5432", "localhost:6379") are
# wrong here in two ways: Compose publishes those services on *configurable*
# host ports (POSTGRES_HOST_PORT, REDIS_HOST_PORT), and the database password
# comes from the untracked .env. A developer who happens to run Postgres
# locally gets a silent connection to the wrong server rather than an error.
#
# So the connection strings are derived rather than assumed: the published
# ports come from `docker compose port`, and the credentials from .env (with
# .env.example's documented defaults as the fallback). Anything already set in
# the environment wins, so CI -- which runs service containers on fixed ports
# -- is unaffected.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

[ "$#" -gt 0 ] || { printf 'usage: %s COMMAND [ARGS...]\n' "$0" >&2; exit 2; }

read_env() {
  local key="$1" default="$2" value=""
  for file in .env .env.example; do
    if [ -f "$file" ]; then
      value="$(grep -E "^${key}=" "$file" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' || true)"
      [ -n "$value" ] && break
    fi
  done
  printf '%s' "${value:-$default}"
}

published() {
  # "0.0.0.0:5434" -> "5434". Empty if the service is not running.
  docker compose port "$1" "$2" 2>/dev/null | awk -F: '{print $NF}'
}

PG_PORT="$(published postgres 5432)"
REDIS_PORT="$(published redis 6379)"

if [ -z "$PG_PORT" ] || [ -z "$REDIS_PORT" ]; then
  printf 'FAILED: Postgres and Redis are not running.\n\n  Start them with:\n    docker compose up -d postgres redis\n' >&2
  exit 1
fi

PG_USER="$(read_env POSTGRES_USER agentflow)"
PG_PASSWORD="$(read_env POSTGRES_PASSWORD agentflow)"

base="postgresql+asyncpg://${PG_USER}:${PG_PASSWORD}@localhost:${PG_PORT}"

# Four databases, deliberately separate:
#   agentflow_test  reset between integration runs
#   agentflow_eval  the evaluation harness, so a run cannot disturb the others
#   agentflow_live  preserved live-provider evidence; never truncated
export TEST_DATABASE_URL="${TEST_DATABASE_URL:-${base}/agentflow_test}"
export EVAL_DATABASE_URL="${EVAL_DATABASE_URL:-${base}/agentflow_eval}"
export LIVE_DATABASE_URL="${LIVE_DATABASE_URL:-${base}/agentflow_live}"
export REDIS_URL="${REDIS_URL:-redis://localhost:${REDIS_PORT}/0}"

# Create the databases if they are absent, so a clean checkout needs one
# command rather than a documented psql incantation. Runs inside the container
# so no host Postgres client is required, and the password is passed through
# the environment rather than on a command line.
ensure_database() {
  local name="$1"
  local exists
  exists="$(docker compose exec -T -e PGPASSWORD="$PG_PASSWORD" postgres \
    psql -U "$PG_USER" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname = '${name}'" 2>/dev/null || true)"
  if [ "$exists" != "1" ]; then
    docker compose exec -T -e PGPASSWORD="$PG_PASSWORD" postgres \
      createdb -U "$PG_USER" "$name" >/dev/null 2>&1 \
      && printf '  created database %s\n' "$name" >&2 \
      || printf '  WARNING: could not create database %s\n' "$name" >&2
  fi
}

if [ "${SKIP_DB_BOOTSTRAP:-0}" != "1" ]; then
  for db in agentflow_test agentflow_eval agentflow_live; do
    ensure_database "$db"
  done
fi

printf '  postgres : localhost:%s\n  redis    : localhost:%s\n\n' "$PG_PORT" "$REDIS_PORT" >&2

cd backend
exec "$@"
