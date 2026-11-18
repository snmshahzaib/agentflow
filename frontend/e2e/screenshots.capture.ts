/**
 * Captures the screenshots used in the README and docs.
 *
 * Not a test -- it writes files -- but it runs through the same harness as
 * the suite, against the same real stack in its offline profile. That matters
 * for honesty as much as for convenience: every screenshot is of the actual
 * application, showing real rows written by a real worker, and each one
 * includes the "Offline demo mode" banner, so a reader can see at a glance
 * that the content came from the fixture corpus and the stub adapter rather
 * than from a live model.
 *
 *   scripts/capture-screenshots.sh
 */

import { mkdir } from 'node:fs/promises'
import path from 'node:path'

import { expect, test, type Page } from '@playwright/test'

import { Dashboard, seedApprovedGateTask } from './helpers'

const OUT = path.resolve('../docs/screenshots')

const QUESTION =
  'Compare three open-source workflow orchestration frameworks for a small engineering team.'

async function shot(page: Page, name: string): Promise<void> {
  await mkdir(OUT, { recursive: true })
  // Animations are already disabled by the reduced-motion setting in the
  // config; this waits for the network to settle so no skeleton is caught
  // mid-load.
  await page.waitForLoadState('networkidle')
  await page.screenshot({ path: path.join(OUT, `${name}.png`), animations: 'disabled' })
}

test.describe.configure({ mode: 'serial' })

test('capture the documentation screenshots', async ({ page }) => {
  const app = new Dashboard(page)
  await app.open()

  await test.step('empty dashboard', async () => {
    await expect(app.taskList.getByText(/no research yet/i)).toBeVisible()
    await shot(page, '01-empty-dashboard')
  })

  const taskId = await seedApprovedGateTask(page, QUESTION)

  await test.step('report at the approval gate', async () => {
    await app.open(`/tasks/${taskId}?view=report`)
    await expect(app.panel.getByRole('table')).toBeVisible()
    await shot(page, '02-report')
  })

  await test.step('claim support', async () => {
    await app.open(`/tasks/${taskId}?view=claims`)
    await expect(app.panel.getByRole('list', { name: /claim support summary/i })).toBeVisible()
    await shot(page, '03-claim-support')
  })

  await test.step('retrieved sources', async () => {
    await app.open(`/tasks/${taskId}?view=sources`)
    await expect(app.panel.getByRole('listitem').first()).toBeVisible()
    await shot(page, '04-sources')
  })

  await test.step('workflow timeline', async () => {
    await app.open(`/tasks/${taskId}?view=workflow`)
    await expect(app.panel.getByRole('listitem').first()).toBeVisible()
    await shot(page, '05-workflow')
  })

  await test.step('the Markdown that will be exported', async () => {
    await app.open(`/tasks/${taskId}?view=report`)
    await app.panel.getByRole('button', { name: /show markdown source/i }).click()
    await expect(app.panel.getByText(/^## Limitations$/m)).toBeVisible()
    await shot(page, '06-markdown-source')
  })

  await test.step('after a revision, two versions', async () => {
    await app.open(`/tasks/${taskId}?view=report`)
    await app.reviewerNote.fill('Say more about operational burden for a small team.')
    await app.revisionButton.click()
    await app.waitForApprovalGate()
    await expect(app.panel.getByRole('group', { name: /report version/i })).toBeVisible({
      timeout: 60_000,
    })
    await shot(page, '07-revision-versions')
  })

  await test.step('approved, export unlocked', async () => {
    await app.approveButton.click()
    await expect(app.exportLink).toBeVisible({ timeout: 60_000 })
    await shot(page, '08-approved-export')
  })

  await test.step('phone layout', async () => {
    await page.setViewportSize({ width: 390, height: 844 })
    await app.open(`/tasks/${taskId}?view=report`)
    await expect(app.panel).toBeVisible()
    await shot(page, '09-phone-report')
  })
})
