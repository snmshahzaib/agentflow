/**
 * Query hooks.
 *
 * Polling, not server-sent events (ADR-006): at a 1.5s cadence SSE buys
 * nothing perceptible and adds proxy-buffering and reconnection failure
 * modes. Polling stops as soon as a task reaches a terminal status, so an
 * idle tab is not making requests forever.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { QueryClient } from '@tanstack/react-query'

import { api } from '@/services/api'
import { ACTIVE_STATUSES, type TaskStatus } from '@/types/api'

const POLL_INTERVAL_MS = Number(import.meta.env.VITE_POLL_INTERVAL_MS ?? 1500)

export const queryKeys = {
  meta: ['meta'] as const,
  tasks: ['tasks'] as const,
  task: (id: string) => ['task', id] as const,
  steps: (id: string) => ['steps', id] as const,
}

function pollWhileActive(status: TaskStatus | undefined): number | false {
  return status && ACTIVE_STATUSES.includes(status) ? POLL_INTERVAL_MS : false
}

export function useMeta() {
  return useQuery({
    queryKey: queryKeys.meta,
    queryFn: api.meta,
    // Runtime configuration cannot change without a restart.
    staleTime: Infinity,
  })
}

export function useTasks() {
  return useQuery({
    queryKey: queryKeys.tasks,
    queryFn: () => api.listTasks(),
    refetchInterval: (query) =>
      query.state.data?.items.some((t) => ACTIVE_STATUSES.includes(t.status))
        ? POLL_INTERVAL_MS
        : false,
  })
}

export function useTask(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.task(id ?? ''),
    queryFn: () => api.getTask(id!),
    enabled: Boolean(id),
    refetchInterval: (query) => pollWhileActive(query.state.data?.status),
  })
}

export function useSteps(id: string | undefined, status: TaskStatus | undefined) {
  return useQuery({
    queryKey: queryKeys.steps(id ?? ''),
    queryFn: () => api.getSteps(id!),
    enabled: Boolean(id),
    refetchInterval: () => pollWhileActive(status),
  })
}

export function useSources(id: string | undefined, status: TaskStatus | undefined) {
  return useQuery({
    queryKey: ['sources', id ?? ''],
    queryFn: () => api.getSources(id!),
    enabled: Boolean(id),
    refetchInterval: () => pollWhileActive(status),
  })
}

export function useClaims(id: string | undefined, status: TaskStatus | undefined) {
  return useQuery({
    queryKey: ['claims', id ?? ''],
    queryFn: () => api.getClaims(id!),
    enabled: Boolean(id),
    // A missing report is expected before the draft node runs, so a 404 here
    // is a normal state rather than an error worth retrying.
    retry: false,
    refetchInterval: () => pollWhileActive(status),
  })
}

export function useReport(id: string | undefined, status: TaskStatus | undefined) {
  return useQuery({
    queryKey: ['report', id ?? ''],
    queryFn: () => api.getReport(id!),
    enabled: Boolean(id),
    retry: false,
    refetchInterval: () => pollWhileActive(status),
  })
}

export function useRequestRevision(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (feedback: string) => api.requestRevision(id, feedback),
    onSuccess: async () => {
      await invalidateTask(queryClient, id)
    },
  })
}

export function useCreateTask() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (question: string) =>
      // A fresh idempotency key per submission attempt: a retried request
      // after a dropped response must not create a second task.
      api.createTask(question, crypto.randomUUID()),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.tasks }),
  })
}

export function useApproveTask(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (feedback?: string) => api.approveTask(id, feedback),
    onSuccess: async () => {
      await invalidateTask(queryClient, id)
    },
  })
}

/** Everything that changes when a task's state moves. */
async function invalidateTask(queryClient: QueryClient, id: string): Promise<void> {
  await Promise.all(
    [
      queryKeys.task(id),
      queryKeys.steps(id),
      queryKeys.tasks,
      ['sources', id],
      ['claims', id],
      ['report', id],
    ].map((queryKey) => queryClient.invalidateQueries({ queryKey })),
  )
}
