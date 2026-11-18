import { StepStatusBadge } from '@/components/StatusBadge'
import {
  NODE_DESCRIPTIONS,
  NODE_LABELS,
  NODE_ORDER,
  type StepView,
  type TaskStatus,
} from '@/types/api'

/**
 * Every node in execution order, including ones not yet reached, so the shape
 * of the workflow is legible before it finishes.
 *
 * Retries are shown individually rather than collapsed. A worker can die
 * after a node finishes but before the checkpoint is written, which re-runs
 * that node as a new attempt; hiding that would make the timeline a tidier
 * but less truthful account of what happened.
 */
export function WorkflowTimeline({
  steps,
  status,
}: {
  steps: StepView[]
  status: TaskStatus
}) {
  const byNode = new Map<string, StepView[]>()
  for (const step of steps) {
    byNode.set(step.node, [...(byNode.get(step.node) ?? []), step])
  }

  return (
    <ol className="space-y-2">
      {NODE_ORDER.map((node, index) => {
        const attempts = byNode.get(node) ?? []
        const latest = attempts.at(-1)
        const reached = attempts.length > 0

        return (
          <li
            key={node}
            className={`rounded-lg border p-3 ${
              reached ? 'border-line bg-surface' : 'border-dashed border-line/70'
            }`}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex min-w-0 gap-3">
                <span
                  aria-hidden="true"
                  className={`mt-0.5 inline-flex size-5 shrink-0 items-center justify-center rounded-full text-[0.65rem] font-semibold ${
                    latest?.status === 'succeeded'
                      ? 'bg-positive/15 text-positive'
                      : latest?.status === 'failed'
                        ? 'bg-critical/15 text-critical'
                        : latest?.status === 'running'
                          ? 'bg-accent/15 text-accent'
                          : 'bg-surface-muted text-ink-muted'
                  }`}
                >
                  {index + 1}
                </span>
                <div className="min-w-0">
                  <p className={`text-sm font-medium ${reached ? '' : 'text-ink-muted'}`}>
                    {NODE_LABELS[node]}
                  </p>
                  <p className="mt-0.5 text-xs text-ink-muted">{NODE_DESCRIPTIONS[node]}</p>
                </div>
              </div>
              {latest ? (
                <StepStatusBadge status={latest.status} />
              ) : (
                <span className="shrink-0 text-xs text-ink-muted">
                  {status === 'queued' ? 'Waiting' : 'Not reached'}
                </span>
              )}
            </div>

            {latest?.error ? (
              <p
                role="alert"
                className="mt-2 rounded border border-critical/30 bg-critical/5 px-2 py-1 font-mono text-xs text-critical"
              >
                {latest.error}
              </p>
            ) : null}

            {attempts.length > 1 ? (
              <details className="mt-2">
                <summary className="cursor-pointer text-xs font-medium text-caution">
                  {attempts.length} attempts recorded
                </summary>
                <ul className="mt-2 space-y-1 border-l border-line pl-3">
                  {attempts.map((attempt) => (
                    <li key={attempt.id} className="font-mono text-xs text-ink-muted">
                      attempt {attempt.attempt} · {attempt.status}
                      {attempt.duration_ms !== null ? ` · ${attempt.duration_ms} ms` : ''}
                      {attempt.error ? ` · ${attempt.error}` : ''}
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}

            {latest && latest.duration_ms !== null ? (
              <p className="mt-2 font-mono text-xs text-ink-muted">
                {latest.duration_ms} ms
                {latest.input_summary ? ` · ${latest.input_summary}` : ''}
              </p>
            ) : null}
          </li>
        )
      })}
    </ol>
  )
}
