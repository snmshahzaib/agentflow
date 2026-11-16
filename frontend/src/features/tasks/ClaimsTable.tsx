import { EmptyState, Spinner } from '@/components/Primitives'
import { useClaims } from '@/hooks/useApi'
import { SUPPORT_LABELS, type SupportStatus, type TaskStatus } from '@/types/api'

const BADGE: Record<SupportStatus, string> = {
  supported: 'border-positive/50 text-positive',
  partial: 'border-caution/50 text-caution',
  unsupported: 'border-critical/50 text-critical',
}

/**
 * Per-claim support. The wording here is deliberate and load-bearing: this
 * shows whether a retrieved source *states* a claim, not whether the claim is
 * true, and the UI must never imply the stronger thing.
 */
export function ClaimsTable({ taskId, status }: { taskId: string; status: TaskStatus }) {
  const claims = useClaims(taskId, status)

  if (claims.isPending) return <Spinner label="Loading claim checks…" />
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

  const unsupported = claims.data.filter((c) => c.support_status === 'unsupported').length

  return (
    <div>
      <p className="text-xs text-ink-muted">
        Each claim was checked against the sources retrieved for this task. This confirms a
        source <strong>states</strong> the claim — not that the claim is true.
      </p>
      {unsupported > 0 ? (
        <p className="mt-2 text-xs text-critical">
          {unsupported} claim{unsupported === 1 ? '' : 's'} could not be traced to any retrieved
          source.
        </p>
      ) : null}

      <ul className="mt-3 space-y-2">
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
      </ul>
    </div>
  )
}
