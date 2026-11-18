/**
 * Report content is hostile input.
 *
 * A report quotes retrieved web pages and is written by a model that read
 * them, so both the quoted text and the prose around it are attacker-
 * influenced. These tests pin the three properties that make rendering it
 * safe, because each one is a library default that a future change could
 * silently remove.
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Markdown } from '@/components/Markdown'
import { safeUrl } from '@/lib/urls'

describe('Markdown rendering is not an injection surface', () => {
  it('does not execute or render embedded HTML', () => {
    const hostile = [
      '# Report',
      '',
      '<script>window.__pwned = true</script>',
      '',
      '<img src=x onerror="window.__pwned = true">',
      '',
      '<iframe src="https://evil.test"></iframe>',
    ].join('\n')

    const { container } = render(<Markdown>{hostile}</Markdown>)

    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('iframe')).toBeNull()
    expect(container.querySelector('img')).toBeNull()
    expect((window as unknown as Record<string, unknown>).__pwned).toBeUndefined()
    // The text is still shown, so a reader can see what the report contained.
    expect(screen.getByText(/window.__pwned = true/)).toBeInTheDocument()
  })

  it('refuses to make a javascript: link clickable', () => {
    const hostile = '[Click here](javascript:window.__pwned=true)'

    render(<Markdown>{hostile}</Markdown>)

    expect(screen.queryByRole('link')).toBeNull()
    // The link text survives as plain text: dropping it would hide that the
    // report tried to say something here.
    expect(screen.getByText('Click here')).toBeInTheDocument()
  })

  it('refuses data: URLs, which can smuggle a document into a new tab', () => {
    render(<Markdown>{'[Open](data:text/html,<script>alert(1)</script>)'}</Markdown>)
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('opens legitimate links without handing over a window reference', () => {
    render(<Markdown>{'[A source](https://example.test/page)'}</Markdown>)

    const link = screen.getByRole('link', { name: 'A source' })
    expect(link).toHaveAttribute('href', 'https://example.test/page')
    expect(link).toHaveAttribute('target', '_blank')
    // Without noopener the opened page can reach back through window.opener.
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('nests report headings under the page outline instead of emitting h1', () => {
    // A report is a document inside a page that already has an h1. Emitting
    // its own h1 produced an invalid outline and nine h2s in one panel.
    render(<Markdown>{'# Title\n\n## Section\n\n### Subsection'}</Markdown>)

    expect(screen.getByRole('heading', { name: 'Title', level: 3 })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Section', level: 4 })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Subsection', level: 5 })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { level: 1 })).toBeNull()
  })

  it('does not shift past h6', () => {
    render(<Markdown headingOffset={4}>{'### Deep'}</Markdown>)
    expect(screen.getByRole('heading', { name: 'Deep', level: 6 })).toBeInTheDocument()
  })

  it('renders the tables and blockquotes real reports contain', () => {
    const report = [
      '> **Live web research — retrieved, not verified.**',
      '',
      '| # | Claim | Support |',
      '|---|---|---|',
      '| 1 | A claim | supported |',
    ].join('\n')

    render(<Markdown>{report}</Markdown>)

    expect(screen.getByText(/retrieved, not verified/)).toBeInTheDocument()
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'A claim' })).toBeInTheDocument()
  })

  it('puts wide tables in their own scroll container', () => {
    // Otherwise the page itself scrolls sideways on a phone.
    const { container } = render(
      <Markdown>{'| a | b |\n|---|---|\n| 1 | 2 |'}</Markdown>,
    )
    const scroller = container.querySelector('.md-table-scroll')
    expect(scroller).not.toBeNull()
    expect(scroller?.querySelector('table')).not.toBeNull()
  })
})

describe('safeUrl', () => {
  it.each([
    ['https://example.test/x', 'https://example.test/x'],
    ['http://example.test', 'http://example.test'],
    ['mailto:someone@example.test', 'mailto:someone@example.test'],
    ['/relative/path', '/relative/path'],
  ])('allows %s', (input, expected) => {
    expect(safeUrl(input)).toBe(expected)
  })

  it.each([
    'javascript:alert(1)',
    'JaVaScRiPt:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    'vbscript:msgbox(1)',
    'file:///etc/passwd',
  ])('rejects %s', (input) => {
    expect(safeUrl(input)).toBe('')
  })
})
