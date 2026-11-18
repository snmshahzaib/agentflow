import { useState } from 'react'

import { Markdown } from '@/components/Markdown'
import { EmptyState, Skeleton } from '@/components/Primitives'
import { useReport } from '@/hooks/useApi'
import { api } from '@/services/api'
import type { ReportStatus, TaskStatus } from '@/types/api'

/**
 * The report, as a reviewer reads it.
 *
 * Two deliberate choices:
 *
 * **Rendered, with a raw view one click away.** Markdown is rendered so the
 * claim-support table and citations are legible, and the exact text that will
 * be exported stays inspectable -- a reviewer approving a document should be
 * able to see precisely what they are approving. The renderer treats the body
 * as hostile input; see `components/Markdown.tsx`.
 *
 * **Earlier versions stay readable.** A revision supersedes its predecessor
 * rather than deleting it, so the version switcher reads any round. Versions
 * are sequential from 1, so the latest version's number is also the count.
 */
export function ReportPanel({
  taskId,
  status,
  onVersionChange,
}: {
  taskId: string
  status: TaskStatus
  onVersionChange?: (version: number) => void
}) {
  const [requested, setRequested] = useState<number | undefined>(undefined)
  const [showRaw, setShowRaw] = useState(false)

  const latest = useReport(taskId, status)
  const selected = useReport(taskId, status, requested)
  const report = requested === undefined ? latest : selected

  if (report.isPending) return <Skeleton lines={6} label="Loading report…" />

  if (report.isError || report.data === undefined) {
    return (
      <EmptyState
        title="No report yet"
        body="The report appears once the drafting step has run. Nothing is hidden — it has not been written."
      />
    )
  }

  const latestVersion = latest.data?.version ?? report.data.version
  const versions = Array.from({ length: latestVersion }, (_, index) => index + 1)
  const current = report.data.version
  const canExport = status === 'approved'

  function choose(version: number) {
    const next = version === latestVersion ? undefined : version
    setRequested(next)
    onVersionChange?.(version)
  }

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          {versions.length > 1 ? (
            <div
              role="group"
              aria-label="Report version"
              className="inline-flex overflow-hidden rounded-lg border border-line"
            >
              {versions.map((version) => (
                <button
                  key={version}
                  type="button"
                  onClick={() => choose(version)}
                  aria-pressed={version === current}
                  className={`px-3 py-1.5 text-sm font-medium transition-colors ${
                    version === current
                      ? 'bg-accent text-accent-ink'
                      : 'hover:bg-surface-muted'
                  }`}
                >
                  v{version}
                </button>
              ))}
            </div>
          ) : (
            <p className="text-sm font-medium">Version {current}</p>
          )}
          <ReportStatusChip status={report.data.status} />
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => setShowRaw((shown) => !shown)}
            aria-pressed={showRaw}
            className="rounded-lg border border-line px-3 py-1.5 text-sm font-medium hover:bg-surface-muted"
          >
            {showRaw ? 'Show rendered' : 'Show Markdown source'}
          </button>
          {canExport ? (
            <a
              href={api.exportUrl(taskId)}
              download
              className="rounded-lg bg-accent px-3 py-1.5 text-sm font-semibold text-accent-ink"
            >
              Download Markdown
            </a>
          ) : (
            <p className="text-xs text-ink-muted">Export unlocks after approval.</p>
          )}
        </div>
      </div>

      {current !== latestVersion ? (
        <p className="mt-3 rounded-lg border border-caution/40 bg-caution/5 px-3 py-2 text-xs">
          You are reading version {current}, which was superseded by version {latestVersion}.
          Export always produces the approved version.
        </p>
      ) : null}

      <div className="mt-3 max-h-[36rem] overflow-y-auto rounded-lg border border-line bg-canvas p-4">
        {showRaw ? (
          <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed">
            {report.data.content_markdown}
          </pre>
        ) : (
          <Markdown>{report.data.content_markdown}</Markdown>
        )}
      </div>
    </div>
  )
}

const REPORT_STATUS: Record<ReportStatus, { label: string; className: string }> = {
  draft: { label: 'Draft', className: 'border-line text-ink-muted' },
  approved: { label: 'Approved', className: 'border-positive/50 text-positive' },
  superseded: { label: 'Superseded', className: 'border-caution/50 text-caution' },
}

function ReportStatusChip({ status }: { status: ReportStatus }) {
  const style = REPORT_STATUS[status]
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${style.className}`}
    >
      {style.label}
    </span>
  )
}
