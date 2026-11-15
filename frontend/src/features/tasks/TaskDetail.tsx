import { useState } from 'react'

import { Card, EmptyState, ErrorState, Spinner } from '@/components/Primitives'
import { StatusBadge } from '@/components/StatusBadge'
import { WorkflowTimeline } from '@/features/tasks/WorkflowTimeline'
import { useApproveTask, useSteps, useTask } from '@/hooks/useApi'
import { ApiError } from '@/services/api'

export function TaskDetail({ taskId }: { taskId: string | null }) {
  const [feedback, setFeedback] = useState('')
  const task = useTask(taskId ?? undefined)
  const steps = useSteps(taskId ?? undefined, task.data?.status)
  const approve = useApproveTask(taskId ?? '')

  if (!taskId) {
    return (
      <EmptyState
        title="No task selected"
        body="Choose a task from the list to see its workflow, provenance and approval gate."
      />
    )
  }

  if (task.isPending) {
    return (
      <Card>
        <Spinner label="Loading task…" />
      </Card>
    )
  }

  if (task.isError) {
    const error = task.error instanceof ApiError ? task.error : null
    return (
      <ErrorState
        title="Could not load this task"
        message={error?.message ?? 'Unknown error.'}
        correlationId={error?.correlationId}
        onRetry={() => void task.refetch()}
      />
    )
  }

  const detail = task.data
  const canApprove = detail.status === 'awaiting_approval'
  const approveError = approve.error instanceof ApiError ? approve.error : null

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex items-start justify-between gap-3">
          <h2 className="text-base font-semibold">{detail.question}</h2>
          <StatusBadge status={detail.status} />
        </div>

        <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
          <div>
            <dt className="text-xs text-ink-muted">Model provider</dt>
            <dd className="font-mono text-xs">{detail.llm_provider}</dd>
          </div>
          <div>
            <dt className="text-xs text-ink-muted">Search</dt>
            <dd className="font-mono text-xs">{detail.search_mode}</dd>
          </div>
          <div>
            <dt className="text-xs text-ink-muted">Model calls</dt>
            <dd className="font-mono text-xs">
              {detail.calls_used}/{detail.max_calls_budget}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-ink-muted">Tokens</dt>
            <dd className="font-mono text-xs">
              {detail.tokens_used.toLocaleString()}/{detail.max_tokens_budget.toLocaleString()}
            </dd>
          </div>
        </dl>

        {detail.error_message ? (
          <div className="mt-4">
            <ErrorState
              title={`Task failed (${detail.error_code ?? 'unknown'})`}
              message={detail.error_message}
            />
          </div>
        ) : null}
      </Card>

      <Card>
        <WorkflowTimeline
          steps={steps.data ?? []}
          status={detail.status}
          isLoading={steps.isFetching}
        />
      </Card>

      <Card>
        <h3 className="text-sm font-semibold">Approval gate</h3>
        {canApprove ? (
          <>
            <p className="mt-1 text-sm text-ink-muted">
              The workflow is paused and no process is waiting. Approving resumes it from its
              last checkpoint.
            </p>
            <label htmlFor="feedback" className="mt-4 block text-sm font-medium">
              Reviewer note <span className="font-normal text-ink-muted">(optional)</span>
            </label>
            <textarea
              id="feedback"
              rows={2}
              value={feedback}
              maxLength={2000}
              onChange={(event) => setFeedback(event.target.value)}
              className="mt-2 w-full resize-y rounded-lg border border-line bg-canvas px-3 py-2 text-sm"
            />
            <div className="mt-3 flex items-center gap-3">
              <button
                type="button"
                disabled={approve.isPending}
                onClick={() => approve.mutate(feedback.trim() || undefined)}
                className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-accent-ink disabled:opacity-50"
              >
                Approve report
              </button>
              {approve.isPending ? <Spinner label="Approving…" /> : null}
            </div>
            {/* Revision is M2: it needs real report versioning behind it, and
                a button that silently did nothing would be worse than none. */}
          </>
        ) : (
          <p className="mt-1 text-sm text-ink-muted">
            {detail.status === 'approved'
              ? 'This report was approved. Export becomes available in the next milestone.'
              : 'Nothing to approve yet — the workflow has not reached the gate.'}
          </p>
        )}

        {approveError ? (
          <div className="mt-4">
            <ErrorState
              title="Could not approve"
              message={approveError.message}
              correlationId={approveError.correlationId}
              onRetry={() => approve.reset()}
            />
          </div>
        ) : null}
      </Card>
    </div>
  )
}
