import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApprovalPanel } from '@/features/tasks/ApprovalPanel'
import { awaitingTask } from '@/test/fixtures'
import { renderWithQuery } from '@/test/render'
import type { TaskDetail } from '@/types/api'

afterEach(() => vi.unstubAllGlobals())

function stubOk() {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ task_id: awaitingTask.id, status: 'approved', report_version: 1 }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function render(task: TaskDetail = awaitingTask, version: number | undefined = 1) {
  return renderWithQuery(<ApprovalPanel task={task} currentVersion={version} />)
}

describe('ApprovalPanel', () => {
  it('states what each action will do', () => {
    // The one place a human is required: the consequences belong on screen,
    // not in documentation.
    render()
    expect(screen.getByText(/unlocks the Markdown export/i)).toBeInTheDocument()
    expect(screen.getByText(/produces version 2/i)).toBeInTheDocument()
    expect(screen.getByText(/may retrieve different sources/i)).toBeInTheDocument()
  })

  it('approves with an optional note', async () => {
    const fetchMock = stubOk()
    render()

    await userEvent.click(screen.getByRole('button', { name: /approve report/i }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    const [url, init] = fetchMock.mock.calls[0] as [string, { body: string }]
    expect(url).toBe(`/api/tasks/${awaitingTask.id}/approve`)
    expect(JSON.parse(init.body)).toEqual({})
  })

  it('refuses a revision with no note, and says why', async () => {
    const fetchMock = stubOk()
    render()

    await userEvent.click(screen.getByRole('button', { name: /request revision/i }))

    expect(screen.getByRole('alert')).toHaveTextContent(/explaining what should change/i)
    // Nothing was sent: the client refused rather than relying on a 422.
    expect(fetchMock).not.toHaveBeenCalled()
    // And focus is moved to the field that needs filling in.
    expect(screen.getByLabelText(/reviewer note/i)).toHaveFocus()
  })

  it('sends the note with a revision request', async () => {
    const fetchMock = stubOk()
    render()

    await userEvent.type(screen.getByLabelText(/reviewer note/i), 'Cover licensing.')
    await userEvent.click(screen.getByRole('button', { name: /request revision/i }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    const [url, init] = fetchMock.mock.calls[0] as [string, { body: string }]
    expect(url).toBe(`/api/tasks/${awaitingTask.id}/request-revision`)
    expect(JSON.parse(init.body)).toEqual({ feedback: 'Cover licensing.' })
  })

  it('surfaces a rejected approval with its correlation id', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: {
              code: 'conflict',
              message: 'The task is not awaiting approval.',
              details: {},
              correlation_id: 'abc123',
            },
          }),
          { status: 409, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    )
    render()

    await userEvent.click(screen.getByRole('button', { name: /approve report/i }))

    expect(await screen.findByText(/could not approve/i)).toBeInTheDocument()
    expect(screen.getByText(/not awaiting approval/i)).toBeInTheDocument()
    expect(screen.getByText(/abc123/)).toBeInTheDocument()
  })

  it('offers no controls when the task is not at the gate', () => {
    render({ ...awaitingTask, status: 'running' })
    expect(screen.queryByRole('button', { name: /approve/i })).toBeNull()
    expect(screen.getByText(/has not reached the approval gate/i)).toBeInTheDocument()
  })

  it('explains a failed task rather than offering approval', () => {
    render({ ...awaitingTask, status: 'failed' })
    expect(screen.queryByRole('button', { name: /approve/i })).toBeNull()
    expect(screen.getByText(/failed before reaching the approval gate/i)).toBeInTheDocument()
  })

  it('points an approved task at its export', () => {
    render({ ...awaitingTask, status: 'approved' })
    expect(screen.getByText(/can be exported from the Report tab/i)).toBeInTheDocument()
  })
})
