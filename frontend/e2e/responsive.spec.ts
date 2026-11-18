/**
 * Layout on a phone and on a desktop.
 *
 * The assertion that matters most is the absence of horizontal page scroll.
 * A report contains a claim-support table and long URLs, and the easy failure
 * is for one of those to widen the document so the whole page slides
 * sideways. Wide content is supposed to scroll inside its own box.
 */

import { expect, test, type Page } from '@playwright/test'

import { Dashboard, seedApprovedGateTask } from './helpers'

const PHONE = { width: 390, height: 844 }
const TABLET = { width: 834, height: 1112 }
const DESKTOP = { width: 1440, height: 900 }

async function pageScrollsSideways(page: Page): Promise<boolean> {
  return page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  )
}

test.describe('responsive layout', () => {
  for (const [name, viewport] of [
    ['phone', PHONE],
    ['tablet', TABLET],
    ['desktop', DESKTOP],
  ] as const) {
    test(`no horizontal page scroll on ${name}`, async ({ page }) => {
      await page.setViewportSize(viewport)
      const app = new Dashboard(page)
      await app.open()
      const taskId = await seedApprovedGateTask(page)

      for (const view of ['report', 'sources', 'claims', 'workflow'] as const) {
        await app.open(`/tasks/${taskId}?view=${view}`)
        await expect(app.panel).toBeVisible()
        expect(await pageScrollsSideways(page), `${view} on ${name}`).toBe(false)
      }
    })
  }

  test('the report table scrolls in its own container rather than the page', async ({ page }) => {
    await page.setViewportSize(PHONE)
    const app = new Dashboard(page)
    await app.open()
    const taskId = await seedApprovedGateTask(page)
    await app.open(`/tasks/${taskId}?view=report`)
    await expect(app.panel.getByRole('table')).toBeVisible()

    const scroller = app.panel.locator('.md-table-scroll')
    await expect(scroller).toBeVisible()

    // The contract is that overflow is *contained*: the box is a scroll
    // container, and the page does not move. Whether this particular table is
    // wider than the box depends on fixture column widths, so asserting
    // either way about that would be testing the fixture.
    expect(await scroller.evaluate((el) => getComputedStyle(el).overflowX)).toBe('auto')
    expect(await scroller.evaluate((el) => el.clientWidth)).toBeLessThanOrEqual(
      await page.evaluate(() => document.documentElement.clientWidth),
    )
    expect(await pageScrollsSideways(page)).toBe(false)
  })

  test('the approval controls are reachable without hunting on a phone', async ({ page }) => {
    await page.setViewportSize(PHONE)
    const app = new Dashboard(page)
    await app.open()
    const taskId = await seedApprovedGateTask(page)
    await app.open(`/tasks/${taskId}`)

    // The gate sits above the tabbed evidence, not below all of it: it is the
    // reason the page exists.
    const approveBox = await app.approveButton.boundingBox()
    const tabsBox = await page.getByRole('tablist').boundingBox()
    expect(approveBox).not.toBeNull()
    expect(tabsBox).not.toBeNull()
    expect(approveBox!.y).toBeLessThan(tabsBox!.y)
  })

  test('the sidebar and detail stack on a phone and sit side by side on a desktop', async ({
    page,
  }) => {
    const app = new Dashboard(page)

    await page.setViewportSize(PHONE)
    await app.open()
    const form = await app.questionField.boundingBox()
    const detail = await page.locator('#task-detail').boundingBox()
    expect(detail!.y).toBeGreaterThan(form!.y)

    await page.setViewportSize(DESKTOP)
    await app.open()
    const wideForm = await app.questionField.boundingBox()
    const wideDetail = await page.locator('#task-detail').boundingBox()
    expect(wideDetail!.x).toBeGreaterThan(wideForm!.x + wideForm!.width - 1)
  })
})
