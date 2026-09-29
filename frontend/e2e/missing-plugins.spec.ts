import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { expect, test } from '@playwright/test';

// Host integration: persisted unavailable objects, snapshot transport and canvas controls.
test('missing cards and relationships remain visible and removable', async ({ page, request }, testInfo) => {
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation,
    changes: { 'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en' },
  } });
  const create = async (type: string, name: string, x: number) => {
    const response = await request.post('/api/nodes', { data: { type, name, position: { x, y: 150 } } });
    expect(response.ok()).toBe(true);
    return response.json();
  };
  const agent = await create('agent', 'Healthy agent', 100);
  const card = await create('text', 'Lost index', 560);
  const edgeResponse = await request.post('/api/edges', { data: { source: agent.id, target: card.id, relationship: 'read' } });
  expect(edgeResponse.ok()).toBe(true);
  const edge = await edgeResponse.json();
  const root = path.resolve('..');
  // Only the disposable world owned by scripts/run-e2e.mjs is altered.
  execFileSync(path.join(root, 'backend/.venv/Scripts/python.exe'), ['-c', `
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as db:
    db.execute("UPDATE cards SET type='literature.index', plugin_id='research.literature' WHERE id=?", (sys.argv[2],))
    db.execute("UPDATE edges SET relationship='literature.search', plugin_id='research.literature' WHERE id=?", (sys.argv[3],))
`, path.join(root, '.open-agent-world/playwright/database/world.sqlite3'), card.id, edge.id], { windowsHide: true });
  const snapshot = await (await request.get('/api/world')).json();
  expect(snapshot.nodes.find((node: { id: string }) => node.id === card.id).missing_plugin.plugin_id).toBe('research.literature');
  await page.goto('/');
  const missing = page.locator(`.world-card[data-card-id="${card.id}"]`);
  await expect(missing).toBeVisible();
  await expect(missing).toHaveAttribute('data-missing', 'true');
  await expect(missing).toHaveCSS('background-image', /conic-gradient/);
  await expect(page.locator('.semantic-edge-label.is-missing')).toContainText('MISSING');
  await expect(page.locator(`.semantic-edge-path[data-edge-id="${edge.id}"]`)).toHaveAttribute('data-missing', 'true');
  await page.screenshot({ path: testInfo.outputPath('missing-canvas.png') });
  await missing.locator('.card-name').hover();
  await missing.getByRole('button', { name: 'Rename', exact: true }).click();
  await missing.getByRole('textbox').fill('Retained index');
  await missing.getByRole('textbox').press('Enter');
  await expect.poll(async () => (await (await request.get(`/api/nodes/${card.id}`)).json()).name).toBe('Retained index');
  const header = missing.locator('.card-eyebrow');
  const box = (await header.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 80, box.y + box.height / 2 + 180, { steps: 12 });
  await page.mouse.up();
  await expect.poll(async () => (await (await request.get(`/api/nodes/${card.id}`)).json()).position.x).not.toBe(card.position.x);
  await missing.locator('.node-preview-body').click();
  await expect(missing.getByText('Card implementation unavailable')).toBeVisible();
  await expect(missing.getByRole('button', { name: 'Remove Retained index' })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('missing-inspector.png') });
  await missing.getByRole('button', { name: 'Remove Retained index' }).click();
  await expect(missing).toHaveCount(0);
  await expect.poll(async () => (await request.get(`/api/nodes/${card.id}`)).status()).toBe(404);
  await expect(page.locator(`.semantic-edge-path[data-edge-id="${edge.id}"]`)).toHaveCount(0);
  await expect(page.locator(`.world-card[data-card-id="${agent.id}"]`)).toBeVisible();
});
