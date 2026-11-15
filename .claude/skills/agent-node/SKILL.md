---
name: agent-node
description: Add or change a node in AgentFlow's LangGraph research workflow, following the project's node contract — pure function, Pydantic output schema, budget check, transactional step persistence, stub fixture and tests. Use when implementing or modifying the plan, research, analyse, citation_check or draft nodes.
---

# Add or change a graph node

## The contract

A node is a plain async function. It must stay orchestrator-agnostic so the
LangGraph dependency remains replaceable (ADR-002).

```python
async def my_node(state: GraphState, deps: NodeDeps) -> GraphState:
    """One sentence on what this node is responsible for."""
```

Rules, all enforced by review or by a hook:

1. **No LangGraph import.** `app/graph/builder.py` binds dependencies and is
   the only module that knows the orchestrator exists.
2. **Return a partial state delta.** Never mutate `state`.
3. **Never name a model.** Call `deps.llm.complete_structured(...)`; the model
   comes from `deps.settings.model_for_node(node)`. A pre-commit hook rejects
   a hardcoded model ID.
4. **Declare a Pydantic output schema** and pass it as `schema=`. Never parse
   free text — schema-constrained output is layer 3 of the prompt-injection
   boundary (`docs/security.md`).
5. **Check the budget before the call.** A node must fail cleanly rather than
   overrun `max_tokens_budget` / `max_calls_budget`.
6. **Persist in one transaction** — the `workflow_steps` row, any domain rows
   and the task update commit together, or not at all.
7. **Treat retrieved content as untrusted.** It goes in a delimited block
   labelled as data, never in the system prompt, and the model is offered no
   tools.

## Steps

1. Add the node name to `app/domain/nodes.py` if it is new (and to `LLM_NODES`
   only if it actually calls a model — `research` deliberately does not).
2. Define the output schema next to the node.
3. Write the node in `app/graph/nodes/`, following `skeleton.py`'s step
   persistence.
4. Wire it in `app/graph/builder.py` and add the edge.
5. Register a stub response in `app/adapters/llm/stub_responses.json` so the
   node runs offline and in CI. A missing entry fails loudly by design.
6. Add a per-node environment variable to `NODE_MODEL_ENV_VARS` in
   `app/core/config.py`, to `.env.example`, and to the table in
   `docs/model-configuration.md`.
7. Add tests: the node records a step; a re-run records a new attempt; the
   output schema is enforced; budget exhaustion fails cleanly.
8. Add the node's label and description to `frontend/src/types/api.ts` so the
   timeline shows it. Descriptions must not overstate what the node does — the
   citation check confirms a claim points at a retrieved source, not that it
   is true.

## Verify

```bash
cd backend && uv run pytest tests/test_graph.py -q && uv run lint-imports
```

Then run the `verify-local` skill before reporting the node as working.
