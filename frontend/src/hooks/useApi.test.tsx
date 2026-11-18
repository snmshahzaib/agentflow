import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useSteps } from '@/hooks/useApi'
import { steps } from '@/test/fixtures'
import { withQuery } from '@/test/render'
import type { TaskStatus } from '@/types/api'

interface Props {
  status: TaskStatus
}

afterEach(() => vi.unstubAllGlobals())

function stubSteps(...responses: unknown[][]) {
  const fetchMock = vi.fn()
  for (const body of responses) {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
  }
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('task-scoped queries', () => {
  /**
   * Regression test for a bug a browser test found: a completed task showed
   * an empty workflow timeline, "No sources yet" and "No report yet".
   *
   * Polling stops as soon as a task leaves `running`, so the last poll before
   * the transition was the final word -- and it happened before the steps
   * existed. The status is part of the query key precisely so that arriving
   * at the approval gate is itself a refetch.
   */
  it('refetches when the task status changes', async () => {
    const fetchMock = stubSteps([], steps)

    const { result, rerender } = renderHook(
      ({ status }: Props) => useSteps('t1', status),
      { wrapper: withQuery(), initialProps: { status: 'running' } },
    )

    await waitFor(() => expect(result.current.data).toEqual([]))
    expect(fetchMock).toHaveBeenCalledTimes(1)

    rerender({ status: 'awaiting_approval' })

    await waitFor(() => expect(result.current.data).toHaveLength(steps.length))
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('keeps showing the previous data while the new status loads', async () => {
    // Without keepPreviousData the key change would flash a loading state
    // over content already on screen.
    const fetchMock = stubSteps(steps, steps)

    const { result, rerender } = renderHook(
      ({ status }: Props) => useSteps('t1', status),
      { wrapper: withQuery(), initialProps: { status: 'running' } },
    )
    await waitFor(() => expect(result.current.data).toHaveLength(steps.length))

    rerender({ status: 'awaiting_approval' })

    // Data is still present on the very next render, before the refetch lands.
    expect(result.current.data).toHaveLength(steps.length)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})
