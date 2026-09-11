import { expect, test } from '@playwright/test';

test('live card updates preserve an active marquee and the completed selection', async ({ page, request }) => {
  const ids: string[] = [];
  try {
    for (const x of [400, 760]) {
      const response = await request.post('/api/nodes', { data: { type: 'text', name: 'Live selection', position: { x, y: 350 } } });
      expect(response.ok()).toBe(true);
      ids.push((await response.json()).id);
    }
    await page.goto('/');
    const cards = ids.map(id => page.locator(`[data-card-id="${id}"]`));
    for (const card of cards) await expect(card).toBeVisible();
    const a = (await cards[0].boundingBox())!;
    const b = (await cards[1].boundingBox())!;
    await page.keyboard.down('Shift');
    await page.mouse.move(a.x - 20, a.y - 20);
    await page.mouse.down();
    await page.mouse.move(b.x + b.width + 20, b.y + b.height + 20, { steps: 15 });
    for (const card of cards) await expect(card).toHaveClass(/is-selected/);

    for (let revision = 0; revision < 3; revision++) {
      const name = `Live selection revision ${revision}`;
      const response = await request.patch(`/api/nodes/${ids[0]}`, { data: { name } });
      expect(response.ok()).toBe(true);
      await expect(cards[0]).toHaveAttribute('aria-label', `Text file ${name}`);
      await page.mouse.move(b.x + b.width + 25 + revision, b.y + b.height + 25, { steps: 3 });
      for (const card of cards) await expect(card).toHaveClass(/is-selected/);
    }
    await page.mouse.up();
    await page.keyboard.up('Shift');
    const response = await request.patch(`/api/nodes/${ids[1]}`, { data: { name: 'Updated after release' } });
    expect(response.ok()).toBe(true);
    await expect(cards[1]).toHaveAttribute('aria-label', 'Text file Updated after release');
    for (const card of cards) await expect(card).toHaveClass(/is-selected/);
    await expect(page.getByTestId('legion-selection-bar')).toContainText('2 selected');
    const selection = page.locator('#oaw-world-map .react-flow__nodesselection-rect');
    await expect(selection).toBeVisible();
    const before = (await cards[0].boundingBox())!;
    await page.mouse.move(before.x + 30, before.y + 30);
    await page.mouse.down();
    await page.mouse.move(before.x + 90, before.y + 70, { steps: 12 });
    for (const card of cards) {
      await expect(card).toHaveClass(/is-selected/);
      await expect(card).toHaveCSS('outline-style', 'solid');
      await expect(card).toHaveCSS('outline-width', '2px');
    }
    await expect(selection).toBeVisible();
    await expect.poll(async () => (await cards[0].boundingBox())!.x - before.x).toBeGreaterThan(40);
    await page.mouse.up();
    for (const card of cards) await expect(card).toHaveClass(/is-selected/);
    await expect(selection).toBeVisible();
    // A direct node drag has no group overlay to supply the selection cue.
    await page.mouse.click(a.x - 25, a.y - 25);
    const single = (await cards[0].boundingBox())!;
    await page.mouse.move(single.x + 30, single.y + 30);
    await page.mouse.down();
    await page.mouse.move(single.x + 85, single.y + 65, { steps: 12 });
    await expect(cards[0]).toHaveClass(/is-selected/);
    await expect(cards[0]).toHaveCSS('outline-style', 'solid');
    await expect(cards[1]).not.toHaveClass(/is-selected/);
    await page.mouse.up();
    await expect(cards[0]).toHaveCSS('outline-style', 'solid');
  } finally {
    for (const id of ids) await request.delete(`/api/nodes/${id}`);
  }
});

test('restarting a marquee inside the previous selection keeps tracking over controls', async ({ page, request }) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  const ids: string[] = [];
  try {
    for (const x of [400, 850]) {
      const response = await request.post('/api/nodes', { data: { type: 'text', name: 'Selection target', position: { x, y: x === 400 ? 350 : 450 } } });
      expect(response.ok()).toBe(true);
      ids.push((await response.json()).id);
    }
    await page.goto('/');
    const cards = ids.map(id => page.locator(`[data-card-id="${id}"]`));
    await expect(cards[0]).toBeVisible();
    await expect(cards[1]).toBeVisible();
    const a = (await cards[0].boundingBox())!;
    const b = (await cards[1].boundingBox())!;
    await page.keyboard.down('Shift');
    await page.mouse.move(a.x - 25, a.y - 25);
    await page.mouse.down();
    await page.mouse.move(b.x + b.width + 25, b.y + b.height + 25, { steps: 15 });
    await page.mouse.up();
    await page.keyboard.up('Shift');
    const selected = page.locator('#oaw-world-map .react-flow__nodesselection-rect');
    await expect(selected).toBeVisible();
    const start = { x: a.x + a.width + 30, y: a.y + 20 };
    await page.mouse.move(start.x, start.y);
    await page.keyboard.down('Shift');
    await page.mouse.down();
    await page.mouse.move(start.x + 10, start.y + 10, { steps: 2 });
    await expect(page.locator('#oaw-world-map .react-flow__selection')).toBeVisible();
    await page.mouse.move(b.x + b.width + 25, b.y + b.height + 25, { steps: 15 });
    await page.mouse.up();
    await page.keyboard.up('Shift');
    await expect(cards[1]).toHaveClass(/is-selected/);
    await expect(cards[0]).not.toHaveClass(/is-selected/);
    await expect(page.locator('#oaw-world-map .react-flow__selection')).toHaveCount(0);
    // Start on the remaining selection and release above a sibling toolbar.
    // Losing capture leaves the marquee stuck because the pane misses pointerup.
    await page.keyboard.down('Shift');
    await page.mouse.move(b.x + 10, b.y + 10);
    await page.mouse.down();
    await page.mouse.move(b.x + 20, b.y + 20, { steps: 2 });
    const toolbar = (await page.getByRole('button', { name: 'Open settings', exact: true }).boundingBox())!;
    await page.mouse.move(toolbar.x + toolbar.width / 2, toolbar.y + toolbar.height / 2, { steps: 15 });
    await page.mouse.up();
    await page.keyboard.up('Shift');
    await expect(page.locator('#oaw-world-map .react-flow__selection')).toHaveCount(0);
  } finally {
    for (const id of ids) await request.delete(`/api/nodes/${id}`);
  }
});
