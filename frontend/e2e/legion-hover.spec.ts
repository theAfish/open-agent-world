import { expect, test } from '@playwright/test';

test('Legion previews show members on cards and empty canvas without blocking pan', async ({ page, request }) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.addInitScript(() => {
    localStorage.setItem('oaw.locale', 'en');
    localStorage.setItem('oaw-onboarding-v1', JSON.stringify({ state: { status: 'skipped' }, version: 1 }));
  });
  const ids: string[] = [];
  let savedId: string | undefined;
  try {
    for (const [name, x] of [['Hover Scout', 400], ['Hover Writer', 1000]] as const) {
      const response = await request.post('/api/nodes', { data: { type: 'agent', name, position: { x, y: 250 } } });
      expect(response.ok()).toBeTruthy();
      ids.push((await response.json()).id);
    }
    const formed = await request.post('/api/legion-groups', { data: { name: 'Hover team', node_ids: ids } });
    expect(formed.ok()).toBeTruthy();
    const saved = await request.post('/api/legions', { data: { name: 'Hover formation', node_ids: ids } });
    expect(saved.ok()).toBeTruthy();
    const summary = await saved.json();
    savedId = summary.id;
    expect(summary.members).toEqual(expect.arrayContaining([{ name: 'Hover Scout', type: 'agent' }]));
    await page.goto('/');
    const group = page.locator('.legion-container');
    await expect(group).toBeVisible();
    ids.push((await group.getAttribute('data-card-id'))!);
    await page.getByRole('button', { name: 'Fit view', exact: true }).click();
    await page.waitForTimeout(500);
    const box = (await group.boundingBox())!;
    const point = { x: box.x + box.width - 60, y: box.y + box.height - 60 };
    expect(await page.evaluate(p => document.elementFromPoint(p.x, p.y)?.classList.contains('react-flow__pane'), point)).toBe(true);
    await page.mouse.move(point.x, point.y);
    const panel = page.getByRole('tooltip', { name: 'Legion contents' });
    await expect(panel).not.toBeVisible();
    await expect(panel).toBeVisible();
    await expect(panel).toContainText('Hover Scout');
    await expect(panel).toContainText('Hover Writer');
    await page.screenshot({ path: '../.open-agent-world/legion-hover-canvas.png' });
    await page.keyboard.press('Escape');
    await expect(panel).not.toBeVisible();
    await page.mouse.move(5, 5);
    await page.mouse.move(point.x, point.y);
    await expect(panel).toBeVisible();
    const viewport = page.locator('#oaw-world-map .react-flow__viewport').first();
    const transform = await viewport.getAttribute('style');
    await page.mouse.down();
    await expect(panel).not.toBeVisible();
    await page.mouse.move(point.x - 60, point.y - 30, { steps: 5 });
    await page.mouse.up();
    await expect(viewport).not.toHaveAttribute('style', transform!);
    await page.locator(`[data-card-id="${ids[0]}"]`).first().hover();
    await page.waitForTimeout(750);
    await expect(panel).not.toBeVisible();
    // Header hover only observes events: dragging and controls retain ownership.
    const header = (await group.locator('.container-header').boundingBox())!;
    await page.mouse.move(header.x + 30, header.y + 30);
    await expect(panel).toBeVisible();
    await expect(panel).toContainText('Hover Scout');
    const groupId = ids[2];
    const position = async () => (await (await request.get(`/api/nodes/${groupId}`)).json()).position;
    const beforeDrag = await position();
    await page.mouse.down();
    await expect(panel).not.toBeVisible();
    await page.mouse.move(header.x + 90, header.y + 70, { steps: 5 });
    await page.mouse.up();
    await expect.poll(position).not.toEqual(beforeDrag);
    const settings = group.getByRole('button', { name: 'Legion settings', exact: true });
    await settings.hover();
    await expect(panel).toBeVisible();
    await settings.click();
    await expect(panel).not.toBeVisible();
    await expect(settings).toHaveAttribute('aria-expanded', 'true');
    await page.getByRole('button', { name: 'Close Legion settings' }).click();
    await page.getByRole('tab', { name: /Legions/ }).click();
    const card = page.locator(`[data-palette-card="${savedId}"]`);
    await expect(card).not.toHaveAttribute('title');
    await card.hover();
    await expect(panel).toBeVisible();
    await expect(panel).toContainText('Hover Scout');
    await page.screenshot({ path: '../.open-agent-world/legion-hover-card.png' });
    const bounds = (await panel.boundingBox())!;
    expect(bounds.y).toBeGreaterThanOrEqual(0);
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(1000);
  } finally {
    if (savedId) await request.delete(`/api/legions/${savedId}`);
    await request.post('/api/nodes/batch-delete', { data: { node_ids: ids } });
  }
});

