import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { StageProgress } from '@/features/tasks/StageProgress'
import type { StepView } from '@/types/api'

function step(node: StepView['node'], status: StepView['status']): StepView {
  return {
    id: `${node}-${status}`,
    node,
    status,
    attempt: 1,
    input_summary: null,
    output_json: null,
    error: status === 'failed' ? 'provider_unavailable' : null,
    started_at: '2026-11-15T12:00:00Z',
    finished_at: null,
    duration_ms: null,
  }
}

describe('StageProgress', () => {
  it('names the stage in progress, not just that something is happening', () => {
    render(
      <StageProgress
        steps={[step('plan', 'succeeded'), step('research', 'running')]}
        status="running"
      />,
    )
    expect(screen.getByText(/Stage 2 of 5: Retrieve sources/)).toBeInTheDocument()
    expect(screen.getByText('1/5')).toBeInTheDocument()
  })

  it('announces progress to assistive technology exactly once', () => {
    // Progress must not be information only sighted users get -- and the
    // visible text is itself the live region, so the same sentence does not
    // appear in the accessibility tree twice.
    render(<StageProgress steps={[step('plan', 'succeeded')]} status="running" />)
    const live = screen.getByRole('status')
    expect(live).toHaveAttribute('aria-live', 'polite')
    expect(live).toHaveTextContent(/1 of 5 stages complete|Stage/)
    expect(screen.getAllByText(live.textContent)).toHaveLength(1)
  })

  it('reports a queued task as waiting for a worker', () => {
    render(<StageProgress steps={[]} status="queued" />)
    expect(screen.getByText(/Queued — waiting for a worker/)).toBeInTheDocument()
  })

  it('says where a failure happened', () => {
    render(
      <StageProgress
        steps={[step('plan', 'succeeded'), step('research', 'failed')]}
        status="failed"
      />,
    )
    expect(screen.getByText(/Failed at Retrieve sources/)).toBeInTheDocument()
  })

  it('distinguishes waiting for review from being finished', () => {
    const done = (['plan', 'research', 'analyse', 'citation_check', 'draft'] as const).map((n) =>
      step(n, 'succeeded'),
    )
    const { rerender } = render(<StageProgress steps={done} status="awaiting_approval" />)
    expect(screen.getByText(/waiting for your review/)).toBeInTheDocument()

    rerender(<StageProgress steps={done} status="approved" />)
    expect(screen.getByText(/Approved — all stages complete/)).toBeInTheDocument()
  })
})
