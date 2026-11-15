import { useState } from 'react'

import { Card, ErrorState, Spinner } from '@/components/Primitives'
import { ProvenanceBanner } from '@/components/ProvenanceBanner'
import { TaskCreateForm } from '@/features/tasks/TaskCreateForm'
import { TaskDetail } from '@/features/tasks/TaskDetail'
import { TaskList } from '@/features/tasks/TaskList'
import { useMeta } from '@/hooks/useApi'
import { ApiError } from '@/services/api'

export function App() {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const meta = useMeta()

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:py-12">
      <header>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h1 className="text-xl font-semibold tracking-tight">AgentFlow</h1>
          {meta.data ? (
            <p className="font-mono text-xs text-ink-muted">
              v{meta.data.version} · {meta.data.app_env}
            </p>
          ) : null}
        </div>
        <p className="mt-1 max-w-2xl text-sm text-ink-muted">
          Human-in-the-loop research orchestration. Specialised nodes plan a question, retrieve
          evidence, check every claim against its sources, and pause for your approval before a
          report can be exported.
        </p>
      </header>

      <div className="mt-6">
        {meta.isPending ? <Spinner label="Checking runtime configuration…" /> : null}
        {meta.isError ? (
          <ErrorState
            title="Cannot reach the API"
            message={
              meta.error instanceof ApiError ? meta.error.message : 'Unknown error.'
            }
            onRetry={() => void meta.refetch()}
          />
        ) : null}
        {meta.data ? <ProvenanceBanner meta={meta.data} /> : null}
      </div>

      <main className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
        <div className="space-y-4">
          <TaskCreateForm onCreated={setSelectedId} />
          <section aria-label="Your research tasks">
            <h2 className="px-1 pb-2 text-sm font-semibold">Your research</h2>
            <TaskList selectedId={selectedId} onSelect={setSelectedId} />
          </section>
        </div>

        <section aria-label="Task detail">
          <TaskDetail taskId={selectedId} />
        </section>
      </main>

      {meta.data ? (
        <footer className="mt-10">
          <Card className="text-xs text-ink-muted">
            <p className="font-medium text-ink">Resolved model configuration</p>
            <ul className="mt-2 grid gap-1 sm:grid-cols-2">
              {meta.data.models.map((model) => (
                <li key={model.node} className="font-mono">
                  {model.node} → {model.model}
                </li>
              ))}
            </ul>
            <p className="mt-3">
              Catalogue: {meta.data.catalogue_source} ({meta.data.catalogue_size} models) ·
              strict schema output: {String(meta.data.require_structured_output)}
              {meta.data.fallback_models.length > 0
                ? ` · fallbacks: ${meta.data.fallback_models.join(', ')}`
                : ''}
            </p>
          </Card>
        </footer>
      ) : null}
    </div>
  )
}
