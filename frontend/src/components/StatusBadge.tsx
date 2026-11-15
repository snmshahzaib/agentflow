import type { StepStatus, TaskStatus } from '@/types/api'

const TASK_STYLES: Record<TaskStatus, { label: string; className: string }> = {
  queued: { label: 'Queued', className: 'border-line text-ink-muted' },
  running: { label: 'Running', className: 'border-accent/40 text-accent' },
  awaiting_approval: {
    label: 'Awaiting approval',
    className: 'border-caution/50 text-caution',
  },
  approved: { label: 'Approved', className: 'border-positive/50 text-positive' },
  failed: { label: 'Failed', className: 'border-critical/50 text-critical' },
  cancelled: { label: 'Cancelled', className: 'border-line text-ink-muted' },
}

const STEP_STYLES: Record<StepStatus, { label: string; className: string }> = {
  pending: { label: 'Pending', className: 'border-line text-ink-muted' },
  running: { label: 'Running', className: 'border-accent/40 text-accent' },
  succeeded: { label: 'Succeeded', className: 'border-positive/50 text-positive' },
  failed: { label: 'Failed', className: 'border-critical/50 text-critical' },
  skipped: { label: 'Skipped', className: 'border-line text-ink-muted' },
}

export function StatusBadge({ status }: { status: TaskStatus }) {
  const style = TASK_STYLES[status]
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${style.className}`}
    >
      {style.label}
    </span>
  )
}

export function StepStatusBadge({ status }: { status: StepStatus }) {
  const style = STEP_STYLES[status]
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full border px-2 py-0.5 text-xs font-medium ${style.className}`}
    >
      {style.label}
    </span>
  )
}
