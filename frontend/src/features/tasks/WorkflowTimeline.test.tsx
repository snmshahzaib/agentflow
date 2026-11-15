import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { WorkflowTimeline } from '@/features/tasks/WorkflowTimeline'
import { steps } from '@/test/fixtures'

describe('WorkflowTimeline', () => {
  it('shows every node in order, including ones not yet reached', () => {
    render(<WorkflowTimeline steps={[]} status="queued" isLoading={false} />)
    for (const label of ['Plan', 'Retrieve sources', 'Analyse', 'Check citations', 'Draft report']) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
    expect(screen.getAllByText(/waiting/i).length).toBeGreaterThan(0)
  })

  it('keeps a retried node visible rather than overwriting the failure', () => {
    render(<WorkflowTimeline steps={steps} status="running" isLoading={false} />)
    expect(screen.getByText(/2 attempts recorded/i)).toBeInTheDocument()
    // The latest attempt is the one whose status is shown.
    expect(screen.getAllByText('Succeeded').length).toBe(2)
  })

  it('describes the citation check without implying fact-checking', () => {
    render(<WorkflowTimeline steps={[]} status="queued" isLoading={false} />)
    expect(
      screen.getByText(/not whether it is true/i),
    ).toBeInTheDocument()
  })

  it('reports that retrieval makes no model call', () => {
    render(<WorkflowTimeline steps={[]} status="queued" isLoading={false} />)
    expect(screen.getByText(/No model call/i)).toBeInTheDocument()
  })
})
