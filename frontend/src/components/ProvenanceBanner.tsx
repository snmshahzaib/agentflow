/**
 * States plainly what produced the output on screen.
 *
 * A core project rule: stub and fixture output is never presented as a live
 * model response. This banner is how the running configuration is made
 * visible rather than left to a reader's assumption.
 */

import type { Meta } from '@/types/api'

export function ProvenanceBanner({ meta }: { meta: Meta }) {
  if (meta.offline_mode) {
    return (
      <div className="rounded-xl border border-caution/40 bg-caution/5 px-4 py-3 text-sm">
        <p className="font-medium text-caution">Offline demo mode</p>
        <p className="mt-0.5 text-ink-muted">
          Results come from committed fixtures and a deterministic stub adapter — not from a
          live model or live web search. Output is repeatable and costs nothing.
        </p>
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-line bg-surface-muted px-4 py-3 text-sm">
      <p className="font-medium">Live mode</p>
      <p className="mt-0.5 text-ink-muted">
        Models are served via <span className="font-mono">{meta.llm_provider}</span>; sources
        are retrieved with <span className="font-mono">{meta.search_provider}</span>. Claim
        checks confirm that a claim points at a retrieved source, not that it is true.
      </p>
    </div>
  )
}
