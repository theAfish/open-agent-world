import { expect, test } from '@playwright/test';

test('declared bottom controls reserve space and retain drag, focus and workspace actions', async ({ page, request }) => {
  await page.route('**/api/catalog', async route => {
    const response = await route.fetch();
    const catalog = await response.json();
    const board = catalog.node_types.find((item: { id: string }) => item.id === 'oaw.tasks');
    board.presentation = { ...board.presentation, initial: 'inspector', content_controls: 'bottom',
      sizes: { inspector: { width: 640, height: 400 }, workspace: { width: 800, height: 560 } } };
    await route.fulfill({ response, json: catalog });
  });
  const created = await request.post('/api/nodes', { data: {
    type: 'oaw.tasks', name: 'Bottom controls', position: { x: 300, y: 160 },
  } });
  expect(created.status()).toBe(201);
  const node = await created.json();
  try {
    await page.goto('/');
    const card = page.locator(`.world-card[data-card-id="${node.id}"]`);
    await expect(card).toHaveAttribute('data-content-controls', 'bottom');
    const controls = card.locator(':scope > .content-surface-controls');
    const content = card.locator(':scope > .node-inspector-content');
    await expect(controls).toBeVisible();
    await expect.poll(async () => {
      const [body, edge] = await Promise.all([content.boundingBox(), controls.boundingBox()]);
      return body && edge ? Math.abs(body.y + body.height - edge.y) : Infinity;
    }).toBeLessThan(1);
    const before = (await card.boundingBox())!;
    const grip = (await controls.locator('.content-surface-grip').boundingBox())!;
    await page.mouse.move(grip.x + grip.width / 2, grip.y + grip.height / 2);
    await page.mouse.down();
    await page.mouse.move(grip.x + grip.width / 2 + 60, grip.y + grip.height / 2 + 30, { steps: 8 });
    await page.mouse.up();
    await expect.poll(async () => (await card.boundingBox())!.x - before.x).toBeGreaterThan(40);
    const details = controls.getByRole('button', { name: 'Card details', exact: true });
    await details.click();
    const menu = page.getByRole('dialog', { name: 'Card details', exact: true });
    await expect(menu).toHaveAttribute('data-placement', 'top');
    await expect(menu.getByRole('button', { name: 'Remove Bottom controls' })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(menu).not.toBeVisible();
    await expect(details).toBeFocused();
    await controls.getByRole('button', { name: 'Open workspace', exact: true }).click();
    const workspace = page.getByRole('dialog', { name: 'Bottom controls workspace', exact: true });
    await expect(workspace).toHaveAttribute('data-content-controls', 'bottom');
    await expect.poll(async () => {
      const [body, edge] = await Promise.all([
        workspace.locator(':scope > .workspace-content').boundingBox(),
        workspace.locator(':scope > .content-surface-controls').boundingBox(),
      ]);
      return body && edge ? Math.abs(body.y + body.height - edge.y) : Infinity;
    }).toBeLessThan(1);
    await workspace.getByRole('button', { name: 'Close workspace', exact: true }).click();
    await expect(card).toHaveAttribute('data-surface-level', 'inspector');
  } finally {
    await request.delete(`/api/nodes/${node.id}`);
  }
});
