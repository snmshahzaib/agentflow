import { EmptyState, ErrorState, Spinner } from '@/components/Primitives'
import { StatusBadge } from '@/components/StatusBadge'
import { useTasks } from '@/hooks/useApi'
import { ApiError } from '@/services/api'
import { NODE_LABELS } from '@/types/api'

function formatWhen(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
}

export function TaskList({
  selectedId,
  onSelect,
}: {
  selectedId: string | null
  onSelect: (id: string) => void
}) {
  const tasks = useTasks()

  if (tasks.isPending) {
    return (
      <div className="px-1 py-6">
        <Spinner label="Loading your research…" />
      </div>
    )
  }

  if (tasks.isError) {
    const error = tasks.error instanceof ApiError ? tasks.error : null
    return (
      <ErrorState
        title="Could not load tasks"
        message={error?.message ?? 'Unknown error.'}
        correlationId={error?.correlationId}
        onRetry={() => void tasks.refetch()}
      />
    )
  }

  if (tasks.data.items.length === 0) {
    return (
      <EmptyState
        title="No research yet"
        body="Submit a question above and the workflow timeline will appear here."
      />
    )
  }

  return (
    <ul className="space-y-2">
      {tasks.data.items.map((task) => {
        const isSelected = task.id === selectedId
        return (
          <li key={task.id}>
            <button
              type="button"
              onClick={() => onSelect(task.id)}
              aria-current={isSelected ? 'true' : undefined}
              className={`w-full rounded-xl border p-4 text-left transition-colors ${
                isSelected
                  ? 'border-accent/60 bg-surface'
                  : 'border-line bg-surface hover:bg-surface-muted'
              }`}
            >
              <div className="flex items-start justify-between gap-3">
                <p className="line-clamp-2 text-sm font-medium">{task.question}</p>
                <StatusBadge status={task.status} />
              </div>
              <p className="mt-2 text-xs text-ink-muted">
                {formatWhen(task.created_at)}
                {task.current_node ? ` · ${NODE_LABELS[task.current_node]}` : ''}
              </p>
            </button>
          </li>
        )
      })}
    </ul>
  )
}
