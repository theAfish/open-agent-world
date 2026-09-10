import { expect, test } from '@playwright/test';

test('nested knowledge backgrounds never change world pixels or share SVG resources', async ({ page, request }) => {
  await page.setViewportSize({ width: 3000, height: 1000 });
  const created: string[] = [];
  try {
    for (const x of [100, 1300]) {
      const response = await request.post('/api/nodes', { data: { type: 'matcreator.kdg', name: 'Background isolation check', position: { x, y: 100 } } });
      expect(response.status()).toBe(201);
      created.push((await response.json()).id);
    }
    await page.goto('/');
    const world = page.locator('.world-canvas > .react-flow');
    await expect(world.locator(':scope > .react-flow__background')).toBeVisible();
    await expect(page.locator(`[data-card-id="${created[0]}"]`)).toBeVisible();
    const clip = { x: 2800, y: 200, width: 150, height: 150 };
    const baseline = await page.screenshot({ clip, path: '../.outputs/kdg-background-before.png' });
    const checkBackground = async () => {
      const references = await page.locator('svg.react-flow__background').evaluateAll(backgrounds => backgrounds.map(background => {
        const pattern = background.querySelector('pattern')!;
        const rect = background.querySelector('rect')!;
        return { id: pattern.id, world: !background.closest('.kdg-canvas'), ownReference: document.getElementById(pattern.id) === pattern, fill: rect.getAttribute('fill') };
      }));
      const unchanged = (await page.screenshot({ clip, path: '../.outputs/kdg-background-after.png' })).equals(baseline);
      expect(unchanged).toBe(true);
      expect(references.every(reference => reference.ownReference)).toBe(true);
      expect(new Set(references.map(reference => reference.id)).size).toBe(references.length);
      await expect(page.locator('.kdg-canvas .contour-chunk')).toHaveCount(0);
      expect(await page.locator('.contour-chunk').evaluateAll(chunks => chunks.length > 0 && chunks.every(chunk => chunk.closest('.react-flow')?.id === 'oaw-world-map'))).toBe(true);
    };
    await expect(page.getByRole('region', { name: 'Background isolation check workspace' }).first().locator('.react-flow__background')).toBeVisible();
    await expect(page.getByRole('region', { name: 'Background isolation check workspace' }).last().locator('.react-flow__background')).toBeVisible();
    await checkBackground();
    const map = page.locator(`[data-card-id="${created[1]}"] .kdg-canvas`);
    await map.getByRole('button', { name: /zoom in/i }).click();
    await expect.poll(async () => (await page.screenshot({ clip })).equals(baseline)).toBe(true);
    await checkBackground();
    for (const id of [...created].reverse()) {
      await request.delete(`/api/nodes/${id}`);
      await expect(page.locator(`[data-card-id="${id}"]`)).toHaveCount(0);
      await checkBackground();
    }
  } finally {
    for (const id of created.reverse()) await request.delete(`/api/nodes/${id}`);
  }
});
