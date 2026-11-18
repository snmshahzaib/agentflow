import { Navigate, Route, Routes, useNavigate, useParams, useSearchParams } from 'react-router'

import { Card, ErrorState, Skeleton } from '@/components/Primitives'
import { ProvenanceBanner } from '@/components/ProvenanceBanner'
import { TaskDetail } from '@/features/tasks/TaskDetail'
import { isView } from '@/features/tasks/views'
import { TaskCreateForm } from '@/features/tasks/TaskCreateForm'
import { TaskList } from '@/features/tasks/TaskList'
import { useMeta } from '@/hooks/useApi'
import { ApiError } from '@/services/api'

/**
 * The task id lives in the path and the open section in the query string, so
 * a reviewer can link a colleague to exactly what they are looking at, the
 * back button works, and a reload does not lose the selection. Before this,
 * selection was component state and a refresh dropped it.
 */
export function App() {
  return (
    <Routes>
      <Route path="/" element={<Dashboard />} />
      <Route path="/tasks/:taskId" element={<Dashboard />} />
      {/* Anything else is a mistyped link, not an error worth a page. */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

function Dashboard() {
  const { taskId } = useParams<{ taskId: string }>()
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const meta = useMeta()

  const viewParam = params.get('view')
  const view = isView(viewParam) ? viewParam : 'report'

  return (
    <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:py-10">
      <a
        href="#task-detail"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-accent focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:text-accent-ink"
      >
        Skip to task detail
      </a>

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
          Human-in-the-loop research orchestration. Specialised stages plan a question, retrieve
          evidence, check every claim against its sources, and pause for your approval before a
          report can be exported.
        </p>
      </header>

      <div className="mt-5">
        {meta.isPending ? <Skeleton lines={2} label="Checking runtime configuration…" /> : null}
        {meta.isError ? (
          <ErrorState
            title="Cannot reach the API"
            message={meta.error instanceof ApiError ? meta.error.message : 'Unknown error.'}
            correlationId={meta.error instanceof ApiError ? meta.error.correlationId : null}
            onRetry={() => void meta.refetch()}
          />
        ) : null}
        {meta.data ? <ProvenanceBanner meta={meta.data} /> : null}
      </div>

      {/* grid-cols-1 is load-bearing, not redundant. Without it the implicit
          single column is auto-sized, so a long unbroken URL inside a card
          sets the column's min-content width and the whole page scrolls
          sideways by a few pixels on a phone. The lg template already clamps
          with minmax(0,…) for the same reason. */}
      <main className="mt-5 grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,21rem)_minmax(0,1fr)]">
        <div className="space-y-4">
          <TaskCreateForm onCreated={(id) => void navigate(`/tasks/${id}`)} />
          <section aria-label="Your research tasks">
            <h2 className="px-1 pb-2 text-sm font-semibold">Your research</h2>
            <TaskList
              selectedId={taskId ?? null}
              onSelect={(id) => void navigate(`/tasks/${id}`)}
            />
          </section>
        </div>

        <section id="task-detail" aria-label="Task detail" tabIndex={-1}>
          <TaskDetail
            taskId={taskId ?? null}
            view={view}
            onViewChange={(next) => {
              // replace: switching section is not a navigation step a reader
              // wants to walk back through one tab at a time.
              setParams((current) => {
                const updated = new URLSearchParams(current)
                updated.set('view', next)
                return updated
              }, { replace: true })
            }}
          />
        </section>
      </main>

      {meta.data ? (
        <footer className="mt-10">
          <Card className="text-xs text-ink-muted">
            <h2 className="font-medium text-ink">Resolved model configuration</h2>
            <ul className="mt-2 grid gap-1 sm:grid-cols-2">
              {meta.data.models.map((model) => (
                <li key={model.node} className="font-mono">
                  {model.node} → {model.model}
                </li>
              ))}
            </ul>
            <p className="mt-3">
              Catalogue: {meta.data.catalogue_source} ({meta.data.catalogue_size} models) · strict
              schema output: {String(meta.data.require_structured_output)}
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
