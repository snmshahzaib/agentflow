import { Spinner } from '@/components/Primitives'
import { StepStatusBadge } from '@/components/StatusBadge'
import { NODE_DESCRIPTIONS, NODE_LABELS, NODE_ORDER, type StepView, type TaskStatus } from '@/types/api'

/**
 * Shows every node in execution order, including ones not yet reached, so the
 * shape of the workflow is legible before it finishes. Recorded attempts are
 * shown individually: a retry stays visible rather than being overwritten.
 */
export function WorkflowTimeline({
  steps,
  status,
  isLoading,
}: {
  steps: StepView[]
  status: TaskStatus
  isLoading: boolean
}) {
  const byNode = new Map<string, StepView[]>()
  for (const step of steps) {
    byNode.set(step.node, [...(byNode.get(step.node) ?? []), step])
  }

  return (
    <div>
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Workflow</h3>
        {isLoading ? <Spinner label="Updating…" /> : null}
      </div>

      <ol className="mt-3 space-y-2">
        {NODE_ORDER.map((node) => {
          const attempts = byNode.get(node) ?? []
          const reached = attempts.length > 0
          const latest = attempts.at(-1)

          return (
            <li
              key={node}
              className={`rounded-lg border p-3 ${
                reached ? 'border-line bg-surface' : 'border-dashed border-line/70'
              }`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className={`text-sm font-medium ${reached ? '' : 'text-ink-muted'}`}>
                    {NODE_LABELS[node]}
                  </p>
                  <p className="mt-0.5 text-xs text-ink-muted">{NODE_DESCRIPTIONS[node]}</p>
                </div>
                {latest ? (
                  <StepStatusBadge status={latest.status} />
                ) : (
                  <span className="shrink-0 text-xs text-ink-muted">
                    {status === 'queued' ? 'Waiting' : 'Not reached'}
                  </span>
                )}
              </div>

              {attempts.length > 1 ? (
                <p className="mt-2 text-xs text-caution">
                  {attempts.length} attempts recorded
                </p>
              ) : null}

              {latest?.error ? (
                <p className="mt-2 text-xs text-critical">{latest.error}</p>
              ) : null}

              {latest?.duration_ms !== null && latest?.duration_ms !== undefined ? (
                <p className="mt-2 font-mono text-xs text-ink-muted">
                  {latest.duration_ms} ms
                  {latest.input_summary ? ` · ${latest.input_summary}` : ''}
                </p>
              ) : null}
            </li>
          )
        })}
      </ol>
    </div>
  )
}
