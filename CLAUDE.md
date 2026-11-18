# CLAUDE.md — AgentFlow

Guidance for Claude Code working in this repository. Read `../AI_Portfolio_Claude_Code_Master_Plan.md`
and `../planning/agentflow-refinement-proposal-v2.md` for the approved plan; this file is the
operational contract.

## What this project is

A human-in-the-loop multi-agent research orchestrator. A user submits a research question; five
graph nodes plan it, retrieve sources, synthesise findings, check claim-to-source support, and
draft a report; the graph then pauses for human approval before export is permitted.

**MVP boundary.** Out of scope: multi-user accounts, SSO, billing, autonomous browsing beyond
the search provider's returned content, server-side fetching of arbitrary URLs, PDF export,
token-level streaming, Kubernetes, vector search, tool-calling agents with internet access. Any
change to this boundary needs the repository owner's approval.

## Non-negotiable rules

1. **Never claim a command passed unless it actually ran.** Paste real output. "Should work" is
   not a result. This applies to tests, migrations, Docker, linting and deployment alike.
2. **Never present stub or fixture output as live AI.** `LLM_PROVIDER=stub` and
   `SEARCH_PROVIDER=fixture` output must be labelled in the UI, in docs and in any screenshot.
3. **Never hardcode a model ID in application code.** Models resolve from environment variables
   through `app.core.config` and `app.adapters.llm.registry`. A pre-commit hook enforces this.
4. **Never commit secrets.** Only `*.example` files are tracked. gitleaks runs pre-commit and in
   CI. Never bypass a hook with `--no-verify`.
5. **Never document unimplemented behaviour as working.** The README status table and
   `docs/testing.md` must match reality.
6. **One project at a time.** Do not touch the sibling portfolio projects.
7. **Commits carry one author: the repository owner.** Use the repo-local Git identity
   (`git config --local user.name/user.email`), never a global or inferred one. Do **not** add
   `Co-Authored-By` trailers for assistants or tooling, and do not add "generated with" lines.
   Eleven commits were rewritten on 2026-11-18 to remove such trailers; do not reintroduce
   them.

## Architecture boundaries (enforced by `lint-imports` in CI)

```
app.api | app.workers             <- entrypoints; peers, may NOT import each other
       app.graph                  <- orchestration; the worker drives the graph
       app.services               <- use cases, transactions
       app.db                     <- SQLAlchemy models and queries
       app.domain                 <- pure types and rules, framework-free
app.adapters.{llm,search,queue}   <- outbound ports; may import domain/core only
```

- `app.domain` imports no framework and no other app package. It is pure Python.
- Vendor SDKs (`openai`, …) may be imported **only** inside `app.adapters`. Reaching one
  transitively (worker -> registry -> adapter) is fine and expected.
- The LangGraph dependency is confined to `app.graph.builder` and `app.workers`. Node functions
  in `app.graph.nodes` are plain `async def node(state) -> dict` and must stay
  orchestrator-agnostic, so the graph runner can be replaced without touching them.

## Process model

Two process types over one codebase and one image:

- `uvicorn app.main:app` — HTTP, short-lived requests, never calls an LLM.
- `arq app.workers.settings.WorkerSettings` — long research jobs, owns all LLM and search calls.

The API enqueues; the worker executes. A task's rows are written only by the worker that holds
its single-flight Redis lock.

## Development commands

All backend commands run from `backend/`.

```bash
uv sync --extra dev                 # install dependencies
uv run uvicorn app.main:app --reload --port 8000
uv run arq app.workers.settings.WorkerSettings
uv run alembic upgrade head         # apply migrations
uv run alembic revision --autogenerate -m "description"
uv run ruff check . && uv run ruff format .
uv run mypy app
uv run lint-imports                 # architecture contracts
uv run pytest                       # all tests
uv run pytest tests/test_llm_registry.py::test_name -q   # a single test
uv run pytest -m "not integration"  # skip tests needing Postgres/Redis
```

Frontend commands run from `frontend/`:

```bash
pnpm install
pnpm dev
pnpm lint
pnpm test
pnpm build
```

Whole stack:

```bash
docker compose up --build
make verify        # the honest pre-claim gate: lint, types, boundaries, tests
```

## Writing a graph node

Every node follows the same contract. See `.claude/skills/agent-node/SKILL.md`.

1. Signature `async def node(state: GraphState, deps: NodeDeps) -> GraphState` — returns a
   partial state delta, never mutates `state`. `app.graph.builder` binds `deps`, so nodes stay
   orchestrator-agnostic.
2. Declare a Pydantic output model. Call the LLM through `deps.llm.complete_structured(...)`
   with that schema. Never parse free text.
3. Check the task budget **before** the call; a node must fail cleanly rather than overrun.
4. Persist a `workflow_steps` row plus any domain rows in one transaction, inside the same unit
   of work as the graph checkpoint.
5. Add a stub response in `app/adapters/llm/stub_responses.json` so the node runs offline
   and in CI. A missing entry fails loudly by design — an incomplete offline demo must not
   look complete.
6. Enforce bounds on the model's output in code, not only in the prompt. The prompt asks;
   the code guarantees.

## Error handling

- Every API error returns `{"error": {"code", "message", "details", "correlation_id"}}`.
- `app.core.errors` defines the exception hierarchy; routers never build ad-hoc error bodies.
- Adapter failures are normalised in `app.adapters.llm.errors` into retryable vs terminal.
  Node and service code must never branch on a vendor-specific error shape.
- Every log line is JSON and carries `correlation_id`; worker lines also carry `task_id`.

## Security rules for node and prompt code

Retrieved web content is untrusted input. See `docs/security.md`.

- Untrusted content goes in a delimited block labelled as data, never into the system prompt.
- No tools are offered to the model at synthesis time.
- Validate every model output against a schema; reject, never coerce.
- Re-bind citation identifiers to sources actually retrieved; drop unknown identifiers.
- A `supported` or `partial` verdict with no resolvable source must be downgraded to
  `unsupported`. Support with nothing to point at is not support.
- Never describe the citation check as fact checking, in code, copy, docs or commit
  messages. It checks whether a retrieved source *states* a claim.
- Say "containment", not "prevention", about prompt injection. Whether a model obeys an
  injected instruction is not measured by this project.
- `LLM_REQUIRE_STRUCTURED_OUTPUT=true` is the live default and must not be relaxed to make a
  model work.

## Current status

**Milestone: M2 complete — fixture research workflow, approval gate, revision, export
restrictions, restart recovery and prompt-injection containment.** M3 (live OpenRouter) may
not begin without the repository owner's approval.

Default configuration is offline: `LLM_PROVIDER=stub`, `SEARCH_PROVIDER=fixture`. Output is
deterministic fixture content and must be labelled as such everywhere it appears.

Definition of done for the project as a whole: working application, complete documentation,
live public deployment, published GitHub repository, and the owner's explicit approval. A
locally working application is not complete.
