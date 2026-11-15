# AgentFlow

**Human-in-the-loop multi-agent research orchestrator.** Submit a research question, watch
specialised agents plan it, retrieve evidence, check every claim against its sources, and
approve the report before it can be exported.

> **Status: in active development — Milestone 1 of 8.**
> This README documents what is *implemented*, not what is planned. The status table below is
> the authoritative list. Nothing here claims to be production-tested, and no live deployment
> exists yet.

---

## The problem

An analyst comparing options across the public web repeats the same unpaid work every time:
decompose the question, search, skim, track which source actually said what, and assemble
something a colleague will trust. A chatbot answer collapses all of that into one opaque
paragraph — the sub-questions, the sources consulted, the claims that *weren't* supported, and
the decision to publish all disappear.

AgentFlow makes each of those steps a durable, inspectable artifact, and puts a human approval
gate in front of publication.

## How it works

```mermaid
flowchart LR
  subgraph Browser
    UI[React + TypeScript]
  end
  subgraph API["API process (FastAPI)"]
    R[Routers] --> SVC[Services]
  end
  subgraph WORK["Worker process (arq)"]
    J[Job handler] --> G[LangGraph StateGraph]
    G --> N1[plan] --> N2[research] --> N3[analyse] --> N4[citation check] --> N5[draft]
  end
  UI -->|REST + polling| R
  SVC -->|enqueue / resume| Q[(Redis)]
  Q --> J
  SVC --> DB[(PostgreSQL)]
  J --> DB
  N2 --> SA[Search port: fixture / Tavily]
  N1 & N3 & N4 & N5 --> LA[LLM port: stub / OpenRouter]
  G -.checkpoints.-> DB
```

Five nodes, each a single schema-validated LLM call over text the system already holds:

| Node | Responsibility |
|---|---|
| `plan` | Decompose the question into at most 4 bounded subtasks |
| `research` | Retrieve sources per subtask through the configured search port (**no LLM call**) |
| `analyse` | Synthesise findings, each tied to retrieved sources |
| `citation_check` | Adjudicate whether each claim is supported by a retrieved source |
| `draft` | Produce the report with citations and a generated limitations section |

Then the graph **interrupts**. The job ends — no process sits waiting — and the task rests in
`awaiting_approval` until a human approves or requests a revision, at which point a resume job
continues the graph from its checkpoint.

### Two design decisions worth calling out

**The model never gets internet access.** Our code performs the search; each node is one
structured-output call over text we already hold. The *graph* is the agent, not a tool-calling
loop. This removes an entire class of runaway-agent and SSRF failure modes.

**Retrieved web content is untrusted input.** It is delimited and labelled as data, the model
has no tools at synthesis time, every node returns a schema-validated object, and citation
identifiers are re-bound against the sources actually retrieved — a model cannot invent a
citation that survives. See [`docs/security.md`](docs/security.md).

## Honest terminology

The `citation_check` node verifies **that a claim points to a retrieved source**. It does not
verify that the claim is true. AgentFlow makes no factual-accuracy guarantee, and the report's
limitations section is generated from actual run data (search mode, model served per node,
source count, unsupported-claim count, token spend) so it cannot drift from reality.

## Technology

| Layer | Choice |
|---|---|
| API | Python 3.12, FastAPI, Pydantic v2 |
| Orchestration | LangGraph (graph + `interrupt()` + Postgres checkpointer only) |
| Worker / queue | arq over Redis |
| Database | PostgreSQL, SQLAlchemy 2.0 async, Alembic |
| LLM | Provider-agnostic OpenAI-compatible gateway; OpenRouter by default |
| Search | Pluggable port: offline fixtures (default) or Tavily |
| Frontend | React 19, TypeScript, Vite, Tailwind, TanStack Query |
| Quality | ruff, mypy, import-linter, pytest, Vitest, gitleaks |

**No model ID appears in application code.** Every model is resolved from environment
variables and validated against the provider catalogue at startup — see
[`docs/model-configuration.md`](docs/model-configuration.md).

## Implementation status

| Milestone | Scope | Status |
|---|---|---|
| M1 | Walking skeleton + LLM config spine | **complete — awaiting review** |
| M2 | Fixture vertical slice, approval gate, restart recovery | not started |
| M3 | Live OpenRouter gateway, capability negotiation, cost ledger | not started |
| M4 | Tavily search, URL safety, rate limits, retention | not started |
| M5 | Complete frontend | not started |
| M6 | Verification, documentation, evaluation harness | not started |
| M7 | Deployment and public repository | not started |
| M8 | Completion report | not started |

## What exists today (end of M1)

Working and verified end to end in Docker: task submission with idempotency, durable
execution by a separate worker process, the five-node graph recording a workflow timeline,
the human approval gate (`interrupt()` ends the job — the pause costs no running compute),
resume from a Postgres checkpoint in a separate job, session-scoped access, the full error
envelope with correlation ids, and the provider-agnostic LLM gateway with capability
validation and cross-vendor fallback.

**The five graph nodes are skeletons.** They record the timeline and prove the durable
execution path, but they do not yet plan, retrieve, analyse, check citations or draft. Every
step row they write is marked `skeleton` so their output cannot be mistaken for research.
Real node implementations, sources, reports, claims and export arrive in M2–M4.

## Quick start

Prerequisites: Docker Desktop, and [uv](https://docs.astral.sh/uv/) for backend work outside
containers.

```bash
cp .env.example .env          # then edit .env — it is gitignored
docker compose up --build
```

| Service | URL |
|---|---|
| Frontend | http://localhost:8080 |
| API docs | http://localhost:8000/docs |
| Readiness | http://localhost:8000/ready |

AgentFlow runs with **no API keys at all** in its default configuration: `LLM_PROVIDER=stub`
and `SEARCH_PROVIDER=fixture` give a fully deterministic offline demo. Fixture and stub output
is labelled as such in the UI and is never presented as live AI.

Every published host port is configurable in `.env` (`API_PORT`, `FRONTEND_PORT`,
`POSTGRES_HOST_PORT`, `REDIS_HOST_PORT`), which matters if you already run Postgres or Redis
locally — a clash shows up as `Bind for 127.0.0.1:5433 failed: port is already allocated`.

Verify the whole stack end to end:

```bash
scripts/smoke-test.sh http://localhost:8080
```

It submits a task, waits for the approval gate, approves it, and checks that idempotency,
double-approval rejection and session scoping all behave. It prints `SMOKE TEST PASSED` or
exits non-zero.

See [`docs/development.md`](docs/development.md) for running the API, worker and frontend
outside Docker, and [`docs/testing.md`](docs/testing.md) for the test commands.

## Documentation

| Document | Contents |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Components, boundaries, process model |
| [`docs/model-configuration.md`](docs/model-configuration.md) | Model matrix, capability validation, costs |
| [`docs/data-model.md`](docs/data-model.md) | Entities, relationships, indexes |
| [`docs/api.md`](docs/api.md) | Endpoints, schemas, errors |
| [`docs/security.md`](docs/security.md) | Trust boundaries, injection defence, secret handling |
| [`docs/testing.md`](docs/testing.md) | Test categories and verified results |
| [`docs/decisions.md`](docs/decisions.md) | Architecture decision records |

## Licence

MIT — see [LICENSE](LICENSE).
