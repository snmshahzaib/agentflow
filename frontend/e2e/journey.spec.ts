/**
 * The complete reviewer journey, executed for real.
 *
 * Nothing here is mocked. A real API accepts the task, a real arq worker runs
 * five graph nodes, real rows are written, and the browser polls for them. The
 * model is the deterministic stub and retrieval is the committed fixture
 * corpus, which is what makes the run free, repeatable and safe for CI --
 * everything else is the production path.
 */

import { expect, test } from '@playwright/test'

import { Dashboard, DEMO_QUESTION, FIXTURE_HOST } from './helpers'

test.describe('research lifecycle', () => {
  test('a question goes from submission through revision to an exported report', async ({
    page,
  }) => {
    const app = new Dashboard(page)
    await app.open()

    await test.step('a new session starts empty and says so', async () => {
      await expect(app.taskList.getByText(/no research yet/i)).toBeVisible()
      await expect(app.detail.getByText(/no task selected/i)).toBeVisible()
    })

    await test.step('the provenance of what is about to run is stated up front', async () => {
      // A core project rule: fixture output is never presented as live AI.
      await expect(page.getByText(/offline demo mode/i)).toBeVisible()
      await expect(page.getByText(/not from a live model or live web search/i)).toBeVisible()
    })

    await test.step('the question is validated before a round trip', async () => {
      await app.questionField.fill('too short')
      await app.submitButton.click()
      await expect(page.getByRole('alert')).toContainText(/at least 10 characters/i)
      // Nothing was created: the client refused rather than relying on a 422.
      await expect(app.taskList.getByText(/no research yet/i)).toBeVisible()
    })

    await test.step('submitting creates the task and selects it', async () => {
      await app.submit()
      await expect(app.taskList.getByRole('button', { name: new RegExp(DEMO_QUESTION.slice(0, 40), 'i') })).toBeVisible()
    })

    await test.step('every workflow stage is visible and reaches success', async () => {
      await app.waitForApprovalGate()
      for (const stage of ['Plan', 'Retrieve sources', 'Analyse', 'Check citations', 'Draft report']) {
        await expect(app.stage(stage).getByText('Succeeded')).toBeVisible()
      }
    })

    await test.step('retrieved sources are shown and labelled as fixtures', async () => {
      const sources = app.detail.getByRole('listitem').filter({ hasText: FIXTURE_HOST })
      expect(await sources.count()).toBeGreaterThan(0)
      // Exact: the fixture URLs themselves contain the word "fixtures", so a
      // substring match would also hit every source link.
      await expect(sources.first().getByText('Fixture', { exact: true })).toBeVisible()
    })

    await test.step('claim support is shown, and is not described as fact checking', async () => {
      await expect(app.detail.getByText(/confirms a source/i)).toBeVisible()
      await expect(app.detail.getByText(/not that the claim is true/i).first()).toBeVisible()
    })

    await test.step('the report is readable but export is locked before approval', async () => {
      await expect(app.detail.getByText(/^Version 1/)).toBeVisible()
      await expect(app.exportLink).toHaveCount(0)
      await expect(app.detail.getByText(/export unlocks after approval/i)).toBeVisible()
    })

    await test.step('a revision without a note is refused by the client', async () => {
      await app.revisionButton.click()
      await expect(page.getByRole('alert')).toContainText(/note explaining what should change/i)
    })

    await test.step('a revision with a note produces a second version', async () => {
      await app.reviewerNote.fill('Say more about operational burden for a small team.')
      await app.revisionButton.click()
      // The task leaves the gate, re-runs, and comes back with v2.
      await expect(app.detail.getByText(/^Version 2/)).toBeVisible({ timeout: 60_000 })
      await app.waitForApprovalGate()
    })

    await test.step('approving unlocks the export', async () => {
      await app.approveButton.click()
      await expect(app.detail.getByText('Approved').first()).toBeVisible({ timeout: 60_000 })
      await expect(app.exportLink).toBeVisible()
    })

    await test.step('the exported Markdown is the real report, with its limitations', async () => {
      const download = await Promise.all([
        page.waitForEvent('download'),
        app.exportLink.click(),
      ]).then(([event]) => event)

      const stream = await download.createReadStream()
      const chunks: Buffer[] = []
      for await (const chunk of stream) chunks.push(chunk as Buffer)
      const markdown = Buffer.concat(chunks).toString('utf8')

      expect(markdown).toContain('## Limitations')
      expect(markdown).toContain('## Sources')
      // Provenance must survive into the artefact a user keeps.
      expect(markdown).toMatch(/fixture|No language model was called/i)
      expect(markdown).toContain('version 2')
    })
  })
})
