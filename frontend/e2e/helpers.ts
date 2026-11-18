/**
 * Page objects for the dashboard.
 *
 * Selectors live here so a UI change breaks one file rather than every spec,
 * and everything is addressed by accessible role or visible text: a test that
 * can only find a control by CSS class is not evidence that a user could find
 * it either.
 */

import { expect, type Locator, type Page } from '@playwright/test'

/** The fixture corpus is the only retrieval source in the offline profile. */
export const FIXTURE_HOST = 'fixtures.agentflow.test'

export const DEMO_QUESTION =
  'Compare three open-source workflow orchestration frameworks for a small engineering team.'

export class Dashboard {
  readonly page: Page

  constructor(page: Page) {
    this.page = page
  }

  async open(path = '/'): Promise<void> {
    await this.page.goto(path)
    await expect(this.page.getByRole('heading', { name: 'AgentFlow', level: 1 })).toBeVisible()
  }

  get questionField(): Locator {
    return this.page.getByLabel('Research question')
  }

  get submitButton(): Locator {
    return this.page.getByRole('button', { name: 'Start research' })
  }

  get taskList(): Locator {
    return this.page.getByRole('region', { name: /your research tasks/i })
  }

  get detail(): Locator {
    return this.page.getByRole('region', { name: /task detail/i })
  }

  /** Submit a question and wait until its task is the one on screen. */
  async submit(question = DEMO_QUESTION): Promise<void> {
    await this.questionField.fill(question)
    await this.submitButton.click()
    await expect(this.detail.getByRole('heading', { name: question })).toBeVisible()
  }

  /** Wait for the workflow to park at the human approval gate. */
  async waitForApprovalGate(): Promise<void> {
    await expect(this.detail.getByText('Awaiting approval').first()).toBeVisible({
      timeout: 60_000,
    })
  }

  /** Open one of the detail sections. */
  async openTab(name: 'Report' | 'Sources' | 'Claims' | 'Workflow'): Promise<void> {
    await this.page.getByRole('tab', { name: new RegExp(`^${name}`) }).click()
    await expect(this.page.getByRole('tabpanel')).toBeVisible()
  }

  /** The polite live region that reports workflow progress. */
  get progress(): Locator {
    return this.detail.getByRole('group', { name: 'Workflow progress' }).getByRole('status')
  }

  get panel(): Locator {
    return this.page.getByRole('tabpanel')
  }

  stage(name: string): Locator {
    return this.panel.getByRole('listitem').filter({ hasText: name })
  }

  get approveButton(): Locator {
    return this.page.getByRole('button', { name: /approve report/i })
  }

  get revisionButton(): Locator {
    return this.page.getByRole('button', { name: /request revision/i })
  }

  get reviewerNote(): Locator {
    return this.page.getByLabel(/reviewer note/i)
  }

  get exportLink(): Locator {
    return this.page.getByRole('link', { name: /download markdown/i })
  }
}

/**
 * Create a task through the API and wait for it to park at the approval gate.
 *
 * Specs that are about the *interface* -- navigation, accessibility, layout --
 * need a finished task to look at, not a demonstration that the workflow
 * runs; the journey spec already proves that through the UI. `page.request`
 * carries the page's cookies, so the task belongs to the session the browser
 * is using.
 */
export async function seedApprovedGateTask(
  page: Page,
  question = DEMO_QUESTION,
): Promise<string> {
  const created = await page.request.post('/api/tasks', { data: { question } })
  if (!created.ok()) {
    throw new Error(`seeding failed: ${created.status()} ${await created.text()}`)
  }
  const { id } = (await created.json()) as { id: string }

  const deadline = Date.now() + 60_000
  for (;;) {
    const detail = await page.request.get(`/api/tasks/${id}`)
    const { status } = (await detail.json()) as { status: string }
    if (status === 'awaiting_approval') return id
    if (status === 'failed') throw new Error(`seeded task ${id} failed`)
    if (Date.now() > deadline) throw new Error(`seeded task ${id} stuck in ${status}`)
    await page.waitForTimeout(500)
  }
}

/**
 * Serve a canned API response for one endpoint.
 *
 * Used only for states the real backend will not readily produce -- a dead
 * API, a 429, hostile report content. The happy path is never mocked: that
 * would turn an end-to-end test into a screenshot of our own assumptions.
 */
export async function mockApi(
  page: Page,
  urlPattern: string | RegExp,
  response: { status?: number; json?: unknown; body?: string; headers?: Record<string, string> },
): Promise<void> {
  await page.route(urlPattern, async (route) => {
    await route.fulfill({
      status: response.status ?? 200,
      contentType: response.json !== undefined ? 'application/json' : 'text/plain',
      headers: response.headers ?? {},
      body: response.json !== undefined ? JSON.stringify(response.json) : (response.body ?? ''),
    })
  })
}
