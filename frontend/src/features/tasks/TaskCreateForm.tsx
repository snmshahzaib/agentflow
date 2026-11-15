import { useState } from 'react'

import { Card, ErrorState, Spinner } from '@/components/Primitives'
import { useCreateTask } from '@/hooks/useApi'
import { ApiError } from '@/services/api'

const MIN_LENGTH = 10
const MAX_LENGTH = 2000

export function TaskCreateForm({ onCreated }: { onCreated: (taskId: string) => void }) {
  const [question, setQuestion] = useState('')
  const [touched, setTouched] = useState(false)
  const createTask = useCreateTask()

  const trimmed = question.trim()
  // Mirrors the backend's own constraint, so the user is told before a round
  // trip rather than after a 422.
  const validationMessage =
    trimmed.length === 0
      ? 'Enter a research question.'
      : trimmed.length < MIN_LENGTH
        ? `Add a little more detail (at least ${MIN_LENGTH} characters).`
        : null

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setTouched(true)
    if (validationMessage) return
    createTask.mutate(trimmed, {
      onSuccess: (task) => {
        setQuestion('')
        setTouched(false)
        onCreated(task.id)
      },
    })
  }

  const showError = touched && validationMessage
  const error = createTask.error instanceof ApiError ? createTask.error : null

  return (
    <Card>
      <form onSubmit={handleSubmit} noValidate>
        <label htmlFor="question" className="block text-sm font-medium">
          Research question
        </label>
        <p id="question-help" className="mt-1 text-sm text-ink-muted">
          State what you want compared or investigated. The planner breaks it into a few
          bounded subtasks.
        </p>
        <textarea
          id="question"
          name="question"
          rows={3}
          value={question}
          maxLength={MAX_LENGTH}
          onChange={(event) => setQuestion(event.target.value)}
          onBlur={() => setTouched(true)}
          aria-describedby="question-help"
          aria-invalid={showError ? true : undefined}
          placeholder="Compare three open-source workflow orchestration frameworks for a small engineering team."
          className="mt-3 w-full resize-y rounded-lg border border-line bg-canvas px-3 py-2 text-sm placeholder:text-ink-muted/70"
        />

        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-ink-muted">
            {trimmed.length}/{MAX_LENGTH} characters
          </p>
          <div className="flex items-center gap-3">
            {createTask.isPending ? <Spinner label="Submitting…" /> : null}
            <button
              type="submit"
              disabled={createTask.isPending}
              className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-accent-ink disabled:opacity-50"
            >
              Start research
            </button>
          </div>
        </div>

        {showError ? (
          <p role="alert" className="mt-3 text-sm text-critical">
            {validationMessage}
          </p>
        ) : null}
      </form>

      {error ? (
        <div className="mt-4">
          <ErrorState
            title="Could not start the task"
            message={error.message}
            correlationId={error.correlationId}
            onRetry={() => createTask.reset()}
          />
        </div>
      ) : null}
    </Card>
  )
}
