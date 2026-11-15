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
