import { useState } from 'react'

import { Card, EmptyState, ErrorState, Skeleton } from '@/components/Primitives'
import { StatusBadge } from '@/components/StatusBadge'
import { TabPanel, Tabs } from '@/components/Tabs'
import { ApprovalPanel } from '@/features/tasks/ApprovalPanel'
import { ClaimsTable } from '@/features/tasks/ClaimsTable'
import { ReportPanel } from '@/features/tasks/ReportPanel'
import { SourcesPanel } from '@/features/tasks/SourcesPanel'
import { StageProgress } from '@/features/tasks/StageProgress'
import { WorkflowTimeline } from '@/features/tasks/WorkflowTimeline'
import type { View } from '@/features/tasks/views'
import { useClaims, useReport, useSources, useSteps, useTask } from '@/hooks/useApi'
import { ApiError } from '@/services/api'

/**
 * One task, in detail.
 *
 * Tabbed rather than a single column of panels. The reviewer's job is to read
 * a report and decide, and the evidence behind it is deep -- sources,
 * per-claim verdicts, a stage timeline with retries. Stacking all of it made
 * the primary task a scrolling exercise, and on a phone the approval controls
 * ended up several screens below the report they apply to.
 *
 * The approval gate sits outside the tabs: it is the reason the page exists,
 * and it must not be something a reviewer has to go and find.
 */
export function TaskDetail({
  taskId,
  view,
  onViewChange,
}: {
  taskId: string | null
  view: View
  onViewChange: (view: View) => void
}) {
  const [readingVersion, setReadingVersion] = useState<number | undefined>(undefined)

  const task = useTask(taskId ?? undefined)
  const status = task.data?.status
  const steps = useSteps(taskId ?? undefined, status)
  const sources = useSources(taskId ?? undefined, status)
  const claims = useClaims(taskId ?? undefined, status, readingVersion)
  const report = useReport(taskId ?? undefined, status)

  if (!taskId) {
    return (
      <EmptyState
        title="No task selected"
        body="Choose a task from the list, or submit a question to start one. Each task keeps its own workflow timeline, sources and report versions."
      />
    )
  }

  if (task.isPending) {
    return (
      <Card>
        <Skeleton lines={8} label="Loading task…" />
      </Card>
    )
  }

  if (task.isError) {
    const error = task.error instanceof ApiError ? task.error : null
    return (
      <ErrorState
        title="Could not load this task"
        message={error?.message ?? 'Unknown error.'}
        correlationId={error?.correlationId}
        onRetry={() => void task.refetch()}
      />
    )
  }

  const detail = task.data
  const sourceCount = sources.data?.length ?? 0
  const claimCount = claims.data?.length ?? 0
  const unsupported = claims.data?.filter((c) => c.support_status === 'unsupported').length ?? 0

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <h2 className="min-w-0 flex-1 text-base font-semibold">{detail.question}</h2>
          <StatusBadge status={detail.status} />
        </div>

        <div className="mt-4">
          <StageProgress steps={steps.data ?? []} status={detail.status} />
        </div>

        <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
          <Fact term="Model provider" value={detail.llm_provider} />
          <Fact term="Search" value={detail.search_mode} />
          <Fact
            term="Model calls"
            value={`${detail.calls_used}/${detail.max_calls_budget}`}
          />
          <Fact
            term="Tokens"
            value={`${detail.tokens_used.toLocaleString()}/${detail.max_tokens_budget.toLocaleString()}`}
          />
        </dl>

        {detail.error_message ? (
          <div className="mt-4">
            <ErrorState
              title={`Task failed (${detail.error_code ?? 'unknown'})`}
              message={detail.error_message}
            />
          </div>
        ) : null}
      </Card>

      <Card>
        <h3 className="text-sm font-semibold">Approval gate</h3>
        <div className="mt-2">
          <ApprovalPanel task={detail} currentVersion={report.data?.version} />
        </div>
      </Card>

      <Card className="pt-3">
        <Tabs
          label="Task detail sections"
          active={view}
          onChange={(id) => onViewChange(id as View)}
          tabs={[
            { id: 'report', label: 'Report' },
            { id: 'sources', label: 'Sources', badge: sourceCount || undefined },
            {
              id: 'claims',
              label: 'Claims',
              badge: claimCount ? `${claimCount}${unsupported ? ` · ${unsupported}!` : ''}` : undefined,
            },
            { id: 'workflow', label: 'Workflow' },
          ]}
        />

        <div className="mt-4">
          <TabPanel id="report" active={view}>
            <ReportPanel
              taskId={detail.id}
              status={detail.status}
              onVersionChange={setReadingVersion}
            />
          </TabPanel>

          <TabPanel id="sources" active={view}>
            <SourcesPanel taskId={detail.id} status={detail.status} />
          </TabPanel>

          <TabPanel id="claims" active={view}>
            <ClaimsTable taskId={detail.id} status={detail.status} version={readingVersion} />
          </TabPanel>

          <TabPanel id="workflow" active={view}>
            <WorkflowTimeline steps={steps.data ?? []} status={detail.status} />
          </TabPanel>
        </div>
      </Card>
    </div>
  )
}

function Fact({ term, value }: { term: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-ink-muted">{term}</dt>
      <dd className="font-mono text-xs">{value}</dd>
    </div>
  )
}
