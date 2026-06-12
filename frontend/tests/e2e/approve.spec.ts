/**
 * Playwright e2e: sign-in → queue → open parked incident → approve → see "remediated" (T038).
 *
 * These tests require the full stack (frontend + backend) to be running.
 * Run with: docker compose up --build, then npx playwright test
 *
 * The test is skipped when ARGUS_E2E is not set, keeping CI fast.
 */
import { test, expect } from '@playwright/test'

const RUN_E2E = !!process.env.ARGUS_E2E

test.describe('Approval workflow e2e', () => {
  test.skip(!RUN_E2E, 'Set ARGUS_E2E=1 to run full-stack e2e tests')

  async function login(page: import('@playwright/test').Page) {
    await page.goto('/login')
    await page.getByLabel(/username/i).fill('admin')
    await page.getByLabel(/password/i).fill(process.env.ARGUS_ADMIN_PASS ?? 'admin123')
    await page.getByRole('button', { name: /sign in/i }).click()
    await page.waitForURL('/queue')
  }

  test('sign-in → queue → open parked incident → approve → see remediated', async ({ page }) => {
    await login(page)

    // Wait for the queue to load
    await expect(page.getByRole('table', { name: /incident queue/i })).toBeVisible({ timeout: 5000 })

    // Filter for awaiting_approval incidents
    await page.getByRole('button', { name: /toggle filters/i }).click()
    const apvButton = page.getByRole('button', { name: /awaiting_approval/i })
    await apvButton.click()

    // Wait for a row with "Awaiting Approval" badge to appear
    const row = page.getByRole('row').filter({ hasText: /Awaiting Approval/ }).first()
    await row.waitFor({ timeout: 10_000 })

    // Open the incident detail
    await row.click()
    await expect(page.getByText(/human approval required/i)).toBeVisible({ timeout: 5000 })

    // Click Approve
    await page.getByRole('button', { name: /approve remediation/i }).click()

    // Confirm in dialog
    await page.getByRole('button', { name: /confirm approve/i }).click()

    // Wait for disposition to update — poll the page until "remediated" appears
    await expect(
      page.getByText(/remediated/i).first()
    ).toBeVisible({ timeout: 10_000 })
  })

  test('reject path → rejected_by_human', async ({ page }) => {
    await login(page)

    await expect(page.getByRole('table', { name: /incident queue/i })).toBeVisible({ timeout: 5000 })

    await page.getByRole('button', { name: /toggle filters/i }).click()
    await page.getByRole('button', { name: /awaiting_approval/i }).click()

    const row = page.getByRole('row').filter({ hasText: /Awaiting Approval/ }).first()
    await row.waitFor({ timeout: 10_000 })
    await row.click()

    await expect(page.getByText(/human approval required/i)).toBeVisible()

    await page.getByRole('button', { name: /reject remediation/i }).click()
    await page.getByRole('button', { name: /confirm reject/i }).click()

    await expect(
      page.getByText(/rejected/i).first()
    ).toBeVisible({ timeout: 10_000 })
  })

  test('sign-in navigates to queue on success', async ({ page }) => {
    await login(page)
    await expect(page).toHaveURL(/\/queue/)
    await expect(page.getByRole('navigation', { name: /main navigation/i })).toBeVisible()
  })

  test('wrong credentials shows error message', async ({ page }) => {
    await page.goto('/login')
    await page.getByLabel(/username/i).fill('admin')
    await page.getByLabel(/password/i).fill('wrongpassword')
    await page.getByRole('button', { name: /sign in/i }).click()
    await expect(page.getByText(/invalid credentials/i)).toBeVisible({ timeout: 3000 })
  })
})
