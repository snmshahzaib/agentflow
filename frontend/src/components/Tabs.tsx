/**
 * An ARIA tab list with the keyboard behaviour the pattern requires.
 *
 * Built rather than imported: the whole widget is the roving-tabindex rule
 * plus four key handlers, and the alternative was a dependency in the
 * browser bundle for that. Arrow keys move between tabs, Home and End jump
 * to the ends, and only the selected tab is in the tab order, so Tab moves
 * out of the list rather than through it.
 */

import { useRef } from 'react'

export interface TabDefinition {
  id: string
  label: string
  /** Shown after the label, e.g. a count. Not part of the accessible name. */
  badge?: string | number | undefined
}

export function Tabs({
  tabs,
  active,
  onChange,
  label,
}: {
  tabs: TabDefinition[]
  active: string
  onChange: (id: string) => void
  label: string
}) {
  const listRef = useRef<HTMLDivElement>(null)

  function move(delta: number, absolute?: 'first' | 'last') {
    const index = tabs.findIndex((tab) => tab.id === active)
    const next =
      absolute === 'first'
        ? 0
        : absolute === 'last'
          ? tabs.length - 1
          : (index + delta + tabs.length) % tabs.length
    const target = tabs[next]
    if (!target) return
    onChange(target.id)
    // Focus follows selection, which is the automatic-activation variant of
    // the pattern and the right one when switching panels is cheap.
    listRef.current?.querySelector<HTMLButtonElement>(`#tab-${target.id}`)?.focus()
  }

  return (
    <div
      ref={listRef}
      role="tablist"
      aria-label={label}
      className="-mx-1 flex gap-1 overflow-x-auto px-1 pb-1"
      onKeyDown={(event) => {
        if (event.key === 'ArrowRight') move(1)
        else if (event.key === 'ArrowLeft') move(-1)
        else if (event.key === 'Home') move(0, 'first')
        else if (event.key === 'End') move(0, 'last')
        else return
        event.preventDefault()
      }}
    >
      {tabs.map((tab) => {
        const selected = tab.id === active
        return (
          <button
            key={tab.id}
            id={`tab-${tab.id}`}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls={`panel-${tab.id}`}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(tab.id)}
            className={`shrink-0 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
              selected
                ? 'bg-surface-muted text-ink shadow-sm'
                : 'text-ink-muted hover:bg-surface-muted/60 hover:text-ink'
            }`}
          >
            {tab.label}
            {tab.badge !== undefined && tab.badge !== '' ? (
              <span className="ml-1.5 font-mono text-xs text-ink-muted">{tab.badge}</span>
            ) : null}
          </button>
        )
      })}
    </div>
  )
}

export function TabPanel({
  id,
  active,
  children,
}: {
  id: string
  active: string
  children: React.ReactNode
}) {
  if (id !== active) return null
  return (
    <div id={`panel-${id}`} role="tabpanel" aria-labelledby={`tab-${id}`} tabIndex={0}>
      {children}
    </div>
  )
}
