import { expect, test } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

test('workspace backup notice fits and remains visible after save', async ({ page }) => {
  const paths = ['D:\\Old Workspaces\\研究材料\\' + 'long-project-folder-'.repeat(7) + '\\workspace', 'D:\\Old Workspaces\\codex-workspace'];
  let saved = false;
  await page.route('**/api/settings/sandbox', route => {
    if (route.request().method() === 'PUT') saved = true;
    return route.fulfill({ json: { workspace_root: 'E:\\Workspaces', runtime: 'auto', backup_paths: saved ? paths : [] } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Open settings', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Settings', exact: true });
  await dialog.getByRole('button', { name: 'Sandbox', exact: true }).click();
  await expect(dialog.getByLabel('Default Workspace location', { exact: true })).toBeEnabled();
  await dialog.getByRole('button', { name: 'Save settings', exact: true }).click();
  await expect(dialog.getByText('Workspace settings saved.', { exact: true })).toBeVisible();
  await expect(dialog.getByText(/please delete unused backup folders manually/)).toBeVisible();
  const panel = dialog.locator('.settings-workspace-backups');
  for (const width of [1280, 540]) {
    await page.setViewportSize({ width, height: 800 });
    const fits = await panel.evaluate(element => ({ fits: element.scrollWidth <= element.clientWidth,
      selectable: getComputedStyle(element.querySelector('code')!).userSelect }));
    expect(fits).toEqual({ fits: true, selectable: 'text' });
    await mkdir('../.outputs/sandbox-backup-notice', { recursive: true });
    await page.screenshot({ path: `../.outputs/sandbox-backup-notice/${width}.png` });
  }
  await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  await page.getByRole('button', { name: 'Open settings', exact: true }).click();
  await expect(page.getByText(paths[1], { exact: true })).toBeVisible();
});
