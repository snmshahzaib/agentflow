import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { TaskCreateForm } from '@/features/tasks/TaskCreateForm'
import { renderWithQuery } from '@/test/render'

afterEach(() => vi.unstubAllGlobals())

function stubFetch(status: number, body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('TaskCreateForm', () => {
  it('rejects a too-short question before making a request', async () => {
    const fetchMock = stubFetch(202, {})
    renderWithQuery(<TaskCreateForm onCreated={() => {}} />)

    await userEvent.type(screen.getByLabelText(/research question/i), 'short')
    await userEvent.click(screen.getByRole('button', { name: /start research/i }))

    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('submits a valid question with an idempotency key', async () => {
    const fetchMock = stubFetch(202, { id: 'task-1' })
    const onCreated = vi.fn()
    renderWithQuery(<TaskCreateForm onCreated={onCreated} />)

    await userEvent.type(
      screen.getByLabelText(/research question/i),
      'Compare three workflow orchestration frameworks.',
    )
    await userEvent.click(screen.getByRole('button', { name: /start research/i }))

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith('task-1'))
    const [, init] = fetchMock.mock.calls[0]
    expect(init.headers['Idempotency-Key']).toBeTruthy()
    expect(init.credentials).toBe('include')
  })

  it('surfaces the API error message and its correlation id', async () => {
    stubFetch(409, {
      error: {
        code: 'demo_budget_exceeded',
        message: 'The daily demo budget has been spent.',
        details: {},
        correlation_id: 'abc123',
      },
    })
    renderWithQuery(<TaskCreateForm onCreated={() => {}} />)

    await userEvent.type(
      screen.getByLabelText(/research question/i),
      'Compare three workflow orchestration frameworks.',
    )
    await userEvent.click(screen.getByRole('button', { name: /start research/i }))

    expect(await screen.findByText(/daily demo budget/i)).toBeInTheDocument()
    expect(screen.getByText(/abc123/)).toBeInTheDocument()
  })
})
