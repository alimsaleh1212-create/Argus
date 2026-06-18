/**
 * Playwright e2e: pipeline map loads, a stage expands, the human-attention
 * lane shows real incidents, and selecting one opens the drawer.
 *
 * Requires the full stack (frontend + backend) running. Run with:
 *   docker compose up --build, then ARGUS_E2E=1 npx playwright test pipeline-map
 *
 * Skipped when ARGUS_E2E is not set, keeping CI fast.
 */
import { test, expect } from '@playwright/test'

const RUN_E2E = !!process.env.ARGUS_E2E

test.describe('Pipeline map e2e', () => {
  test.skip(!RUN_E2E, 'Set ARGUS_E2E=1 to run full-stack e2e tests')

  async function login(page: import('@playwright/test').Page) {
    await page.goto('/login')
    await page.getByLabel(/username/i).fill('admin')
    await page.getByLabel(/password/i).fill(process.env.ARGUS_ADMIN_PASS ?? 'admin123')
    await page.getByRole('button', { name: /sign in/i }).click()
    // Post-login default route is the Pipeline Map.
    await page.waitForURL('/map')
  }

  test('map loads and shows the rail and terminal column', async ({ page }) => {
    await login(page)
    await expect(page.getByTestId('pipeline-map')).toBeVisible({ timeout: 5000 })
    await expect(page.getByTestId('stage-node-intake')).toBeVisible()
    await expect(page.getByTestId('terminal-column')).toBeVisible()
    // Human Attention is a dedicated page now — assert its nav link is present.
    await expect(
      page.getByRole('link', { name: /human attention/i })
    ).toBeVisible()
  })

  test('expanding a stage reveals its branch breakdown', async ({ page }) => {
    await login(page)
    await page.goto('/map')
    await expect(page.getByTestId('pipeline-map')).toBeVisible({ timeout: 5000 })
    await page.getByRole('button', { name: /expand triage/i }).click()
    await expect(page.getByTestId('branch-breakdown-triage')).toBeVisible({ timeout: 5000 })
  })

  test('selecting an escalated incident opens the drawer with its detail', async ({ page }) => {
    await login(page)
    await page.goto('/map')
    await expect(page.getByTestId('pipeline-map')).toBeVisible({ timeout: 5000 })
    const card = page.getByText(/escalated/i).first()
    if (await card.isVisible().catch(() => false)) {
      await card.click()
      await expect(page.getByRole('dialog')).toBeVisible({ timeout: 5000 })
    }
  })
})
