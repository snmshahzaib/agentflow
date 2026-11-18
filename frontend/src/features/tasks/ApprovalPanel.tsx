import { useState } from 'react'

import { ErrorState, Spinner } from '@/components/Primitives'
import { useApproveTask, useRequestRevision } from '@/hooks/useApi'
import { ApiError } from '@/services/api'
import type { TaskDetail } from '@/types/api'

const MIN_NOTE = 3
const MAX_NOTE = 2000

/**
 * The human gate.
 *
 * This is the one place in the product where a person is required, so the two
 * actions state their consequences rather than being a pair of unlabelled
 * buttons: approving unlocks export and ends the run, requesting a revision
 * re-plans the research and produces a new version. A revision requires a
 * note, because a revision with no explanation gives the workflow nothing to
 * act on.
 */
export function ApprovalPanel({
  task,
  currentVersion,
}: {
  task: TaskDetail
  /** The latest report version, so the revision button can name its result. */
  currentVersion: number | undefined
}) {
  const [note, setNote] = useState('')
  const [noteError, setNoteError] = useState<string | null>(null)

  const approve = useApproveTask(task.id)
  const revise = useRequestRevision(task.id)
  const busy = approve.isPending || revise.isPending

  const approveError = approve.error instanceof ApiError ? approve.error : null
  const reviseError = revise.error instanceof ApiError ? revise.error : null

  if (task.status !== 'awaiting_approval') {
    return (
      <p className="text-sm text-ink-muted">
        {task.status === 'approved'
          ? 'You approved this report. It can be exported from the Report tab.'
          : task.status === 'failed'
            ? 'This task failed before reaching the approval gate, so there is nothing to approve.'
            : 'The workflow has not reached the approval gate yet.'}
      </p>
    )
  }

  return (
    <div>
      <p className="text-sm text-ink-muted">
        The workflow is paused at a checkpoint and no process is waiting on you. Nothing can be
        exported until you approve.
      </p>

      <label htmlFor="reviewer-note" className="mt-4 block text-sm font-medium">
        Reviewer note{' '}
        <span className="font-normal text-ink-muted">
          (optional to approve, required to request a revision)
        </span>
      </label>
      <textarea
        id="reviewer-note"
        rows={3}
        value={note}
        maxLength={MAX_NOTE}
        onChange={(event) => {
          setNote(event.target.value)
          if (noteError) setNoteError(null)
        }}
        aria-describedby="reviewer-note-help"
        aria-invalid={noteError ? true : undefined}
        placeholder="What should change? For example: say more about operational burden for a small team."
        className="mt-2 w-full resize-y rounded-lg border border-line bg-canvas px-3 py-2 text-sm placeholder:text-ink-muted/70"
      />
      <p id="reviewer-note-help" className="mt-1 text-xs text-ink-muted">
        {note.trim().length}/{MAX_NOTE} characters
      </p>

      {noteError ? (
        <p role="alert" className="mt-2 text-sm text-critical">
          {noteError}
        </p>
      ) : null}

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <Action
          title="Approve report"
          body="Ends the run and unlocks the Markdown export."
          variant="primary"
          disabled={busy}
          pending={approve.isPending}
          pendingLabel="Approving…"
          onClick={() => approve.mutate(note.trim() || undefined)}
        />
        <Action
          title="Request revision"
          body={
            currentVersion === undefined
              ? 'Re-plans the research with your note and produces a new version. It may retrieve different sources.'
              : `Re-plans the research with your note and produces version ${currentVersion + 1}. It may retrieve different sources.`
          }
          variant="secondary"
          disabled={busy}
          pending={revise.isPending}
          pendingLabel="Sending back…"
          onClick={() => {
            const trimmed = note.trim()
            if (trimmed.length < MIN_NOTE) {
              setNoteError('Add a note explaining what should change.')
              document.getElementById('reviewer-note')?.focus()
              return
            }
            setNoteError(null)
            revise.mutate(trimmed, { onSuccess: () => setNote('') })
          }}
        />
      </div>

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

      {reviseError ? (
        <div className="mt-4">
          <ErrorState
            title="Could not request a revision"
            message={reviseError.message}
            correlationId={reviseError.correlationId}
            onRetry={() => revise.reset()}
          />
        </div>
      ) : null}
    </div>
  )
}

function Action({
  title,
  body,
  variant,
  disabled,
  pending,
  pendingLabel,
  onClick,
}: {
  title: string
  body: string
  variant: 'primary' | 'secondary'
  disabled: boolean
  pending: boolean
  pendingLabel: string
  onClick: () => void
}) {
  return (
    <div className="flex flex-col">
      <button
        type="button"
        disabled={disabled}
        onClick={onClick}
        className={`rounded-lg px-4 py-2 text-sm font-semibold disabled:opacity-50 ${
          variant === 'primary'
            ? 'bg-accent text-accent-ink'
            : 'border border-line font-medium hover:bg-surface-muted'
        }`}
      >
        {title}
      </button>
      <p className="mt-1.5 text-xs text-ink-muted">{body}</p>
      {pending ? (
        <div className="mt-1">
          <Spinner label={pendingLabel} />
        </div>
      ) : null}
    </div>
  )
}
