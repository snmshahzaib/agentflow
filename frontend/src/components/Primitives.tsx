/** Small shared building blocks. Every one is used; none is decorative. */

import type { ReactNode } from 'react'

export function Card({ children, className = '' }: { children: ReactNode; className?: string | undefined }) {
  return (
    <div
      className={`rounded-xl border border-line bg-surface p-5 shadow-sm ${className}`}
    >
      {children}
    </div>
  )
}

export function Spinner({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-ink-muted">
      <span
        aria-hidden="true"
        className="size-3.5 animate-spin rounded-full border-2 border-line border-t-accent"
      />
      <span role="status">{label}</span>
    </span>
  )
}

/**
 * Loading placeholder shaped like the content it replaces.
 *
 * Preferred over a bare spinner for panels: the layout does not jump when
 * data arrives, and the screen says what is coming rather than only that
 * something is happening. The visible bars are decorative; the accessible
 * name carries the message.
 */
export function Skeleton({ lines = 3, label }: { lines?: number; label: string }) {
  return (
    <div role="status" aria-busy="true" aria-label={label}>
      <div aria-hidden="true" className="space-y-2">
        {Array.from({ length: lines }, (_, index) => (
          <div
            key={index}
            className="h-3 animate-pulse rounded bg-surface-muted"
            style={{ width: `${100 - index * 7}%` }}
          />
        ))}
      </div>
    </div>
  )
}

export function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-xl border border-dashed border-line px-6 py-10 text-center">
      <p className="font-medium">{title}</p>
      <p className="mx-auto mt-1 max-w-prose text-sm text-ink-muted">{body}</p>
    </div>
  )
}

export function ErrorState({
  title,
  message,
  correlationId,
  onRetry,
}: {
  title: string
  message: string
  correlationId?: string | null | undefined
  onRetry?: (() => void) | undefined
}) {
  return (
    <div
      role="alert"
      className="rounded-xl border border-critical/40 bg-critical/5 px-5 py-4"
    >
      <p className="font-medium text-critical">{title}</p>
      <p className="mt-1 text-sm text-ink">{message}</p>
      {correlationId ? (
        <p className="mt-2 font-mono text-xs text-ink-muted">
          Correlation ID: {correlationId}
        </p>
      ) : null}
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded-lg border border-line px-3 py-1.5 text-sm font-medium hover:bg-surface-muted"
        >
          Try again
        </button>
      ) : null}
    </div>
  )
}
