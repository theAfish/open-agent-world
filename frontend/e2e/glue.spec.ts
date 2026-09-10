import { expect, test } from '@playwright/test';
test('glue joins cards, moves the group, resizes from a free corner and survives reload', async ({ page, request }) => {
  const a = `glue-a-${Date.now()}`, b = `glue-b-${Date.now()}`;
  for (const [id, x] of [[a, 220], [b, 660]] as const) expect((await request.post('/api/nodes', { data: { id, type: 'text', name: id, position: { x, y: 300 } } })).ok()).toBeTruthy();
  try {
    await page.addInitScript(() => localStorage.setItem('oaw-canvas-viewport-v1', JSON.stringify({ state: { viewport: { x: 0, y: 0, zoom: 1, width: 1280, height: 800 } }, version: 0 })));
    await page.goto('/');
    const first = page.locator(`.react-flow__node[data-id="${a}"]`), second = page.locator(`.react-flow__node[data-id="${b}"]`);
    await expect(first).toBeVisible(); await expect(second).toBeVisible();
    await page.getByRole('button', { name: '万能胶', exact: true }).click();
    const r1 = (await first.boundingBox())!, r2 = (await second.boundingBox())!;
    await page.mouse.move(r1.x + 35, r1.y + 25); await page.mouse.down();
    await page.mouse.move(r1.x + 35 + r2.x - r1.x - r1.width, r1.y + 25, { steps: 15 });
    await expect(page.locator('.glue-seam.is-preview')).toBeVisible();
    await page.mouse.up();
    await expect(first).toHaveClass(/is-glued/); await expect(second).toHaveClass(/is-glued/);
    await expect(page.locator('.glue-resize')).toHaveCount(2);
    const glued1 = (await first.boundingBox())!, glued2 = (await second.boundingBox())!;
    expect(Math.abs(glued1.x + glued1.width - glued2.x)).toBeLessThan(2);
    await page.mouse.move(glued1.x + 35, glued1.y + 25); await page.mouse.down();
    await page.mouse.move(glued1.x + 65, glued1.y + 55, { steps: 10 }); await page.mouse.up();
    await expect.poll(async () => (await second.boundingBox())!.x - glued2.x).toBeGreaterThan(20);
    expect(Math.abs(((await second.boundingBox())!.x - glued2.x) - ((await first.boundingBox())!.x - glued1.x))).toBeLessThan(2);
    const handle = page.locator('.glue-resize.bottom-left');
    const h = (await handle.boundingBox())!;
    await page.mouse.move(h.x + 7, h.y + 7); await page.mouse.down(); await page.mouse.move(h.x + 7, h.y + 47, { steps: 10 }); await page.mouse.up();
    await expect.poll(async () => Math.round((await first.boundingBox())!.height)).toBe(Math.round(glued1.height + 40));
    await page.reload(); await expect(first).toHaveClass(/is-glued/);
  } finally { await request.delete(`/api/nodes/${a}`); await request.delete(`/api/nodes/${b}`); }
});


test('stitched relationship opens the original settings and detaching restores the edge', async ({ page, request }) => {
  const a = `glue-agent-${Date.now()}`, b = `glue-conversation-${Date.now()}`;
  for (const [id, type, x] of [[a, 'agent', 250], [b, 'conversation', 536]] as const) expect((await request.post('/api/nodes', { data: { id, type, name: id, position: { x, y: 250 } } })).ok()).toBeTruthy();
  const edge = await request.post('/api/edges', { data: { source: a, target: b, relationship: 'participate' } });
  expect(edge.ok()).toBeTruthy();
  try {
    await page.addInitScript(({ a, b }) => {
      localStorage.setItem('oaw-canvas-viewport-v1', JSON.stringify({ state: { viewport: { x: 0, y: 0, zoom: 1, width: 1280, height: 800 } }, version: 0 }));
      localStorage.setItem('oaw-glue-v1', JSON.stringify({ state: { boxes: {
        [a]: { x: 155, y: 220, width: 286, height: 156, level: 'preview' },
        [b]: { x: 441, y: 220, width: 286, height: 196, level: 'preview' },
      }, bonds: [{ a, b, side: 'right' }] }, version: 0 }));
    }, { a, b });
    await page.goto('/');
    await page.locator('.glue-stitch').click();
    await expect(page.getByRole('complementary', { name: 'Selected relationship' })).toBeVisible();
    const first = page.locator(`.react-flow__node[data-id="${a}"]`);
    await first.click({ modifiers: ['Shift'], position: { x: 35, y: 25 } });
    const handle = page.locator('.glue-resize.bottom-left');
    const h = (await handle.boundingBox())!;
    await page.mouse.move(h.x + 7, h.y + 7); await page.mouse.down();
    await page.mouse.move(h.x + 7, h.y + 43, { steps: 10 }); await page.mouse.up();
    await expect.poll(async () => Math.round((await first.boundingBox())!.height)).toBe(196);
    await page.screenshot({ path: 'test-results/glue-stitched.png' });
    await page.getByRole('button', { name: '解除粘连', exact: true }).click();
    await expect(page.locator('.glue-stitch')).toHaveCount(0);
    await expect(page.locator('.semantic-edge-path')).toHaveCount(1);
  } finally { await request.delete(`/api/nodes/${a}`); await request.delete(`/api/nodes/${b}`); }
});

