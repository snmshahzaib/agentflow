import { EmptyState, Skeleton } from '@/components/Primitives'
import { useSources } from '@/hooks/useApi'
import { safeUrl } from '@/lib/urls'
import type { SourceView, TaskStatus } from '@/types/api'

/**
 * Retrieved evidence, in the order the model saw it.
 *
 * The label shown against each source is the one that appears in the
 * report's citations, so a reader can follow a claim back to what it rests
 * on. Whether a source was retrieved live or came from the committed fixture
 * corpus is stated per source rather than inferred from configuration: a run
 * can legitimately mix the two, and a reader must be able to tell which is
 * which.
 */
export function SourcesPanel({ taskId, status }: { taskId: string; status: TaskStatus }) {
  const sources = useSources(taskId, status)

  if (sources.isPending) return <Skeleton lines={4} label="Loading sources…" />

  if (sources.isError) {
    return (
      <p role="alert" className="text-sm text-critical">
        Could not load sources.
      </p>
    )
  }

  if (sources.data.length === 0) {
    return (
      <EmptyState
        title="No sources yet"
        body="Sources appear once the retrieval step has run."
      />
    )
  }

  const live = sources.data.filter((source) => !source.is_fixture).length
  const fixture = sources.data.length - live

  return (
    <div>
      <p className="rounded-lg border border-line bg-surface-muted px-3 py-2 text-xs">
        {sources.data.length} source{sources.data.length === 1 ? '' : 's'} retrieved
        {live > 0 && fixture > 0
          ? ` — ${live} from the live web, ${fixture} from the fixture corpus.`
          : live > 0
            ? ' from the live web. Retrieval is real; AgentFlow does not assess whether a page is accurate, current or authoritative.'
            : ' from the committed fixture corpus. These are synthetic documents, not live web results.'}
      </p>

      <ul className="mt-3 space-y-2">
        {sources.data.map((source) => (
          <li key={source.id} className="rounded-lg border border-line p-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-sm font-medium">
                  <span className="mr-2 font-mono text-xs text-ink-muted">{source.label}</span>
                  {source.title ?? source.url}
                </p>
                <SourceLink source={source} />
              </div>
              <span
                className={`shrink-0 rounded-full border px-2 py-0.5 text-xs font-medium ${
                  source.is_fixture
                    ? 'border-caution/50 text-caution'
                    : 'border-line text-ink-muted'
                }`}
              >
                {source.is_fixture ? 'Fixture' : source.provider}
              </span>
            </div>
            {source.excerpt ? (
              <p className="mt-2 line-clamp-3 text-xs text-ink-muted">{source.excerpt}</p>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  )
}

function SourceLink({ source }: { source: SourceView }) {
  const href = safeUrl(source.url)

  // The backend rejects unsafe URLs before storing them, so this is defence
  // in depth. Showing the text without the link keeps the record visible
  // instead of silently dropping a source.
  if (!href) {
    return (
      <p className="mt-0.5 break-all font-mono text-xs text-ink-muted">
        {source.url} <span className="text-critical">(unsafe link, not opened)</span>
      </p>
    )
  }

  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="mt-0.5 block truncate font-mono text-xs text-accent hover:underline"
    >
      {source.url}
    </a>
  )
}
