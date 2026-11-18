/**
 * Typed API client.
 *
 * Every error path produces an ApiError carrying the backend's stable error
 * code and correlation id, so the UI can show something specific and a user
 * can quote an id that appears in server logs.
 */

import type {
  ApiErrorBody,
  ClaimView,
  Meta,
  Page,
  ReportView,
  SourceView,
  StepView,
  TaskDetail,
  TaskSummary,
} from '@/types/api'

const BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? ''

export class ApiError extends Error {
  readonly code: string
  readonly status: number
  readonly correlationId: string | null
  readonly details: Record<string, unknown>

  constructor(status: number, body: ApiErrorBody) {
    super(body.message)
    this.name = 'ApiError'
    this.status = status
    this.code = body.code
    this.correlationId = body.correlation_id
    this.details = body.details
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      // The session cookie identifies whose research this is.
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...(init?.headers ?? {}),
      },
    })
  } catch (cause) {
    throw new ApiError(0, {
      code: 'network_error',
      message: 'Could not reach the AgentFlow API. Is the backend running?',
      details: { cause: String(cause) },
      correlation_id: null,
    })
  }

  if (response.status === 204) return undefined as T

  const text = await response.text()
  const parsed: unknown = text ? JSON.parse(text) : null

  if (!response.ok) {
    const body = (parsed as { error?: ApiErrorBody } | null)?.error
    throw new ApiError(
      response.status,
      body ?? {
        code: 'unexpected_error',
        message: `The API returned ${response.status}.`,
        details: {},
        correlation_id: response.headers.get('X-Correlation-ID'),
      },
    )
  }

  return parsed as T
}

export const api = {
  meta: () => request<Meta>('/api/meta'),

  listTasks: (cursor?: string) =>
    request<Page<TaskSummary>>(`/api/tasks${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`),

  getTask: (id: string) => request<TaskDetail>(`/api/tasks/${id}`),

  getSteps: (id: string) => request<StepView[]>(`/api/tasks/${id}/steps`),

  createTask: (question: string, idempotencyKey: string) =>
    request<TaskDetail>('/api/tasks', {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify({ question }),
    }),

  getSources: (id: string) => request<SourceView[]>(`/api/tasks/${id}/sources`),

  // `version` reads a specific report round. The backend has always accepted
  // it; the UI now uses it so a reviewer can read what an earlier version
  // said rather than only the newest one.
  getClaims: (id: string, version?: number) =>
    request<ClaimView[]>(`/api/tasks/${id}/claims${version ? `?version=${version}` : ''}`),

  getReport: (id: string, version?: number) =>
    request<ReportView>(`/api/tasks/${id}/report${version ? `?version=${version}` : ''}`),

  requestRevision: (id: string, feedback: string) =>
    request<{ task_id: string; status: string; report_version: number }>(
      `/api/tasks/${id}/request-revision`,
      { method: 'POST', body: JSON.stringify({ feedback }) },
    ),

  /**
   * The export URL. Used as a plain link so the browser performs the
   * download with the session cookie attached; the API refuses it with 409
   * until a human has approved the report.
   */
  exportUrl: (id: string) => `${BASE}/api/tasks/${id}/export`,

  approveTask: (id: string, feedback?: string) =>
    request<{ task_id: string; status: string; report_version: number }>(
      `/api/tasks/${id}/approve`,
      { method: 'POST', body: JSON.stringify(feedback ? { feedback } : {}) },
    ),
}
