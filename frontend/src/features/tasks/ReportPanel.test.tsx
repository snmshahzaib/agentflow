import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ReportPanel } from '@/features/tasks/ReportPanel'
import { renderWithQuery } from '@/test/render'

afterEach(() => vi.unstubAllGlobals())

const MARKDOWN_V2 = [
  '# Orchestrator comparison',
  '',
  '> **Offline fixture run.** No language model was called.',
  '',
  '## Claim support',
  '',
  '| # | Claim | Support |',
  '|---|---|---|',
  '| 1 | Airflow has the heaviest footprint | supported |',
  '',
  '## Limitations',
  '',
  '- Based on 3 fixture sources.',
].join('\n')

const MARKDOWN_V1 = '# First attempt\n\nSuperseded content.'

/** Responds per requested version, like the API does. */
function stubReports({ latest = 2 }: { latest?: number } = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: string) => {
      const url = String(input)
      const asked = /version=(\d+)/.exec(url)?.[1]
      const version = asked ? Number(asked) : latest
      return Promise.resolve(
        new Response(
          JSON.stringify({
            id: `r${version}`,
            version,
            status: version === latest ? 'draft' : 'superseded',
            content_markdown: version === 1 && latest > 1 ? MARKDOWN_V1 : MARKDOWN_V2,
            created_at: '2026-11-16T12:00:00Z',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
    }),
  )
}

describe('ReportPanel', () => {
  it('renders the Markdown rather than showing its source', async () => {
    stubReports()
    renderWithQuery(<ReportPanel taskId="t1" status="awaiting_approval" />)

    // A real heading element, not a line of text beginning with "#".
    expect(
      await screen.findByRole('heading', { name: 'Orchestrator comparison', level: 3 }),
    ).toBeInTheDocument()
    // And the claim-support table is a table a screen reader can navigate.
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Claim' })).toBeInTheDocument()
  })

  it('can show the exact Markdown that will be exported', async () => {
    // A reviewer approving a document should be able to see precisely what
    // they are approving, not only a rendering of it.
    stubReports()
    renderWithQuery(<ReportPanel taskId="t1" status="awaiting_approval" />)
    await screen.findByRole('heading', { name: 'Orchestrator comparison', level: 3 })

    await userEvent.click(screen.getByRole('button', { name: /show markdown source/i }))

    expect(screen.getByText(/^# Orchestrator comparison/m)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Orchestrator comparison', level: 3 })).toBeNull()
  })

  it('offers no download until the report is approved', async () => {
    // Export is gated server-side too; the UI must not imply otherwise.
    stubReports()
    renderWithQuery(<ReportPanel taskId="t1" status="awaiting_approval" />)
    await screen.findByRole('heading', { name: 'Orchestrator comparison', level: 3 })

    expect(screen.queryByRole('link', { name: /download/i })).not.toBeInTheDocument()
    expect(screen.getByText(/export unlocks after approval/i)).toBeInTheDocument()
  })

  it('offers the download once approved', async () => {
    stubReports()
    renderWithQuery(<ReportPanel taskId="t1" status="approved" />)
    const link = await screen.findByRole('link', { name: /download markdown/i })
    expect(link).toHaveAttribute('href', '/api/tasks/t1/export')
    expect(link).toHaveAttribute('download')
  })

  it('lets a reviewer read a superseded version, and says it is superseded', async () => {
    stubReports()
    renderWithQuery(<ReportPanel taskId="t1" status="awaiting_approval" />)
    await screen.findByRole('heading', { name: 'Orchestrator comparison', level: 3 })

    const versions = screen.getByRole('group', { name: /report version/i })
    expect(versions.querySelectorAll('button')).toHaveLength(2)

    await userEvent.click(screen.getByRole('button', { name: 'v1' }))

    expect(await screen.findByRole('heading', { name: 'First attempt', level: 3 })).toBeInTheDocument()
    expect(screen.getByText(/superseded by version 2/i)).toBeInTheDocument()
  })

  it('shows no version switcher when there is only one version', async () => {
    stubReports({ latest: 1 })
    renderWithQuery(<ReportPanel taskId="t1" status="awaiting_approval" />)
    await screen.findByRole('heading', { name: 'Orchestrator comparison', level: 3 })

    expect(screen.queryByRole('group', { name: /report version/i })).toBeNull()
    expect(screen.getByText('Version 1')).toBeInTheDocument()
  })
})
