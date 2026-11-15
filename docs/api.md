# API

Generated from the running application's OpenAPI document, so it cannot drift
from the implementation. The interactive version is at `/docs` (disabled when
`APP_ENV=production`).

All responses are JSON. Every failure uses one envelope:

```json
{
  "error": {
    "code": "not_found",
    "message": "No such research task.",
    "details": {},
    "correlation_id": "9c791858c6a84549a07b70631b9b97c9"
  }
}
```

`code` is stable and safe to branch on; `message` is for humans and may
change. `correlation_id` appears on every server log line for that request,
and is echoed in the `X-Correlation-ID` response header.

## Authentication

There are no user accounts — the MVP excludes them. The first request issues a
signed, HTTP-only session cookie (`af_session`), and a task is visible only to
the session that created it. A task belonging to another session returns
**404, not 403**: the endpoint must not confirm that an id exists.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/meta` | Resolved runtime configuration |
| `GET` | `/api/tasks` | List this session's tasks |
| `POST` | `/api/tasks` | Submit a research task |
| `GET` | `/api/tasks/{task_id}` | Task detail |
| `POST` | `/api/tasks/{task_id}/approve` | Approve the current report |
| `GET` | `/api/tasks/{task_id}/steps` | Workflow timeline |
| `GET` | `/health` | Liveness probe |
| `GET` | `/ready` | Readiness probe |

*8 endpoints, from OpenAPI 3.1.0, API version 0.1.0.*

## Examples

Submit a research task. Returns **202**, not 200: the work has not run yet.

```bash
curl -i -c jar.txt -X POST http://localhost:8000/api/tasks \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: my-submission-1' \
  -d '{"question":"Compare three open-source workflow orchestration frameworks."}'
```

```
HTTP/1.1 202 Accepted
Location: /api/tasks/dba407db-541a-4c5c-969b-e9ddc60763c2
X-Correlation-ID: e144c40e7c554ff5830d8626c9a70e0f
```

Repeating the same `Idempotency-Key` returns the original task instead of
creating a second one, so a retry after a dropped response is harmless.

Poll until the workflow reaches the approval gate:

```bash
curl -s -b jar.txt http://localhost:8000/api/tasks/$TASK_ID | jq .status
# "queued" -> "running" -> "awaiting_approval"
```

Read the timeline. Every attempt appears, so a retry stays visible rather than
being overwritten by its successor:

```bash
curl -s -b jar.txt http://localhost:8000/api/tasks/$TASK_ID/steps \
  | jq '.[] | {node, status, attempt, duration_ms}'
```

Approve. Only a task actually awaiting approval may be approved; anything else
is a **409**, because approving otherwise would fabricate a decision about a
report that does not exist yet.

```bash
curl -s -b jar.txt -X POST http://localhost:8000/api/tasks/$TASK_ID/approve \
  -H 'Content-Type: application/json' -d '{"feedback":"Looks right."}'
```

Inspect the resolved runtime, including which model each node will use:

```bash
curl -s http://localhost:8000/api/meta | jq '{llm_provider, offline_mode, models}'
```

## Status codes

| Code | When |
|---|---|
| 202 | A task was accepted and queued |
| 200 | A read succeeded, or an approval was recorded |
| 404 | No such task **for this session** |
| 409 | The task is not in a state that permits the operation (e.g. approving a queued task, or approving twice) |
| 422 | The request body, parameters or a path id were invalid |
| 429 | Rate limit or daily demo budget exceeded *(M4)* |
| 503 | A dependency is unavailable, or the LLM configuration is unusable |

## Pagination

`GET /api/tasks` is keyset-paginated, not offset-paginated, so results stay
stable while new tasks arrive. Pass the returned `next_cursor` back as
`?cursor=`; a null cursor means the end of the list.

## Not yet implemented

`GET /api/tasks/{id}/sources`, `/claims`, `/report`, `/export`,
`/request-revision` and `/retry` arrive with the real workflow in M2–M4. They
are specified in `planning/agentflow-refinement-proposal-v2.md` and are
deliberately absent here rather than stubbed, so this document describes only
what exists.
