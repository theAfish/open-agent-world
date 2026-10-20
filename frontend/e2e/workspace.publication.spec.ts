import { expect, test } from '@playwright/test';

test('publishes the original research layout with shared card interfaces', async ({ page }) => {
  const errors: string[] = [];
  const failed: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => {
    if (response.url().includes('/api/runtime-app/workspace') && response.status() >= 400) failed.push(`${response.status()} ${response.url()}`);
  });
  await page.request.post('/api/deployment/session', { data: { password: 'test-operator-password-93841' } });
  const app = await (await page.request.get('/api/runtime-app')).json();
  const sandbox = app.cards.find((card: { type: string }) => card.type === 'sandbox').id;
  const started = await page.request.post(`/api/runtime-app/workspace/sandboxes/${sandbox}/start`);
  expect(started.ok(), await started.text()).toBe(true);
  await page.goto('/');
  await expect(page.locator('.legion-workspace')).toBeVisible();
  await expect(page.locator('.execution-graph')).toBeVisible();
  await expect(page.locator('.jobs-board')).toBeVisible();
  await page.getByRole('tab', { name: 'Skills & knowledge', exact: true }).click();
  await expect(page.getByRole('searchbox', { name: 'Search knowledge' })).toBeVisible();
  await expect(page.locator('.kdg-count')).not.toContainText(/^0 /);
  await page.getByRole('button', { name: 'New entry', exact: true }).click();
  await page.getByLabel('Title', { exact: true }).fill('Browser workspace review');
  await page.getByLabel('Summary', { exact: true }).fill('Saved with the existing card handler');
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Save changes', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Toggle navigator' }).click();
  await expect(page.getByText('Assimilate a Toolset', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Download source snapshots' })).toBeVisible();

  await page.getByRole('tab', { name: 'Research artifacts', exact: true }).click();
  await expect(page.locator('.artifact-file-path')).toContainText('crystal.xyz');
  await expect(page.getByText('Manage version', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Restore retained references', { exact: true })).toHaveCount(0);
  const download = page.waitForEvent('download');
  await page.locator('.artifact-files a[download]').click();
  await download;

  // The published file connection drives the original Structure Viewer.
  await page.getByRole('button', { name: 'crystal.xyz', exact: true }).click();
  await expect(page.locator('.structure-viewer--workspace')).toBeVisible();
  await expect(page.locator('.structure-viewer--workspace canvas')).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: '../.tmp/published-research-workspace.png', fullPage: true });
  expect(errors).toEqual([]);
  expect(failed).toEqual([]);
  expect((await page.request.get('/api/settings/models')).status()).toBe(404);
  expect((await page.request.get('/api/world')).status()).toBe(404);
});
