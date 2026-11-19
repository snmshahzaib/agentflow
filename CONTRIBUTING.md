# Contributing

This is a portfolio project built to a fixed scope, so it is not looking for feature
contributions. Bug reports, questions and corrections are welcome — particularly if something
in the documentation does not match what the code actually does.

## Running it

Everything needed is in the [README quick start](README.md#quick-start). The default
configuration uses a deterministic stub model and a committed fixture corpus, so it needs no
API key, no account and no spend.

## Before opening a pull request

```bash
docker compose up -d postgres redis

# Backend
scripts/with-services.sh uv run pytest
cd backend && uv run ruff check . && uv run ruff format --check . \
  && uv run mypy app evals && uv run lint-imports

# Frontend
cd frontend && pnpm lint && pnpm typecheck && pnpm test && pnpm check:diagrams

# Browser, against the real stack
scripts/e2e-test.sh

# Documentation claims against the implementation
cd backend && uv run python ../scripts/audit-docs.py
```

`make verify` runs the core of this in one command.

## House rules

These are the conventions the codebase is built on. They are stricter than normal house style
because the project is about trustworthy output.

**Never claim a command passed unless it ran.** Paste the real output. This applies to commit
messages, pull request descriptions and documentation equally.

**Never present stub or fixture output as live AI.** `LLM_PROVIDER=stub` and
`SEARCH_PROVIDER=fixture` output must be labelled wherever it appears, including screenshots.

**Never describe claim checking as fact checking.** It establishes that a retrieved source
*states* a claim. It is not a judgement about truth, and no code comment, UI string, document
or commit message may imply otherwise.

**Say "containment", not "prevention", about prompt injection.** Whether a model obeys an
injected instruction is a property of that model; this project does not measure it.

**Never document unimplemented behaviour as working.** `scripts/audit-docs.py` checks the
mechanical parts of this in CI; the rest is on the author.

**No model ID in application code.** Models resolve from environment variables; a pre-commit
hook enforces it.

**Never bypass a hook with `--no-verify`.** gitleaks, type checks and the architecture
contracts run pre-commit for a reason.

## Commits

Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`, `perf:`). Explain *why* in
the body, not just what — the diff already says what.

Commits carry a single author. Please do not add `Co-Authored-By` trailers for assistants or
tooling, or "generated with" lines.

## Architecture boundaries

`import-linter` fails the build if a layer or a vendor SDK leaks across its boundary:

```
app.api | app.workers             entrypoints; peers, must not import each other
       app.graph                  orchestration
       app.services               use cases, transaction boundaries
       app.db                     models and queries
       app.domain                 pure types and rules, framework-free
app.adapters.{llm,search,queue}   outbound ports; import domain/core only
```

See [`docs/decisions.md`](docs/decisions.md) for why, and what was rejected.
