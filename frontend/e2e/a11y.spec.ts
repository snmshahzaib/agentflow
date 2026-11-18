/**
 * Accessibility, checked two ways.
 *
 * axe-core catches the mechanical failures -- unlabelled controls, contrast,
 * broken ARIA relationships, invalid heading order. It cannot tell whether
 * the thing is actually operable, so the keyboard tests below drive the
 * primary journey without a mouse.
 */

import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

import { Dashboard, seedApprovedGateTask } from './helpers'

const WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']

async function scan(page: Page) {
  return new AxeBuilder({ page }).withTags(WCAG).analyze()
}

/**
 * Interactive roles rendered without an accessible name.
 *
 * Read from Playwright's ARIA snapshot, which uses the browser's own
 * accessible-name computation. An earlier version of this test looked only at
 * `aria-label` and text content and flagged two textareas that are correctly
 * labelled by a `<label for=…>`; hand-rolling accname is exactly the wrong
 * place to be clever.
 */
async function unnamedControls(page: Page): Promise<string[]> {
  const snapshot = await page.locator('body').ariaSnapshot()
  return snapshot
    .split('\n')
    .filter((line) => /^\s*-\s+(button|link|tab|textbox|checkbox|combobox)\b(?!\s+")/.test(line))
    .map((line) => line.trim())
}

test.describe('accessibility', () => {
  test('the empty dashboard has no WCAG A/AA violations', async ({ page }) => {
    const app = new Dashboard(page)
    await app.open()
    await expect(app.detail.getByText(/no task selected/i)).toBeVisible()

    const { violations } = await scan(page)
    expect(violations.map((v) => `${v.id}: ${v.help}`)).toEqual([])
  })

  for (const view of ['report', 'sources', 'claims', 'workflow'] as const) {
    test(`the ${view} section has no WCAG A/AA violations`, async ({ page }) => {
      const app = new Dashboard(page)
      await app.open()
      const taskId = await seedApprovedGateTask(page)
      await app.open(`/tasks/${taskId}?view=${view}`)
      await expect(app.panel).toBeVisible()

      const { violations } = await scan(page)
      expect(violations.map((v) => `${v.id}: ${v.help}`)).toEqual([])
    })
  }

  test('the whole journey is operable from the keyboard alone', async ({ page }) => {
    const app = new Dashboard(page)
    await app.open()

    await test.step('a skip link is the first stop, and it works', async () => {
      await page.keyboard.press('Tab')
      await expect(page.getByRole('link', { name: /skip to task detail/i })).toBeFocused()
      await page.keyboard.press('Enter')
      await expect(page.locator('#task-detail')).toBeFocused()
    })

    await test.step('a question can be submitted without a mouse', async () => {
      await app.questionField.focus()
      await page.keyboard.type(
        'Compare three open-source workflow orchestration frameworks for a small team.',
      )
      // Tab past the character counter to the submit button.
      await app.submitButton.focus()
      await page.keyboard.press('Enter')
      await app.waitForApprovalGate()
    })

    await test.step('sections can be reached and switched with arrow keys', async () => {
      await page.getByRole('tab', { name: /^Report/ }).focus()
      await page.keyboard.press('ArrowRight')
      await expect(page.getByRole('tab', { name: /^Sources/ })).toBeFocused()
      await expect(page.getByRole('tab', { name: /^Sources/ })).toHaveAttribute(
        'aria-selected',
        'true',
      )
    })

    await test.step('the report can be approved from the keyboard', async () => {
      await app.approveButton.focus()
      await page.keyboard.press('Enter')
      await expect(app.detail.getByText('Approved').first()).toBeVisible({ timeout: 60_000 })
    })
  })

  test('a validation error is announced, not only coloured', async ({ page }) => {
    const app = new Dashboard(page)
    await app.open()

    await app.questionField.fill('short')
    await app.submitButton.click()

    // role=alert is what makes this reach a screen reader at all.
    const alert = page.getByRole('alert')
    await expect(alert).toBeVisible()
    await expect(alert).toContainText(/at least 10 characters/i)
    await expect(app.questionField).toHaveAttribute('aria-invalid', 'true')
  })

  test('every control has an accessible name', async ({ page }) => {
    const app = new Dashboard(page)
    await app.open()
    const taskId = await seedApprovedGateTask(page)
    await app.open(`/tasks/${taskId}`)
    await expect(app.panel).toBeVisible()

    expect(await unnamedControls(page)).toEqual([])
  })
})
