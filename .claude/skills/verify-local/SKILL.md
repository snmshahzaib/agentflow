---
name: verify-local
description: Run AgentFlow's full local verification gate (lint, types, architecture boundaries, secret scan, backend and frontend tests, optional live smoke test) and report exactly what passed and what did not. Use before claiming a milestone is complete, before reporting progress, and before any commit that touches more than one layer.
---

# Verify locally

The honest pre-claim gate. **Never report a milestone, a fix or a feature as
working without running this and pasting its real output.** "Should work" is
not a result.

## Prerequisites

PostgreSQL and Redis must be reachable. Either start the compose stack, or use
local instances with `backend/.env` pointing at them.

```bash
pg_isready && redis-cli ping
```

If either is down, say so and stop — a skipped gate reported as a pass is the
failure mode this skill exists to prevent.

## Run

```bash
cd backend
uv run ruff check .                      # lint
uv run ruff format --check .             # formatting
uv run mypy app                          # types
uv run lint-imports                       # architecture contracts
uv run pytest -q                          # all tests
uv run pytest --cov=app --cov-report=term # coverage, if numbers are needed
```

```bash
cd frontend
pnpm lint                                 # ESLint, --max-warnings 0
pnpm typecheck                            # tsc -b
pnpm test                                 # Vitest
pnpm build                                # production build must succeed
```

```bash
gitleaks dir . --config .gitleaks.toml --redact --no-banner
gitleaks git . --config .gitleaks.toml --redact --no-banner
```

Or everything the backend side covers in one command:

```bash
make verify
```

## End-to-end (when the change touches the API, worker or graph)

```bash
cd backend && uv run alembic upgrade head
# terminal 1
uv run uvicorn app.main:app --port 8000
# terminal 2
uv run arq app.workers.settings.WorkerSettings
# terminal 3
scripts/smoke-test.sh
```

The smoke test must print `SMOKE TEST PASSED`. Anything else is a failure,
including a hang.

## Expected output

Every command above exits 0. The current baseline, for comparison:

- backend: 195 tests pass (140 unit, 55 integration), 84% line coverage
- mypy: clean across 52 source files
- import-linter: 4 contracts, all kept
- frontend: 10 tests pass, ESLint and `tsc -b` clean, build succeeds
- gitleaks: no leaks in the tree or in history

## Reporting

State what ran, what passed, and what failed — with the real output. If a gate
was skipped (no database, Docker not running), name it explicitly as skipped
rather than omitting it. Update `docs/testing.md` when the numbers change.
