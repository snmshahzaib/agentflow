import { EmptyState, Skeleton } from '@/components/Primitives'
import { useClaims } from '@/hooks/useApi'
import { SUPPORT_LABELS, type SupportStatus, type TaskStatus } from '@/types/api'

const BADGE: Record<SupportStatus, string> = {
  supported: 'border-positive/50 text-positive',
  partial: 'border-caution/50 text-caution',
  unsupported: 'border-critical/50 text-critical',
}

/**
 * Per-claim support.
 *
 * The wording here is load-bearing. This shows whether a retrieved source
 * *states* a claim, not whether the claim is true, and the UI must never
 * imply the stronger thing -- so the distinction is on screen next to the
 * verdicts rather than buried in documentation.
 */
export function ClaimsTable({
  taskId,
  status,
  version,
}: {
  taskId: string
  status: TaskStatus
  version?: number | undefined
}) {
  const claims = useClaims(taskId, status, version)

  if (claims.isPending) return <Skeleton lines={4} label="Loading claim checks…" />

  if (claims.isError || claims.data === undefined) {
    return (
      <EmptyState
        title="No claim checks yet"
        body="Claims appear once the citation-support step has run."
      />
    )
  }

  if (claims.data.length === 0) {
    return <EmptyState title="No claims recorded" body="This report contains no checked claims." />
  }

  const counts: Record<SupportStatus, number> = {
    supported: 0,
    partial: 0,
    unsupported: 0,
  }
  for (const claim of claims.data) counts[claim.support_status] += 1

  return (
    <div>
      <p className="rounded-lg border border-line bg-surface-muted px-3 py-2 text-xs">
        Each claim was checked against the sources retrieved for this task. This confirms a
        source <strong>states</strong> the claim — <strong>not that the claim is true</strong>.
        AgentFlow makes no factual-accuracy guarantee.
      </p>

      <ul className="mt-3 flex flex-wrap gap-2" aria-label="Claim support summary">
        {(['supported', 'partial', 'unsupported'] as const).map((key) =>
          counts[key] > 0 ? (
            <li
              key={key}
              className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${BADGE[key]}`}
            >
              {counts[key]} {SUPPORT_LABELS[key].toLowerCase()}
            </li>
          ) : null,
        )}
      </ul>

      {counts.unsupported > 0 ? (
        <p className="mt-3 rounded-lg border border-critical/30 bg-critical/5 px-3 py-2 text-xs text-critical">
          {counts.unsupported} claim{counts.unsupported === 1 ? '' : 's'} could not be traced to
          any retrieved source. Treat {counts.unsupported === 1 ? 'it' : 'them'} as unverified.
        </p>
      ) : null}

      <ol className="mt-3 space-y-2">
        {claims.data.map((claim) => (
          <li key={claim.id} className="rounded-lg border border-line p-3">
            <div className="flex items-start justify-between gap-3">
              <p className="text-sm">{claim.claim_text}</p>
              <span
                className={`shrink-0 rounded-full border px-2 py-0.5 text-xs font-medium ${BADGE[claim.support_status]}`}
              >
                {SUPPORT_LABELS[claim.support_status]}
              </span>
            </div>
            <p className="mt-2 font-mono text-xs text-ink-muted">
              {claim.source_labels.length > 0
                ? `Sources: ${claim.source_labels.join(', ')}`
                : 'No supporting source'}
            </p>
            {claim.rationale ? (
              <p className="mt-1 text-xs text-ink-muted">{claim.rationale}</p>
            ) : null}
          </li>
        ))}
      </ol>
    </div>
  )
}
