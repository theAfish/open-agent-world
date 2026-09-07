import { expect, test } from "@playwright/test";

test("equipped skills reuse draggable detail cards and keep a visual origin", async ({ page, request }) => {
  await page.setViewportSize({ width: 1800, height: 1200 });
  const owner = await (await request.post('/api/nodes', { data: {
    type: 'agent', name: 'Skill owner', position: { x: 300, y: 180 },
  } })).json();
  const item = await (await request.post('/api/nodes', { data: {
    type: 'oaw.barracks.summoner', equipment: { owner_id: owner.id, relationship: 'oaw.barracks.use' },
  } })).json();
  try {
    await page.goto('/');
    await page.getByRole('button', { name: 'Equipment for Skill owner', exact: true }).click();
    const surface = page.locator(`[data-card-id="${item.id}"]`);
    await surface.getByRole('button', { name: 'Summoning', exact: true }).click();
    await expect(surface).toHaveAttribute('data-surface-level', 'inspector');
    await expect(surface).toHaveCSS('width', '438px');
    await expect(surface.getByRole('button', { name: 'Open workspace', exact: true })).toHaveCount(0);
    await expect(page.getByRole('dialog')).toHaveCount(0);
    const origin = page.locator(`[data-equipment-origin="${item.id}"]`);
    const bridge = page.locator(`[data-surface-bridge="${item.id}"]`);
    await expect(origin).toBeVisible();
    await expect(bridge).toHaveCSS('pointer-events', 'none');
    await expect(async () => {
      const a = (await origin.boundingBox())!, b = (await surface.boundingBox())!;
      expect(b.x).toBeGreaterThan(a.x + a.width + 40);
    }).toPass();
    await page.screenshot({ path: '../.open-agent-world/equipment-detail-light.png' });
    const before = (await surface.boundingBox())!;
    const path = await bridge.locator('path').getAttribute('d');
    await page.mouse.move(before.x + 220, before.y + 300);
    await page.mouse.down();
    await page.mouse.move(before.x + 330, before.y + 340, { steps: 14 });
    await page.mouse.up();
    await expect(async () => {
      const after = (await surface.boundingBox())!;
      expect(after.x).toBeGreaterThan(before.x + 90);
    }).toPass({ timeout: 5000 });
    await expect(bridge.locator('path')).not.toHaveAttribute('d', path!);
    const saved = await (await request.get(`/api/nodes/${item.id}`)).json();
    expect(saved.equipment).toEqual(item.equipment);
    expect(saved.position).toEqual(item.position);
    await surface.getByRole('textbox', { name: 'Summoning name', exact: true }).fill('Call specialists');
    await surface.getByRole('textbox', { name: 'Summoning name', exact: true }).press('Enter');
    await expect.poll(async () => (await (await request.get(`/api/nodes/${item.id}`)).json()).name).toBe('Call specialists');
    await expect(origin).toContainText('Call specialists');
    await page.getByRole('button', { name: 'Use dark theme', exact: true }).click();
    await page.screenshot({ path: '../.open-agent-world/equipment-detail-dark.png' });
    await origin.getByRole('button', { name: 'Hide Call specialists details', exact: true }).click();
    await expect(surface).toHaveClass(/equipment-card/);
    await expect(bridge).toHaveCount(0);
    await expect(origin).toHaveCount(0);
  } finally {
    await request.post('/api/nodes/batch-delete', { data: { node_ids: [owner.id] } });
  }
});
