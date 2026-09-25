import { expect, type Page } from '@playwright/test';

export async function buildTutorialLegion(page: Page, ids: { agent: string; conversation: string; sandbox: string }) {
  const guide = page.locator('.tutorial-bubble');
  await expect(guide).toHaveAttribute('data-step', 'legion-intro');
  await page.getByRole('button', { name: 'Build my Legion', exact: true }).click();
  await expect(guide).toHaveAttribute('data-step', 'legion-form');
  await expect(page.locator('.tutorial-guide')).toHaveAttribute('data-moving', 'false');
  for (const id of Object.values(ids)) {
    const node = page.locator(`.react-flow__node[data-id="${id}"]`);
    await node.locator('.card-kind-icon').click({ modifiers: ['ControlOrMeta'] });
    await expect(node).toHaveClass(/selected/);
  }
  await page.getByRole('button', { name: 'Form Legion', exact: true }).click();
  await expect(guide).toHaveAttribute('data-step', 'legion-open');
  await page.locator('[data-tutorial="legion-open"]').click();
  const workspace = page.locator('dialog.legion-workspace');
  await expect(workspace.locator('.tutorial-bubble')).toHaveAttribute('data-step', 'legion-layout');
  await workspace.locator(`[data-workspace-source="${ids.conversation}"]`).click();
  await workspace.getByRole('button', { name: 'Place selected card', exact: true }).click();
  await workspace.locator(`[data-workspace-source="${ids.sandbox}"]`).click();
  await workspace.locator('[data-dock-side="right"]').click();
  // An unsaved layout must not advance the tutorial.
  await expect(guide).toHaveAttribute('data-step', 'legion-layout');
  await workspace.getByRole('button', { name: 'Done editing', exact: true }).click();
  await expect(guide).toHaveAttribute('data-step', 'legion-return');
  await expect(workspace.locator(`[data-workspace-pane="${ids.conversation}"]`)).toBeVisible();
  await expect(workspace.locator(`[data-workspace-pane="${ids.sandbox}"]`)).toBeVisible();
  await expect(page.locator('.tutorial-guide')).toHaveAttribute('data-moving', 'false');
  await page.screenshot({ path: 'test-results/onboarding-legion-workspace.png' });
  await workspace.getByRole('button', { name: 'Back to canvas', exact: true }).click();
  await expect(guide).toHaveAttribute('data-step', 'finish');
}
