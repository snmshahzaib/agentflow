# Durable approval flow

The human approval pause consumes **no running compute** and survives a restart of every
process. The worker does not sit waiting on a user.

```mermaid
sequenceDiagram
  participant U as User
  participant A as API
  participant R as Redis
  participant W as Worker
  participant D as PostgreSQL
  U->>A: POST /api/tasks (Idempotency-Key)
  A->>D: insert research_task (queued)
  A->>R: enqueue run_task(task_id)
  A-->>U: 202 Accepted + task id
  W->>R: claim job, acquire single-flight lock(task_id)
  W->>D: status=running
  loop each graph node
    W->>D: BEGIN; workflow_step + domain rows + llm_call + checkpoint; COMMIT
  end
  W->>D: status=awaiting_approval
  Note over W,R: interrupt() — the job ENDS here. No process waits.
  U->>A: POST /api/tasks/{id}/approve
  A->>D: approval_event, status=approved
  A->>R: enqueue resume_task(task_id)
  W->>D: load checkpoint, resume with the decision
  U->>A: GET /api/tasks/{id}/export
  A-->>U: 200 Markdown  (409 if not approved)
```

Restart recovery is a demo step, not a disclaimer: `docker compose restart worker` mid-run
resumes from the last committed checkpoint without duplicating steps or reports.
*(Verified by an automated test at M2.)*
