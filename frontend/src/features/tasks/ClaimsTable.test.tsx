import { screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ClaimsTable } from '@/features/tasks/ClaimsTable'
import { renderWithQuery } from '@/test/render'

afterEach(() => vi.unstubAllGlobals())

function stubClaims(claims: unknown[]) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify(claims), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
  )
}

const supported = {
  id: '1',
  ordinal: 0,
  claim_text: 'A claim a retrieved source states.',
  support_status: 'supported',
  rationale: 'Stated by S1.',
  source_ids: ['s1'],
  source_labels: ['S1'],
}

const unsupported = {
  id: '2',
  ordinal: 1,
  claim_text: 'A claim nothing retrieved states.',
  support_status: 'unsupported',
  rationale: 'No source states this.',
  source_ids: [],
  source_labels: [],
}

describe('ClaimsTable', () => {
  it('shows each claim with its support verdict and cited labels', async () => {
    stubClaims([supported, unsupported])
    renderWithQuery(<ClaimsTable taskId="t1" status="awaiting_approval" />)

    expect(await screen.findByText(/a claim a retrieved source states/i)).toBeInTheDocument()
    expect(screen.getByText('Supported')).toBeInTheDocument()
    expect(screen.getByText('Unsupported')).toBeInTheDocument()
    expect(screen.getByText(/sources: s1/i)).toBeInTheDocument()
  })

  it('says an unsupported claim has no supporting source', async () => {
    stubClaims([unsupported])
    renderWithQuery(<ClaimsTable taskId="t1" status="awaiting_approval" />)
    expect(await screen.findByText(/no supporting source/i)).toBeInTheDocument()
  })

  it('counts unsupported claims so they cannot be skimmed past', async () => {
    stubClaims([supported, unsupported])
    renderWithQuery(<ClaimsTable taskId="t1" status="awaiting_approval" />)
    expect(
      await screen.findByText(/1 claim could not be traced to any retrieved source/i),
    ).toBeInTheDocument()
  })

  it('states that support is not a truth judgement', async () => {
    // The project's honesty rule, asserted so the wording cannot drift.
    stubClaims([supported])
    renderWithQuery(<ClaimsTable taskId="t1" status="awaiting_approval" />)
    expect(await screen.findByText(/not that the claim is true/i)).toBeInTheDocument()
  })
})
