## What and why

<!-- The diff says what changed. Say why it needed to. -->

## Verification

<!-- Paste real output. "Should work" is not a result. -->

- [ ] `scripts/with-services.sh uv run pytest`
- [ ] `cd frontend && pnpm lint && pnpm typecheck && pnpm test`
- [ ] `scripts/e2e-test.sh` (if the UI or API surface changed)
- [ ] `cd backend && uv run python ../scripts/audit-docs.py` (if documentation changed)

```
<!-- output here -->
```

## Checklist

- [ ] No stub or fixture output is presented as live AI
- [ ] Claim checking is not described as fact checking anywhere
- [ ] No model ID hardcoded outside the LLM adapter
- [ ] Documentation updated, and still true
- [ ] No `Co-Authored-By` trailer for assistants or tooling
