import { NODE_LABELS, NODE_ORDER, type NodeName, type StepView, type TaskStatus } from '@/types/api'

/**
 * Where the workflow has got to, in one line.
 *
 * The workflow takes minutes with live models, so "something is happening"
 * is not enough: this says which stage of how many, and names the stage. The
 * same text is announced to screen readers through a polite live region, so
 * progress is not information only sighted users get.
 */
export function StageProgress({
  steps,
  status,
}: {
  steps: StepView[]
  status: TaskStatus
}) {
  const latestByNode = new Map(steps.map((step) => [step.node, step]))
  const done = NODE_ORDER.filter((node) => latestByNode.get(node)?.status === 'succeeded').length
  const running = NODE_ORDER.find((node) => latestByNode.get(node)?.status === 'running')
  const failed = NODE_ORDER.find((node) => latestByNode.get(node)?.status === 'failed')
  const total = NODE_ORDER.length

  const message = describe({ status, done, total, running, failed })

  return (
    // Grouped and named so the progress region is addressable without putting
    // an aria-label on the live region itself: a labelled live region can be
    // announced as its label rather than its changed content.
    <div role="group" aria-label="Workflow progress">
      <div className="flex items-baseline justify-between gap-3">
        {/* The visible text *is* the live region, rather than there being a
            separate screen-reader copy: duplicating it put the same sentence
            in the accessibility tree twice. Polite, because progress should
            not interrupt whatever is being read. */}
        <p role="status" aria-live="polite" className="text-xs font-medium text-ink-muted">
          {message}
        </p>
        <p className="font-mono text-xs text-ink-muted">
          {done}/{total}
        </p>
      </div>

      {/* Decorative: the same information is in the text above, so the bar
          itself is hidden from assistive technology rather than duplicated. */}
      <div
        aria-hidden="true"
        className="mt-1.5 flex gap-1"
      >
        {NODE_ORDER.map((node) => {
          const step = latestByNode.get(node)
          const tone =
            step?.status === 'succeeded'
              ? 'bg-positive'
              : step?.status === 'failed'
                ? 'bg-critical'
                : step?.status === 'running'
                  ? 'bg-accent animate-pulse'
                  : 'bg-line'
          return <span key={node} className={`h-1.5 flex-1 rounded-full ${tone}`} />
        })}
      </div>
    </div>
  )
}

function describe({
  status,
  done,
  total,
  running,
  failed,
}: {
  status: TaskStatus
  done: number
  total: number
  running: NodeName | undefined
  failed: NodeName | undefined
}): string {
  if (failed) return `Failed at ${NODE_LABELS[failed]}`
  if (status === 'approved') return 'Approved — all stages complete'
  if (status === 'awaiting_approval') return 'All stages complete — waiting for your review'
  if (status === 'queued' && done === 0) return 'Queued — waiting for a worker'
  if (running) return `Stage ${done + 1} of ${total}: ${NODE_LABELS[running]}`
  return `${done} of ${total} stages complete`
}
