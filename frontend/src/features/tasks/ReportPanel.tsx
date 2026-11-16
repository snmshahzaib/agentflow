import { EmptyState, Spinner } from '@/components/Primitives'
import { useReport } from '@/hooks/useApi'
import { api } from '@/services/api'
import type { TaskStatus } from '@/types/api'

/**
 * Report preview and export.
 *
 * The Markdown is shown as pre-wrapped text rather than rendered to HTML: the
 * body contains model-generated content, and rendering it would introduce an
 * injection surface for no benefit at this stage. Proper rendering, with
 * sanitisation, belongs with the full UI work in M5.
 */
export function ReportPanel({ taskId, status }: { taskId: string; status: TaskStatus }) {
  const report = useReport(taskId, status)
  const canExport = status === 'approved'

  if (report.isPending) return <Spinner label="Loading report…" />
  if (report.isError || report.data === undefined) {
    return (
      <EmptyState
        title="No report yet"
        body="The report appears once the drafting step has run."
      />
    )
  }

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm">
          Version {report.data.version}
          <span className="ml-2 font-mono text-xs text-ink-muted">{report.data.status}</span>
        </p>
        {canExport ? (
          <a
            href={api.exportUrl(taskId)}
            className="rounded-lg border border-line px-3 py-1.5 text-sm font-medium hover:bg-surface-muted"
          >
            Download Markdown
          </a>
        ) : (
          <p className="text-xs text-ink-muted">
            Export unlocks after approval.
          </p>
        )}
      </div>

      <pre className="mt-3 max-h-[28rem] overflow-auto whitespace-pre-wrap break-words rounded-lg border border-line bg-canvas p-4 font-mono text-xs leading-relaxed">
        {report.data.content_markdown}
      </pre>
    </div>
  )
}
