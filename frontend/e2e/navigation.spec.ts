/**
 * Addressability.
 *
 * A reviewer needs to be able to send a colleague a link to exactly what they
 * are looking at, and to survive a page refresh. Before M5 the selected task
 * was component state: a reload dropped it and there was nothing to copy.
 */

import { expect, test } from '@playwright/test'

import { Dashboard, DEMO_QUESTION, seedApprovedGateTask } from './helpers'

test.describe('navigation', () => {
  test('a task has its own URL that survives a reload', async ({ page }) => {
    const app = new Dashboard(page)
    await app.open()
    const taskId = await seedApprovedGateTask(page)

    await app.open(`/tasks/${taskId}`)
    await expect(app.detail.getByRole('heading', { name: DEMO_QUESTION })).toBeVisible()

    await page.reload()
    await expect(app.detail.getByRole('heading', { name: DEMO_QUESTION })).toBeVisible()
    expect(page.url()).toContain(`/tasks/${taskId}`)
  })

  test('the open section is part of the URL', async ({ page }) => {
    const app = new Dashboard(page)
    await app.open()
    const taskId = await seedApprovedGateTask(page)

    await app.open(`/tasks/${taskId}?view=claims`)
    await expect(page.getByRole('tab', { name: /^Claims/ })).toHaveAttribute(
      'aria-selected',
      'true',
    )

    await app.openTab('Workflow')
    await expect(page).toHaveURL(/view=workflow/)

    await page.reload()
    await expect(page.getByRole('tab', { name: /^Workflow/ })).toHaveAttribute(
      'aria-selected',
      'true',
    )
  })

  test('selecting a task from the list navigates, and back returns', async ({ page }) => {
    const app = new Dashboard(page)
    await app.open()
    await seedApprovedGateTask(page)
    await app.open('/')

    await expect(app.detail.getByText(/no task selected/i)).toBeVisible()
    await app.taskList.getByRole('button').first().click()
    await expect(page).toHaveURL(/\/tasks\//)

    await page.goBack()
    await expect(app.detail.getByText(/no task selected/i)).toBeVisible()
  })

  test('an unknown path returns to the dashboard rather than breaking', async ({ page }) => {
    await page.goto('/does/not/exist')
    await expect(page.getByRole('heading', { name: 'AgentFlow', level: 1 })).toBeVisible()
    await expect(page).toHaveURL(/\/$/)
  })

  test('an invalid view parameter falls back instead of rendering nothing', async ({ page }) => {
    const app = new Dashboard(page)
    await app.open()
    const taskId = await seedApprovedGateTask(page)

    await app.open(`/tasks/${taskId}?view=not-a-view`)
    await expect(page.getByRole('tab', { name: /^Report/ })).toHaveAttribute(
      'aria-selected',
      'true',
    )
  })
})
