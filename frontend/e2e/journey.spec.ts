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

    await test.step('progress is reported while the workflow runs', async () => {
      // Named stages, not just a spinner: the run takes minutes with live
      // models, and "something is happening" is not progress.
      // The polite live region, which is what a screen reader is told.
      await expect(app.progress).toHaveText(
        /Queued|Stage \d of 5|stages complete/,
      )
    })

    await test.step('every workflow stage is visible and reaches success', async () => {
      await app.waitForApprovalGate()
      await expect(app.progress).toHaveText(/All stages complete/)
      await app.openTab('Workflow')
      for (const stage of ['Plan', 'Retrieve sources', 'Analyse', 'Check citations', 'Draft report']) {
        await expect(app.stage(stage).getByText('Succeeded')).toBeVisible()
      }
    })

    await test.step('retrieved sources are shown and labelled as fixtures', async () => {
      await app.openTab('Sources')
      await expect(app.panel.getByText(/from the committed fixture corpus/)).toBeVisible()
      const sources = app.panel.getByRole('listitem').filter({ hasText: FIXTURE_HOST })
      expect(await sources.count()).toBeGreaterThan(0)
      // Exact: the fixture URLs themselves contain the word "fixtures", so a
      // substring match would also hit every source link.
      await expect(sources.first().getByText('Fixture', { exact: true })).toBeVisible()
    })

    await test.step('claim support is shown, and is not described as fact checking', async () => {
      await app.openTab('Claims')
      await expect(app.panel.getByText(/confirms a source/i)).toBeVisible()
      await expect(app.panel.getByText(/not that the claim is true/i).first()).toBeVisible()
      await expect(app.panel.getByRole('list', { name: /claim support summary/i })).toBeVisible()
    })

    await test.step('the report renders as a document, not as Markdown source', async () => {
      await app.openTab('Report')
      await expect(app.panel.getByText('Version 1')).toBeVisible()
      // Real headings and a real table, so the claim-support table is
      // navigable rather than a wall of pipes.
      await expect(app.panel.getByRole('heading', { level: 4, name: /sources/i })).toBeVisible()
      await expect(app.panel.getByRole('table')).toBeVisible()
      await expect(app.panel.getByRole('heading', { level: 4, name: /limitations/i })).toBeVisible()
    })

    await test.step('the exact Markdown that will be exported is inspectable', async () => {
      await app.panel.getByRole('button', { name: /show markdown source/i }).click()
      await expect(app.panel.getByText(/^## Limitations$/m)).toBeVisible()
      await app.panel.getByRole('button', { name: /show rendered/i }).click()
    })

    await test.step('export is locked before approval', async () => {
      await expect(app.exportLink).toHaveCount(0)
      await expect(app.panel.getByText(/export unlocks after approval/i)).toBeVisible()
    })

    await test.step('a revision without a note is refused by the client', async () => {
      await app.revisionButton.click()
      await expect(page.getByRole('alert')).toContainText(/note explaining what should change/i)
    })

    await test.step('a revision with a note produces a second version', async () => {
      await app.reviewerNote.fill('Say more about operational burden for a small team.')
      await app.revisionButton.click()
      await app.waitForApprovalGate()
      // The task leaves the gate, re-runs, and comes back with a v2 the
      // reviewer can switch between.
      const versions = app.panel.getByRole('group', { name: /report version/i })
      await expect(versions).toBeVisible({ timeout: 60_000 })
      await expect(versions.getByRole('button', { name: 'v2' })).toHaveAttribute(
        'aria-pressed',
        'true',
      )
    })

    await test.step('the superseded version is still readable, and labelled', async () => {
      await app.panel.getByRole('button', { name: 'v1' }).click()
      await expect(app.panel.getByText(/superseded by version 2/i)).toBeVisible()
      await app.panel.getByRole('button', { name: 'v2' }).click()
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
