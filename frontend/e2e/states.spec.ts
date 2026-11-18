/**
 * Loading, empty, error and retry states.
 *
 * These are the only specs that intercept the network, and only because the
 * real backend will not readily produce what they need: a dead API, a 429, a
 * 500. Each interception is narrow and the reason is stated at the point of
 * use.
 */

import { expect, test } from '@playwright/test'

import { Dashboard, DEMO_QUESTION, mockApi, seedApprovedGateTask } from './helpers'

test.describe('loading and empty states', () => {
  test('a slow API shows what is being loaded, not just that something is', async ({ page }) => {
    // Held open so the loading state is observable rather than a flash.
    await page.route('**/api/meta', async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 1500))
      await route.continue()
    })

    await page.goto('/')
    await expect(page.getByLabel(/checking runtime configuration/i)).toBeVisible()
    await expect(page.getByText(/offline demo mode/i)).toBeVisible({ timeout: 10_000 })
  })

  test('an empty session explains what to do, and offers the action', async ({ page }) => {
    const app = new Dashboard(page)
    await app.open()

    await expect(app.taskList.getByText(/no research yet/i)).toBeVisible()
    await expect(app.taskList.getByText(/submit a question above/i)).toBeVisible()
    await expect(app.detail.getByText(/no task selected/i)).toBeVisible()
    await expect(app.submitButton).toBeEnabled()
  })

  test('sections that have no data yet say why, not just that they are empty', async ({ page }) => {
    const app = new Dashboard(page)
    await app.open()

    // A fresh task has no report; the panel must not look broken.
    const created = await page.request.post('/api/tasks', { data: { question: DEMO_QUESTION } })
    const { id } = (await created.json()) as { id: string }
    await app.open(`/tasks/${id}?view=report`)

    await expect(
      app.panel.getByText(/Nothing is hidden — it has not been written/i),
    ).toBeVisible()
  })
})

test.describe('error and retry states', () => {
  test('an unreachable API is reported with a retry that works', async ({ page }) => {
    let failing = true
    await page.route('**/api/meta', async (route) => {
      if (failing) await route.abort('connectionrefused')
      else await route.continue()
    })

    await page.goto('/')
    const alert = page.getByRole('alert')
    await expect(alert).toContainText(/cannot reach the api/i)
    await expect(alert).toContainText(/is the backend running/i)

    // The retry is a real action, not a decorative button.
    failing = false
    await page.getByRole('button', { name: /try again/i }).click()
    await expect(page.getByText(/offline demo mode/i)).toBeVisible()
  })

  test('a server error shows the correlation id a user can quote', async ({ page }) => {
    const app = new Dashboard(page)
    await app.open()
    const taskId = await seedApprovedGateTask(page)

    await mockApi(page, `**/api/tasks/${taskId}`, {
      status: 500,
      json: {
        error: {
          code: 'internal_error',
          message: 'An unexpected error occurred.',
          details: {},
          correlation_id: 'corr-12345',
        },
      },
    })
    await app.open(`/tasks/${taskId}`)

    const alert = page.getByRole('alert')
    await expect(alert).toContainText(/could not load this task/i)
    await expect(alert).toContainText('corr-12345')
  })

  test('a rate-limited submission explains itself instead of failing silently', async ({ page }) => {
    const app = new Dashboard(page)
    await app.open()

    // The limiter is real and tested server-side; 429 is forced here so the
    // UI path is exercised without driving 20 real submissions.
    await mockApi(page, '**/api/tasks', {
      status: 429,
      headers: { 'Retry-After': '28' },
      json: {
        error: {
          code: 'rate_limited',
          message: 'Too many requests. Try again in 28s.',
          details: { limit: 20, window_seconds: 60, retry_after_seconds: 28 },
          correlation_id: 'corr-429',
        },
      },
    })

    await app.questionField.fill(DEMO_QUESTION)
    await app.submitButton.click()

    const alert = page.getByRole('alert')
    await expect(alert).toContainText(/could not start the task/i)
    await expect(alert).toContainText(/try again in 28s/i)
  })

  test('a failed task shows its error code rather than an empty report', async ({ page }) => {
    const app = new Dashboard(page)
    await app.open()
    const taskId = await seedApprovedGateTask(page)

    await mockApi(page, `**/api/tasks/${taskId}`, {
      json: {
        id: taskId,
        question: DEMO_QUESTION,
        status: 'failed',
        current_node: 'research',
        created_at: '2026-11-18T10:00:00Z',
        updated_at: '2026-11-18T10:01:00Z',
        constraints_json: {},
        search_mode: 'fixture',
        llm_provider: 'stub',
        tokens_used: 120,
        calls_used: 1,
        max_tokens_budget: 120000,
        max_calls_budget: 12,
        cost_usd_estimate: 0,
        error_code: 'search_unavailable',
        error_message: 'Retrieval failed for all 3 subtask(s); no evidence was gathered.',
      },
    })
    await app.open(`/tasks/${taskId}`)

    await expect(page.getByText(/task failed \(search_unavailable\)/i)).toBeVisible()
    await expect(page.getByText(/no evidence was gathered/i)).toBeVisible()
    // And no approval controls, because there is nothing to approve.
    await expect(app.approveButton).toHaveCount(0)
    await expect(page.getByText(/failed before reaching the approval gate/i)).toBeVisible()
  })
})
