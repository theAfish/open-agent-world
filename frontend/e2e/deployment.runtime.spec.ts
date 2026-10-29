import { expect, test } from '@playwright/test';

test('deployment reuses Legion workspace and business controls without engineering access', async ({ page }, testInfo) => {
  const errors: string[] = [];
  const writes: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (request.method() !== 'GET') writes.push(request.url()); });
  await page.goto('/');
  await page.getByLabel('Access password').fill('test-operator-password-93841');
  await page.getByRole('button', { name: 'Open application', exact: true }).click();
  await expect(page.locator('.legion-workspace')).toBeVisible();
  await expect(page.locator('.legion-window-titlebar')).toContainText('Operations');
  await expect(page.locator('.text-editor:visible')).toHaveValue('Operations workspace content');
  await page.getByRole('button', { name: 'Switch workspace', exact: true }).click();
  await expect(page.getByRole('menuitemradio')).toHaveText(['Operations', 'Published studio']);
  await page.screenshot({ path: testInfo.outputPath('workspace-switcher.png'), fullPage: true, animations: 'disabled' });
  await page.getByRole('menuitemradio', { name: 'Published studio' }).click();
  for (const name of ['Edit layout', 'Back to canvas', 'Open settings', 'Publish application']) {
    await expect(page.getByRole('button', { name, exact: true })).toHaveCount(0);
  }
  await expect(page.locator('.published-layout, .runtime-conversation')).toHaveCount(0);
  await expect(page.locator('.text-editor:visible')).toHaveValue('# Welcome\nThis is the published guide.');
  await expect(page.locator('.text-editor:visible')).toHaveAttribute('readonly', '');
  await page.getByRole('button', { name: /^Switch group:/ }).click();
  await page.getByRole('button', { name: 'New group', exact: true }).click();
  await page.getByRole('button', { name: 'Create group', exact: true }).click();
  await expect(page.locator('.conversation-group-draft')).toHaveCount(0);
  await expect(page.locator('.conversation-participant-row')).toHaveCount(1);
  await expect(page.getByRole('button', { name: 'Assistant idle · insert mention', exact: true })).toBeVisible();
  await page.getByLabel('Conversation message', { exact: true }).fill('Hello from the shared workspace');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await expect(page.locator('.workspace-message.is-agent')).toBeVisible();
  await page.getByRole('tab', { name: 'Tasks', exact: true }).click();
  await expect(page.getByLabel('Default executor', { exact: true })).toHaveCount(0);
  await page.getByLabel('New task title').fill('Keep this draft');
  await page.getByRole('tab', { name: 'Guide', exact: true }).click();
  await page.getByRole('tab', { name: 'Tasks', exact: true }).click();
  await expect(page.getByLabel('New task title')).toHaveValue('Keep this draft');
  await page.getByRole('button', { name: 'Add task', exact: true }).click();
  await expect(page.locator('.task-row')).toContainText('Keep this draft');
  await page.getByRole('button', { name: 'Complete Keep this draft', exact: true }).click();
  await expect(page.locator('.task-row.is-done')).toContainText('Keep this draft');
  await page.getByRole('tab', { name: 'Plugin notes', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Save note', exact: true })).toBeEnabled();
  await page.getByLabel('Workspace note').fill('Saved through the original plugin view');
  await page.getByRole('button', { name: 'Save note', exact: true }).click();
  await expect(page.getByText('Saved', { exact: true })).toBeVisible();
  await expect(page.getByText('Connection:', { exact: false })).toHaveCount(0);
  const download = page.waitForEvent('download');
  await page.getByRole('link', { name: 'Download note', exact: true }).click();
  expect((await download).suggestedFilename()).toBe('notes.txt');
  const result = await page.evaluate(async () => ({
    world: (await fetch('/api/world')).status,
    settings: (await fetch('/api/settings/models')).status,
    bootstrap: await (await fetch('/api/runtime-app')).text(),
  }));
  expect(result.world).toBe(404); expect(result.settings).toBe(404);
  expect(result.bootstrap).not.toContain('PRIVATE INSTRUCTION');
  expect(result.bootstrap).not.toContain('PRIVATE-DEMO');
  expect(result.bootstrap).not.toContain('password');
  expect(writes.filter(url => /\/api\/nodes\//.test(url))).toEqual([]);
  await page.reload();
  await expect(page.locator('.legion-window-titlebar')).toContainText('Operations');
  await page.getByRole('button', { name: 'Switch workspace', exact: true }).click();
  await page.getByRole('menuitemradio', { name: 'Published studio' }).click();
  await page.getByRole('tab', { name: 'Plugin notes', exact: true }).click();
  await expect(page.getByLabel('Workspace note')).toHaveValue('Saved through the original plugin view');
  await page.getByRole('tab', { name: 'Tasks', exact: true }).click();
  await expect(page.locator('.task-row.is-done')).toContainText('Keep this draft');
  await page.screenshot({ path: testInfo.outputPath('desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('.legion-layout-stage')).toBeVisible();
  await page.getByRole('button', { name: 'Switch workspace', exact: true }).click();
  await expect(page.getByRole('menu')).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('mobile.png'), fullPage: true, animations: 'disabled' });
  await page.getByRole('menuitemradio', { name: 'Operations' }).click();
  await expect(page.locator('.text-editor:visible')).toHaveValue('Operations workspace content');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByLabel('Access password')).toBeVisible();
  expect(errors).toEqual([]);
});
