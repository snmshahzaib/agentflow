import { EmptyState, Spinner } from '@/components/Primitives'
import { useSources } from '@/hooks/useApi'
import type { TaskStatus } from '@/types/api'

/**
 * Retrieved evidence, in the order the model saw it. The label shown here is
 * the one that appears in the report's citations, so a reader can follow a
 * claim back to its source.
 */
export function SourcesPanel({
  taskId,
  status,
}: {
  taskId: string
  status: TaskStatus
}) {
  const sources = useSources(taskId, status)

  if (sources.isPending) return <Spinner label="Loading sources…" />
  if (sources.isError) return <p className="text-sm text-critical">Could not load sources.</p>
  if (sources.data.length === 0) {
    return (
      <EmptyState
        title="No sources yet"
        body="Sources appear once the retrieval step has run."
      />
    )
  }

  return (
    <ul className="space-y-2">
      {sources.data.map((source) => (
        <li key={source.id} className="rounded-lg border border-line p-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm font-medium">
                <span className="mr-2 font-mono text-xs text-ink-muted">{source.label}</span>
                {source.title ?? source.url}
              </p>
              <a
                href={source.url}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-0.5 block truncate font-mono text-xs text-accent hover:underline"
              >
                {source.url}
              </a>
            </div>
            {source.is_fixture ? (
              <span className="shrink-0 rounded-full border border-caution/50 px-2 py-0.5 text-xs font-medium text-caution">
                Fixture
              </span>
            ) : null}
          </div>
          {source.excerpt ? (
            <p className="mt-2 line-clamp-3 text-xs text-ink-muted">{source.excerpt}</p>
          ) : null}
        </li>
      ))}
    </ul>
  )
}
