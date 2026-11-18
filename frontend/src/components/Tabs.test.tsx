import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'

import { TabPanel, Tabs } from '@/components/Tabs'

const TABS = [
  { id: 'report', label: 'Report' },
  { id: 'sources', label: 'Sources', badge: 3 },
  { id: 'workflow', label: 'Workflow' },
]

function Harness() {
  const [active, setActive] = useState('report')
  return (
    <>
      <Tabs tabs={TABS} active={active} onChange={setActive} label="Sections" />
      <TabPanel id="report" active={active}>
        report body
      </TabPanel>
      <TabPanel id="sources" active={active}>
        sources body
      </TabPanel>
      <TabPanel id="workflow" active={active}>
        workflow body
      </TabPanel>
    </>
  )
}

describe('Tabs', () => {
  it('exposes the ARIA tab relationships a screen reader needs', () => {
    render(<Harness />)

    const list = screen.getByRole('tablist', { name: 'Sections' })
    expect(list).toBeInTheDocument()

    const selected = screen.getByRole('tab', { selected: true })
    expect(selected).toHaveAccessibleName(/report/i)

    const panel = screen.getByRole('tabpanel')
    expect(panel).toHaveTextContent('report body')
    // The panel must name the tab that controls it, and vice versa.
    expect(selected).toHaveAttribute('aria-controls', panel.id)
    expect(panel).toHaveAttribute('aria-labelledby', selected.id)
  })

  it('shows only the active panel', async () => {
    render(<Harness />)
    expect(screen.getByText('report body')).toBeInTheDocument()
    expect(screen.queryByText('sources body')).toBeNull()

    await userEvent.click(screen.getByRole('tab', { name: /sources/i }))

    expect(screen.getByText('sources body')).toBeInTheDocument()
    expect(screen.queryByText('report body')).toBeNull()
  })

  it('keeps only the selected tab in the tab order', async () => {
    // A roving tabindex: pressing Tab moves past the whole list rather than
    // stepping through every tab in it.
    render(<Harness />)
    expect(screen.getByRole('tab', { name: /report/i })).toHaveAttribute('tabindex', '0')
    expect(screen.getByRole('tab', { name: /sources/i })).toHaveAttribute('tabindex', '-1')

    await userEvent.click(screen.getByRole('tab', { name: /sources/i }))

    expect(screen.getByRole('tab', { name: /sources/i })).toHaveAttribute('tabindex', '0')
    expect(screen.getByRole('tab', { name: /report/i })).toHaveAttribute('tabindex', '-1')
  })

  it('moves between tabs with the arrow, Home and End keys', async () => {
    render(<Harness />)
    await userEvent.click(screen.getByRole('tab', { name: /report/i }))

    await userEvent.keyboard('{ArrowRight}')
    expect(screen.getByRole('tab', { selected: true })).toHaveAccessibleName(/sources/i)

    await userEvent.keyboard('{End}')
    expect(screen.getByRole('tab', { selected: true })).toHaveAccessibleName(/workflow/i)

    // Wraps, rather than dead-ending at the last tab.
    await userEvent.keyboard('{ArrowRight}')
    expect(screen.getByRole('tab', { selected: true })).toHaveAccessibleName(/report/i)

    await userEvent.keyboard('{ArrowLeft}')
    expect(screen.getByRole('tab', { selected: true })).toHaveAccessibleName(/workflow/i)

    await userEvent.keyboard('{Home}')
    expect(screen.getByRole('tab', { selected: true })).toHaveAccessibleName(/report/i)
  })

  it('keeps focus with the selection when moving by keyboard', async () => {
    render(<Harness />)
    await userEvent.click(screen.getByRole('tab', { name: /report/i }))
    await userEvent.keyboard('{ArrowRight}')
    expect(screen.getByRole('tab', { name: /sources/i })).toHaveFocus()
  })

  it('does not fold a count into the tab name', () => {
    // "Sources 3" as an accessible name would be read as part of the label.
    render(<Harness />)
    const sources = screen.getByRole('tab', { name: /sources/i })
    expect(sources).toHaveTextContent('3')
  })
})
