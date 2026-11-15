import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ProvenanceBanner } from '@/components/ProvenanceBanner'
import { liveMeta, offlineMeta } from '@/test/fixtures'

describe('ProvenanceBanner', () => {
  it('says plainly that offline output is not from a live model', () => {
    // The project rule this enforces: stub and fixture output is never
    // presented as a live AI response.
    render(<ProvenanceBanner meta={offlineMeta} />)
    expect(screen.getByText(/offline demo mode/i)).toBeInTheDocument()
    expect(screen.getByText(/not from a\s+live model/i)).toBeInTheDocument()
  })

  it('names the provider and search backend in live mode', () => {
    render(<ProvenanceBanner meta={liveMeta} />)
    expect(screen.getByText(/live mode/i)).toBeInTheDocument()
    expect(screen.getByText('openrouter')).toBeInTheDocument()
    expect(screen.getByText('tavily')).toBeInTheDocument()
  })

  it('never claims that claims are verified as true', () => {
    render(<ProvenanceBanner meta={liveMeta} />)
    expect(screen.getByText(/not that it is true/i)).toBeInTheDocument()
  })
})
