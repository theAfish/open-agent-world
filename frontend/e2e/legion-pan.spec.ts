import { expect, test } from '@playwright/test';

test('Legion blank space pans the canvas and labels paint above its background', async ({ page, request }) => {
  await page.setViewportSize({ width: 1800, height: 1100 });
  await page.addInitScript(() => {
    localStorage.setItem('oaw.locale', 'en');
    localStorage.setItem('oaw-onboarding-v1', JSON.stringify({ state: { status: 'skipped' }, version: 1 }));
  });
  const ids: string[] = [];
  try {
    for (const [name, x] of [['Pan first', 500], ['Pan second', 1000]] as const) {
      const response = await request.post('/api/nodes', { data: { type: 'agent', name, position: { x, y: 250 } } });
      expect(response.ok()).toBeTruthy();
      ids.push((await response.json()).id);
    }
    expect((await request.post('/api/edges', { data: { source: ids[0], target: ids[1], relationship: 'communicate' } })).ok()).toBeTruthy();
    const groupResponse = await request.post('/api/legion-groups', { data: { name: 'Pan team', node_ids: ids } });
    expect(groupResponse.ok()).toBeTruthy();
    const [createdGroup] = await groupResponse.json();
    const groupId: string = createdGroup.id;
    ids.push(groupId);
    const profile = await (await request.get('/api/application')).json();
    expect((await request.patch('/api/application/preferences', { data: {
      profile_id: profile.profile_id, generation: profile.generation, changes: {
        'oaw.locale': 'en',
        'oaw-onboarding-v1': JSON.stringify({ state: { status: 'skipped' }, version: 1 }),
        'oaw-canvas-viewport-v1': JSON.stringify({ version: 0, state: {
          viewport: { x: 0, y: 0, zoom: 1, width: 1800, height: 1100 }, mapPins: [],
        } }),
      },
    } })).ok()).toBeTruthy();
    await page.goto('/');
    const group = page.locator(`[data-card-type="legion"][data-card-id="${groupId}"]`);
    await expect(group).toBeVisible();
    await page.getByRole('button', { name: 'Fit view', exact: true }).click();
    await expect.poll(async () => {
      const bounds = await group.boundingBox();
      const canvas = await page.locator('#oaw-world-map').boundingBox();
      return !!bounds && !!canvas && bounds.x >= canvas.x && bounds.y >= canvas.y
        && bounds.x + bounds.width <= canvas.x + canvas.width
        && bounds.y + bounds.height <= canvas.y + canvas.height;
    }).toBe(true);
    const position = async (id: string) => (await (await request.get(`/api/nodes/${id}`)).json()).position;
    const before = await Promise.all(ids.map(position));
    const viewport = page.locator('#oaw-world-map > .react-flow__renderer > .react-flow__pane > .react-flow__viewport');
    const transform = await viewport.getAttribute('style');
    const box = (await group.boundingBox())!;
    const start = { x: box.x + box.width - 70, y: box.y + box.height - 80 };
    expect(await page.evaluate(p => document.elementFromPoint(p.x, p.y)?.getAttribute('class'), start)).toContain('react-flow__pane');
    await page.mouse.move(start.x, start.y);
    await page.mouse.down();
    await page.mouse.move(start.x - 70, start.y - 45, { steps: 8 });
    await page.mouse.up();
    await expect(viewport).not.toHaveAttribute('style', transform!);
    expect(await Promise.all(ids.map(position))).toEqual(before);
    // Header selection must not change the background/label paint order.
    await group.locator('.container-header').click({ position: { x: 30, y: 30 } });
    await expect(group).toHaveClass(/is-selected/);
    const label = page.locator('#oaw-world-map .semantic-edge-label').first();
    await expect(label).toBeVisible();
    expect(await label.evaluate(el => {
      const box = el.getBoundingClientRect();
      const frame = document.querySelector('.legion-container') as HTMLElement;
      el.setAttribute('style', `${el.getAttribute('style')};pointer-events:auto`);
      frame.style.pointerEvents = 'auto';
      const stack = document.elementsFromPoint(box.x + box.width / 2, box.y + box.height / 2);
      const above = stack.indexOf(el) >= 0 && stack.indexOf(el) < stack.indexOf(frame);
      el.style.pointerEvents = '';
      frame.style.pointerEvents = '';
      return above;
    })).toBe(true);
    await page.screenshot({ path: '../.open-agent-world/legion-pan-light.png' });
    const header = (await group.locator('.container-header').boundingBox())!;
    await page.mouse.move(header.x + 30, header.y + 30);
    await page.mouse.down();
    await page.mouse.move(header.x + 80, header.y + 60, { steps: 8 });
    await page.mouse.up();
    await expect.poll(() => position(groupId)).not.toEqual(before[2]);
    await group.getByRole('button', { name: 'Legion settings', exact: true }).click();
    const settings = page.getByRole('complementary', { name: 'Legion settings', exact: true });
    await settings.getByLabel('Enable shared team settings').check();
    await settings.getByLabel('Team instruction', { exact: true }).fill('Still editable');
    await page.getByRole('button', { name: 'Use dark theme' }).click();
    await page.screenshot({ path: '../.open-agent-world/legion-pan-dark.png' });
  } finally {
    await request.post('/api/nodes/batch-delete', { data: { node_ids: ids } });
  }
});
