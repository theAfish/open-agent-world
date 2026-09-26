import { expect, test } from '@playwright/test';
import { resetTutorialProfile } from './tutorial-profile';

// Run with the isolated E2E backend (core.mock), never against a user's world.
test('first goal survives model setup, becomes a draft, and opens a resumable workspace', async ({ page, request }) => {
  await resetTutorialProfile(request);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  let catalog = { revision: 1, connections: [], default_model: null } as Record<string, unknown>;
  await page.route('**/api/settings/models', async route => {
    if (route.request().method() === 'PUT') {
      const draft = route.request().postDataJSON();
      catalog = { ...draft, revision: 2, connections: draft.connections.map(({ api_key, ...connection }: { api_key?: string }) => ({ ...connection, api_key_configured: !!api_key })) };
    }
    await route.fulfill({ json: catalog });
  });
  await page.route('**/api/settings/models/discover', route => route.fulfill({ json: { models: [{ id: 'test-chat', name: 'Test chat' }], truncated: false } }));
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto('/');
    const goal = 'Help me organize my notes.';
    await page.getByLabel('What would you like help with?').fill(goal);
    await page.getByRole('button', { name: 'Open workspace', exact: true }).click();
    await page.getByRole('button', { name: 'OpenAI', exact: true }).click();
    await page.getByLabel('API key', { exact: true }).fill('test-only-placeholder');
    await page.getByRole('button', { name: 'Get available models', exact: true }).click();
    await page.getByLabel('Available models').selectOption('test-chat');
    await expect(page.getByLabel('Available models')).toHaveValue('test-chat');
    await page.getByRole('button', { name: 'Save settings', exact: true }).click();
    const workspace = page.locator('dialog.legion-workspace[open]');
    await expect(workspace).toBeVisible();
    await expect(workspace.locator('.workspace-composer textarea')).toHaveValue(goal);
    await expect(workspace.locator('.workspace-message.is-user')).toHaveCount(0);
    await workspace.getByRole('button', { name: 'Send message', exact: true }).click();
    await expect(workspace.locator('.workspace-message.is-user')).toContainText(goal);
    await expect(workspace.locator('.workspace-message.is-agent')).toContainText('Mock response:');
    await page.reload();
    await expect(workspace).toBeVisible();
    await expect(workspace.locator('.workspace-message.is-user')).toContainText(goal);
    expect(errors).toEqual([]);
  } finally {
    const nodes = await (await request.get('/api/nodes')).json();
    if (nodes.length) await request.post('/api/nodes/batch-delete', { data: { node_ids: nodes.map((n: { id: string }) => n.id) } });
    await resetTutorialProfile(request);
  }
});
