import { expect, test } from "@playwright/test";

test("generated copies fly from their template and reusable activity effects follow runtime events", async ({ page, request }) => {
  await page.setViewportSize({ width: 2300, height: 1200 });
  const box = await (await request.post('/api/nodes', { data: { type: 'oaw.barracks', position: { x: 0, y: 0 } } })).json();
  const template = await (await request.post('/api/nodes', { data: {
    type: 'agent', name: 'Researcher', parent_id: box.id, position: { x: 160, y: 160 },
  } })).json();
  await page.goto('/');
  await expect(page.locator(`[data-card-id="${template.id}"]`)).toBeVisible();
  const instance = await (await request.post(`/api/nodes/${box.id}/summoning/actions`, { data: {
    action: 'summon', agent_id: template.id, prompt: 'Inspect sources',
  } })).json();
  const target = page.locator(`[data-card-id="${instance.entry_agent_id}"]`);
  const workspace = page.locator(`[data-card-id="${instance.workspace_id}"]`);
  await expect(page.locator('.generation-copy')).toHaveCount(1);
  await expect(target).toHaveAttribute('data-generation', 'flying');
  await expect(page.locator(`[data-card-id="${template.id}"]`)).toHaveCount(1);
  await expect(page.locator('.generation-flight')).toHaveCSS('pointer-events', 'none');
  await expect.poll(async () => {
    const copy = await page.locator('.generation-copy').boundingBox();
    const source = await page.locator(`[data-card-id="${template.id}"]`).boundingBox();
    return !!copy && !!source && copy.x > source.x + 100;
  }).toBeTruthy();
  await page.screenshot({ path: '../.open-agent-world/node-generation.png' });
  await expect(page.locator('.generation-copy')).toHaveCount(0);
  await expect(target).toHaveCSS('opacity', '1');
  const persisted = await (await request.get(`/api/nodes/${instance.entry_agent_id}`)).json();
  await page.reload();
  await expect(target).toBeVisible();
  await expect(page.locator('.generation-flight')).toHaveCount(0);

  // Exercise the real event consumer with controlled states; mock Runs finish immediately.
  const state = async (status: string, type: string) => {
    await page.evaluate(async ({ id, status, type }) => {
      const modulePath = '/src/state/worldStore.ts';
      const { useWorldStore } = await import(modulePath);
      const timestamp = new Date().toISOString();
      useWorldStore.getState().ingestEvent({ id: `${type}-${timestamp}`, node_id: id, type, timestamp, payload: {} });
      useWorldStore.getState().ingestEvent({ id: `status-${timestamp}`, node_id: id, type: 'agent_status_changed', timestamp, payload: { status } });
    }, { id: instance.entry_agent_id, status, type });
  };
  await state('running', 'run_started');
  await expect(target).toHaveAttribute('data-activity', 'running');
  await expect(workspace).toHaveAttribute('data-activity', 'running');
  await expect(workspace.getByRole('status')).toHaveText('Working · 1 active');
  await expect(target.locator('.activity-glow > i')).toHaveCount(4);
  await expect(target.locator('.activity-glow')).toHaveCSS('opacity', '0.95');
  await page.screenshot({ path: '../.open-agent-world/node-effects-running.png' });
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
  await page.screenshot({ path: '../.open-agent-world/node-effects-running-dark.png' });
  await target.getByRole('button', { name: 'Collapse Researcher card' }).click();
  await expect(target).toHaveAttribute('data-surface-level', 'node');
  await expect(target).toHaveCSS('width', '96px');
  await expect(target).toHaveCSS('border-top-left-radius', '48px');
  await page.screenshot({ path: '../.open-agent-world/node-effects-running-node.png' });
  await target.getByRole('button', { name: 'Expand Researcher card' }).click();
  await target.click({ position: { x: 140, y: 75 } });
  await expect(target).toHaveAttribute('data-surface-level', 'inspector');
  await expect(target).toHaveAttribute('data-activity', 'running');
  await state('waiting', 'run_waiting');
  await expect(workspace.getByRole('status')).toHaveText('Waiting · 1');
  await expect(target.locator('.activity-glow > i')).toHaveCount(0);
  await state('idle', 'run_succeeded');
  await expect(workspace.getByRole('status')).toHaveText('Completed');
  await state('idle', 'run_failed');
  await expect(workspace.getByRole('status')).toHaveText('Failed');
  await page.screenshot({ path: '../.open-agent-world/node-effects-failed.png' });
  await state('idle', 'run_cancelled');
  await expect(workspace.getByRole('status')).toHaveText('Stopped');
  expect((await (await request.get(`/api/nodes/${instance.entry_agent_id}`)).json()).position).toEqual(persisted.position);
  expect((await (await request.get(`/api/nodes/${template.id}`)).json()).position).toEqual(template.position);

  await request.post(`/api/nodes/${box.id}/summoning/actions`, { data: { action: 'reclaim', instance_id: instance.id } });
  await expect(workspace).toHaveCount(0);

  await page.emulateMedia({ reducedMotion: 'reduce' });
  const reduced = await (await request.post(`/api/nodes/${box.id}/summoning/actions`, { data: {
    action: 'summon', agent_id: template.id, prompt: 'Continue',
  } })).json();
  await expect(page.locator(`[data-card-id="${reduced.entry_agent_id}"]`)).toHaveCSS('opacity', '1');
  await expect(page.locator('.generation-copy')).toHaveCount(0);
  await request.post(`/api/nodes/${box.id}/summoning/actions`, { data: { action: 'reclaim', instance_id: reduced.id } });
  await request.post('/api/nodes/batch-delete', { data: { node_ids: [template.id, box.id] } });
});
