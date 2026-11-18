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

  stage(name: string): Locator {
    return this.detail.getByRole('listitem').filter({ hasText: name })
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
