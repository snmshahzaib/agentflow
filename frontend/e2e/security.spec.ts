/**
 * Frontend security.
 *
 * A report is written by a model that read retrieved web pages, so both the
 * prose and the quoted text are attacker-influenced. The component tests pin
 * the renderer's behaviour; these run the same hostile content through a real
 * browser, where a `<script>` would actually execute and a `javascript:` href
 * would actually navigate.
 *
 * The report payload is intercepted because the fixture corpus deliberately
 * does not contain working exploits -- a committed corpus of real payloads
 * would be a hazard in the repository, and the backend's own injection tests
 * already cover what reaches the model.
 */

import { expect, test } from '@playwright/test'

import { Dashboard, mockApi, seedApprovedGateTask } from './helpers'

const HOSTILE_REPORT = [
  '# Compromised report',
  '',
  '<script>window.__xss = "script tag"</script>',
  '',
  '<img src=x onerror="window.__xss = \'img onerror\'">',
  '',
  '<iframe src="https://attacker.test/frame"></iframe>',
  '',
  '<a href="javascript:window.__xss=\'inline html link\'">inline html link</a>',
  '',
  '[markdown javascript link](javascript:window.__xss="markdown link")',
  '',
  '[data url link](data:text/html,<script>window.__xss="data"</script>)',
  '',
  '[legitimate source](https://example.test/page)',
  '',
  '## Limitations',
  '',
  '- Synthetic content for a security test.',
].join('\n')

test.describe('report rendering is not an injection surface', () => {
  test('hostile report content neither executes nor becomes clickable', async ({ page }) => {
    const app = new Dashboard(page)
    await app.open()
    const taskId = await seedApprovedGateTask(page)

    const consoleErrors: string[] = []
    page.on('pageerror', (error) => consoleErrors.push(error.message))

    await mockApi(page, `**/api/tasks/${taskId}/report*`, {
      json: {
        id: 'r1',
        version: 1,
        status: 'draft',
        content_markdown: HOSTILE_REPORT,
        created_at: '2026-11-18T10:00:00Z',
      },
    })

    await app.open(`/tasks/${taskId}?view=report`)
    await expect(
      app.panel.getByRole('heading', { name: 'Compromised report', level: 3 }),
    ).toBeVisible()

    await test.step('nothing ran', async () => {
      expect(await page.evaluate(() => (window as unknown as Record<string, unknown>).__xss)).toBeUndefined()
      expect(consoleErrors).toEqual([])
    })

    await test.step('no HTML element was created from the markup', async () => {
      await expect(app.panel.locator('script')).toHaveCount(0)
      await expect(app.panel.locator('iframe')).toHaveCount(0)
      await expect(app.panel.locator('img')).toHaveCount(0)
    })

    await test.step('the markup is shown as text, so a reader can see it', async () => {
      await expect(app.panel.getByText(/window.__xss = "script tag"/)).toBeVisible()
    })

    await test.step('no javascript: or data: link is clickable', async () => {
      for (const name of ['inline html link', 'markdown javascript link', 'data url link']) {
        await expect(app.panel.getByRole('link', { name })).toHaveCount(0)
        // The text survives: dropping it would hide what the report said.
        await expect(app.panel.getByText(name)).toBeVisible()
      }
      const hrefs = await app.panel.locator('a').evaluateAll((links) =>
        links.map((link) => link.getAttribute('href') ?? ''),
      )
      expect(hrefs.filter((href) => /^(javascript|data|vbscript):/i.test(href))).toEqual([])
    })

    await test.step('legitimate links open without handing over a window handle', async () => {
      const link = app.panel.getByRole('link', { name: 'legitimate source' })
      await expect(link).toHaveAttribute('href', 'https://example.test/page')
      await expect(link).toHaveAttribute('rel', 'noopener noreferrer')
      await expect(link).toHaveAttribute('target', '_blank')
    })
  })

  test('a hostile source title or URL cannot escape the sources list', async ({ page }) => {
    const app = new Dashboard(page)
    await app.open()
    const taskId = await seedApprovedGateTask(page)

    await mockApi(page, `**/api/tasks/${taskId}/sources*`, {
      json: [
        {
          id: 's1',
          label: 'S1',
          subtask_index: 0,
          url: 'javascript:window.__xss="source url"',
          title: '<img src=x onerror="window.__xss=1">',
          excerpt: '<script>window.__xss="excerpt"</script>',
          provider: 'fixture',
          rank: 0,
          is_fixture: true,
          retrieved_at: '2026-11-18T10:00:00Z',
        },
      ],
    })

    await app.open(`/tasks/${taskId}?view=sources`)
    await expect(app.panel.getByText('S1')).toBeVisible()

    expect(await page.evaluate(() => (window as unknown as Record<string, unknown>).__xss)).toBeUndefined()
    await expect(app.panel.locator('img')).toHaveCount(0)
    await expect(app.panel.locator('script')).toHaveCount(0)
    // React escapes the title, so it is visible as text rather than parsed.
    await expect(app.panel.getByText(/<img src=x onerror=/)).toBeVisible()
    // And the unsafe URL is shown but explicitly not linked.
    await expect(app.panel.getByRole('link')).toHaveCount(0)
    await expect(app.panel.getByText(/unsafe link, not opened/i)).toBeVisible()
  })

  test('the session cookie is not reachable from page scripts', async ({ page, context }) => {
    const app = new Dashboard(page)
    await app.open()

    const cookies = await context.cookies()
    const session = cookies.find((cookie) => cookie.name === 'af_session')
    expect(session, 'the session cookie should exist').toBeTruthy()
    // HttpOnly is what stops an XSS from exfiltrating the session.
    expect(session!.httpOnly).toBe(true)
    expect(session!.sameSite).toBe('Lax')

    const visible = await page.evaluate(() => document.cookie)
    expect(visible).not.toContain('af_session')
  })

  test('security headers actually reach the browser', async ({ page }) => {
    // Configured is not served. nginx *replaces* inherited add_header
    // directives as soon as a block declares one of its own, and the
    // cache-control block that answers "/" declared one -- so every security
    // header was silently absent from the page while looking correct in the
    // config. This test reads what the browser received.
    const response = await page.goto('/')
    const headers = response!.headers()

    expect(headers['x-content-type-options']).toBe('nosniff')
    expect(headers['x-frame-options']).toBe('DENY')
    expect(headers['referrer-policy']).toBe('strict-origin-when-cross-origin')

    const csp = headers['content-security-policy'] ?? ''
    expect(csp).toContain("default-src 'self'")
    expect(csp).toContain("object-src 'none'")
    expect(csp).toContain("frame-ancestors 'none'")
    // No escape hatches: either of these would make the policy decorative.
    expect(csp).not.toContain('unsafe-inline')
    expect(csp).not.toContain('unsafe-eval')
  })

  test('the app runs clean under its own Content-Security-Policy', async ({ page }) => {
    // A policy that the application violates is a policy someone will
    // loosen. This drives the real journey and fails on any violation.
    const violations: string[] = []
    page.on('console', (message) => {
      const text = message.text()
      if (/content security policy/i.test(text)) violations.push(text)
    })
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))

    const app = new Dashboard(page)
    await app.open()
    const taskId = await seedApprovedGateTask(page)

    for (const view of ['report', 'sources', 'claims', 'workflow'] as const) {
      await app.open(`/tasks/${taskId}?view=${view}`)
      await expect(app.panel).toBeVisible()
    }
    await app.approveButton.click()
    await expect(app.detail.getByText('Approved').first()).toBeVisible({ timeout: 60_000 })

    expect(violations).toEqual([])
    expect(errors).toEqual([])
  })

  test('external links in the sources list cannot reach back through window.opener', async ({
    page,
  }) => {
    const app = new Dashboard(page)
    await app.open()
    const taskId = await seedApprovedGateTask(page)
    await app.open(`/tasks/${taskId}?view=sources`)
    await expect(app.panel.getByRole('listitem').first()).toBeVisible()

    const links = app.panel.getByRole('link')
    const count = await links.count()
    expect(count).toBeGreaterThan(0)
    for (let i = 0; i < count; i++) {
      await expect(links.nth(i)).toHaveAttribute('rel', /noopener/)
    }
  })
})
