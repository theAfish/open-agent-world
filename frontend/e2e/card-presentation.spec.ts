import { expect, test } from '@playwright/test';

test('shared collection faces and portrait canvas previews', async ({ page, request }) => {
  await page.goto('/');
  const start = page.getByRole('button', { name: 'Start blank', exact: true });
  await expect(start).toBeVisible();
  await start.click();
  await expect(start).not.toBeVisible();
  await page.getByRole('button', { name: 'Open Pack and Card Library' }).click();
  const library = page.getByRole('dialog', { name: 'Pack & Card Library' });
  const pack = library.getByRole('article', { name: 'Core essentials', exact: true });
  await pack.getByRole('button').click();
  await expect(library.getByRole('status')).toContainText('Core essentials opened');
  await pack.getByRole('button', { name: 'View cards in Core essentials' }).click();
  await library.getByLabel('Search cards', { exact: true }).fill('Agent');
  const inspect = library.getByRole('button', { name: 'Inspect Agent', exact: true });
  await expect(inspect.locator('.card-face')).toBeVisible();
  await inspect.click();
  await page.screenshot({ path: '../.tmp/card-presentation-library.png' });
  await library.getByRole('button', { name: 'Add Agent to deck', exact: true }).click();
  await library.getByRole('button', { name: 'Close Library' }).click();
  const tray = page.getByRole('complementary', { name: 'Active card deck' });
  await tray.hover();
  const deckCard = tray.getByRole('button', { name: 'Place Agent', exact: true });
  await expect(deckCard.locator('.card-face')).toBeVisible();
  await expect(deckCard.locator('.palette-item')).not.toContainText('Place');
  await deckCard.click();
  // Resolve the instance through the real API, independently of its generated name.
  const nodes = await (await request.get('/api/nodes')).json();
  const agent = nodes.find((item: { type: string }) => item.type === 'agent');
  const card = page.locator(`[data-card-id="${agent.id}"]`);
  if (await card.getAttribute('data-surface-level') === 'node') await card.getByRole('button', { name: /^Expand / }).click();
  await expect(card).toHaveAttribute('data-surface-level', 'preview');
  await expect(card).toHaveCSS('width', '224px');
  await expect(card).toHaveCSS('height', '300px');
  await expect(card.locator('.node-preview-content')).not.toContainText('connections');
  const before = (await card.boundingBox())!;
  await page.mouse.move(before.x + 80, before.y + 35);
  await page.mouse.down();
  await page.mouse.move(before.x + 430, before.y - 65, { steps: 15 });
  await page.mouse.up();
  await expect.poll(async () => (await card.boundingBox())!.x - before.x).toBeGreaterThan(300);
  // Check the actual SVG start point against each visible CSS corner while dragging.
  for (const [sx, sy] of [[1, 1], [1, -1], [-1, -1], [-1, 1]]) {
    const box = (await card.boundingBox())!;
    const center = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
    const handle = (await card.locator('.semantic-handle--right').boundingBox())!;
    await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2);
    await page.mouse.down();
    await page.mouse.move(center.x + sx * box.width * .8, center.y + sy * box.height * .8, { steps: 12 });
    const line = page.locator('.semantic-connection-path');
    await expect(line).toBeVisible();
    const endpoint = await line.evaluate(element => {
      const path = element as SVGPathElement;
      const point = path.getPointAtLength(0);
      return { x: new DOMPoint(point.x, point.y).matrixTransform(path.getScreenCTM()!).x,
        y: new DOMPoint(point.x, point.y).matrixTransform(path.getScreenCTM()!).y };
    });
    const radius = await card.evaluate(element => parseFloat(getComputedStyle(element).borderTopLeftRadius));
    const corner = { x: center.x + sx * (box.width / 2 - radius), y: center.y + sy * (box.height / 2 - radius) };
    expect(Math.abs(Math.hypot(endpoint.x - corner.x, endpoint.y - corner.y) - radius)).toBeLessThan(.7);
    await page.mouse.up();
  }
  await tray.hover();
  await page.screenshot({ path: '../.tmp/card-presentation-canvas.png' });
  await card.getByRole('heading').click();
  await expect(card).toHaveAttribute('data-surface-level', 'inspector');
  await card.getByRole('button', { name: /^Close .* inspector$/ }).click();
  await expect(card).toHaveAttribute('data-surface-level', 'preview');
});
