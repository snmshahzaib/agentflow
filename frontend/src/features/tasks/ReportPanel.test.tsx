import { screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ReportPanel } from '@/features/tasks/ReportPanel'
import { renderWithQuery } from '@/test/render'

afterEach(() => vi.unstubAllGlobals())

function stubReport() {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: 'r1',
          version: 2,
          status: 'draft',
          content_markdown: '# Report\n\n## Limitations\n\n- Fixture result.',
          created_at: '2026-11-16T12:00:00Z',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    ),
  )
}

describe('ReportPanel', () => {
  it('shows the report version and content', async () => {
    stubReport()
    renderWithQuery(<ReportPanel taskId="t1" status="awaiting_approval" />)
    expect(await screen.findByText(/version 2/i)).toBeInTheDocument()
    expect(screen.getByText(/## Limitations/)).toBeInTheDocument()
  })

  it('offers no download until the report is approved', async () => {
    // Export is gated server-side too; the UI must not imply otherwise.
    stubReport()
    renderWithQuery(<ReportPanel taskId="t1" status="awaiting_approval" />)
    await screen.findByText(/version 2/i)
    expect(screen.queryByRole('link', { name: /download/i })).not.toBeInTheDocument()
    expect(screen.getByText(/export unlocks after approval/i)).toBeInTheDocument()
  })

  it('offers the download once approved', async () => {
    stubReport()
    renderWithQuery(<ReportPanel taskId="t1" status="approved" />)
    const link = await screen.findByRole('link', { name: /download markdown/i })
    expect(link).toHaveAttribute('href', '/api/tasks/t1/export')
  })
})
