import { expect, test } from '@playwright/test';

test('repairs a flattened glued workspace and enforces its resize minimum', async ({ page, request }) => {
  const a = `workspace-glue-${Date.now()}`, b = `${a}-peer`;
  for (const [id, type, x] of [[a, 'conversation', 100], [b, 'text', 1120]] as const) {
    expect((await request.post('/api/nodes', { data: { id, type, name: id, position: { x, y: 150 } } })).ok()).toBeTruthy();
  }
  try {
    const current = await (await request.get('/api/canvas/glue')).json();
    expect((await request.patch('/api/canvas/glue', { data: { revision: current.revision,
      boxes: { [a]: { x: 100, y: 150, width: 1020, height: 96, level: 'workspace' },
        [b]: { x: 1120, y: 150, width: 286, height: 156, level: 'preview' } },
      bonds: [{ a, b, side: 'right' }],
    } })).ok()).toBeTruthy();
    await page.addInitScript(id => {
      localStorage.setItem('oaw-node-surfaces-v1', JSON.stringify({ state: { surfaceLevels: { [id]: 'workspace' } }, version: 3 }));
      localStorage.setItem('oaw-canvas-viewport-v1', JSON.stringify({ state: { viewport: { x: 0, y: 0, zoom: 0.7, width: 1280, height: 800 } }, version: 0 }));
    }, a);
    await page.goto('/');
    const card = page.locator(`.react-flow__node[data-id="${a}"]`);
    await expect(card).toHaveClass(/is-glued/);
    await expect.poll(async () => (await (await request.get('/api/canvas/glue')).json()).boxes[a].height).toBe(420);
    await card.click({ modifiers: ['Shift'], position: { x: 40, y: 15 } });
    const grip = page.locator('.glue-resize.bottom-left');
    await expect(grip).toBeVisible();
    // An unrelated card event used to refresh persisted geometry mid-gesture.
    const start = (await grip.boundingBox())!;
    await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2);
    await page.mouse.down();
    await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2 + 42, { steps: 8 });
    await expect.poll(async () => Math.round((await card.boundingBox())!.height)).toBe(336);
    expect((await request.patch(`/api/nodes/${b}`, { data: { name: 'Background update' } })).ok()).toBeTruthy();
    await expect(page.getByText('Background update', { exact: true }).first()).toBeVisible();
    // Exercise the 120ms background-refresh debounce while the pointer is held.
    await page.waitForTimeout(500);
    expect(Math.round((await card.boundingBox())!.height)).toBe(336);
    await page.mouse.up();
    await expect.poll(async () => (await (await request.get('/api/canvas/glue')).json()).boxes[a].height).toBe(480);
    const rect = (await grip.boundingBox())!;
    await page.mouse.move(rect.x + rect.width / 2, rect.y + rect.height / 2);
    await page.mouse.down();
    await page.mouse.move(rect.x + 550, rect.y - 240, { steps: 12 });
    await page.mouse.up();
    await expect.poll(async () => {
      const saved = (await (await request.get('/api/canvas/glue')).json()).boxes[a];
      return { width: saved.width, height: saved.height };
    }).toEqual({ width: 640, height: 420 });
    await page.reload();
    await expect(card).toHaveClass(/is-glued/);
    await expect.poll(async () => Math.round((await card.boundingBox())!.height)).toBe(294);
    await page.screenshot({ path: 'test-results/glue-workspace-repaired.png' });
  } finally {
    await request.delete(`/api/nodes/${a}`);
    await request.delete(`/api/nodes/${b}`);
  }
});
