import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { resetTutorialProfile } from './tutorial-profile';

async function presentation(request: APIRequestContext, levels: Record<string, string>, zoom = 1) {
  await resetTutorialProfile(request);
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw-node-surfaces-v1': JSON.stringify({ state: { surfaceLevels: levels }, version: 3 }),
      'oaw-canvas-viewport-v1': JSON.stringify({ state: { viewport: { x: 0, y: 0, zoom, width: 1800, height: 1200 } }, version: 0 }),
    },
  } })).ok()).toBe(true);
}

async function dragCorner(page: Page, id: string, corner: string, dx: number, dy: number, finish = true) {
  const handle = page.locator(`[data-resize-node="${id}"][data-resize-corner="${corner}"]`);
  await expect(handle).toBeVisible();
  const rect = (await handle.boundingBox())!;
  const x = rect.x + rect.width / 2, y = rect.y + rect.height / 2;
  await page.mouse.move(x, y); await page.mouse.down();
  await page.mouse.move(x + dx, y + dy, { steps: 10 });
  if (finish) await page.mouse.up();
}

test.beforeEach(async ({ page }) => {
  await page.setViewportSize({ width: 1800, height: 1200 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
});

test('details use all four arcs and retain position and size after collapse and reload', async ({ page, request }) => {
  const card = await (await request.post('/api/nodes', { data: { type: 'text', name: 'Resize details', position: { x: 700, y: 500 } } })).json();
  try {
    await presentation(request, { [card.id]: 'inspector' }, .8);
    await page.goto('/');
    const surface = page.locator(`.world-card[data-card-id="${card.id}"]`);
    await surface.locator('.card-eyebrow').click();
    await expect(page.locator(`[data-resize-node="${card.id}"]`)).toHaveCount(4);
    for (const corner of ['top-left', 'top-right', 'bottom-left', 'bottom-right']) {
      const before = (await surface.boundingBox())!;
      const left = corner.endsWith('left'), top = corner.startsWith('top');
      await dragCorner(page, card.id, corner, left ? -40 : 40, top ? -24 : 24);
      await expect.poll(async () => (await surface.boundingBox())!.width).toBeCloseTo(before.width + 40, 0);
      const after = (await surface.boundingBox())!;
      expect(after.height).toBeCloseTo(before.height + 24, 0);
      expect(left ? after.x + after.width : after.x).toBeCloseTo(left ? before.x + before.width : before.x, 0);
      expect(top ? after.y + after.height : after.y).toBeCloseTo(top ? before.y + before.height : before.y, 0);
    }
    const resized = (await surface.boundingBox())!;
    await page.screenshot({ path: 'test-results/details-resize-arcs.png' });
    await surface.getByRole('button', { name: 'Close Resize details inspector' }).click();
    await expect(surface).toHaveAttribute('data-surface-level', 'preview');
    await expect(page.locator(`[data-resize-node="${card.id}"]`)).toHaveCount(0);
    await surface.locator('.card-eyebrow').click();
    await expect.poll(async () => (await surface.boundingBox())!.width).toBeCloseTo(resized.width, 0);
    await expect.poll(async () => {
      const profile = await (await request.get('/api/application')).json();
      return JSON.parse(profile.values['oaw-node-surfaces-v1']).state.surfaceSizes[card.id]?.inspector;
    }).toEqual({ width: 638, height: 690 });
    await page.reload();
    await expect.poll(async () => (await surface.boundingBox())!.width).toBeCloseTo(resized.width, 0);
    const restored = (await surface.boundingBox())!;
    expect(restored.x).toBeCloseTo(resized.x, 0);
    expect(restored.y).toBeCloseTo(resized.y, 0);
    expect(restored.height).toBeCloseTo(resized.height, 0);
  } finally { await request.delete(`/api/nodes/${card.id}`); }
});

test('Escape and pointer cancellation restore the full details rectangle without saving', async ({ page, request }) => {
  const card = await (await request.post('/api/nodes', { data: { type: 'text', name: 'Cancel resize', position: { x: 700, y: 500 } } })).json();
  try {
    await presentation(request, { [card.id]: 'inspector' });
    await page.goto('/');
    const surface = page.locator(`.world-card[data-card-id="${card.id}"]`);
    await surface.locator('.card-eyebrow').click();
    const before = (await surface.boundingBox())!;
    for (const cancellation of ['escape', 'pointercancel', 'lostpointercapture']) {
      await dragCorner(page, card.id, 'top-left', -70, -50, false);
      await expect.poll(async () => (await surface.boundingBox())!.width).toBeCloseTo(before.width + 70, 0);
      if (cancellation === 'escape') await page.keyboard.press('Escape');
      else await page.locator(`[data-resize-node="${card.id}"][data-resize-corner="top-left"]`).dispatchEvent(cancellation);
      await page.mouse.up();
      await expect.poll(async () => (await surface.boundingBox())!.width).toBeCloseTo(before.width, 0);
      const after = (await surface.boundingBox())!;
      expect(after.x).toBeCloseTo(before.x, 0);
      expect(after.y).toBeCloseTo(before.y, 0);
      expect(after.height).toBeCloseTo(before.height, 0);
      await expect(surface).toHaveAttribute('data-surface-level', 'inspector');
      expect((await (await request.get(`/api/nodes/${card.id}`)).json()).position).toEqual(card.position);
    }
  } finally { await request.delete(`/api/nodes/${card.id}`); }
});

test('Legion top-left resizing keeps members stationary and supports undo and reload', async ({ page, request }) => {
  const ids: string[] = [];
  try {
    for (const x of [600, 1000]) {
      const member = await (await request.post('/api/nodes', { data: { type: 'text', name: `Member ${x}`, position: { x, y: 500 } } })).json();
      ids.push(member.id);
    }
    const response = await request.post('/api/legion-groups', { data: { name: 'Resizable Legion', node_ids: ids } });
    expect(response.ok()).toBe(true);
    const group = (await response.json()).find((node: { type: string }) => node.type === 'legion');
    const members = await Promise.all(ids.map(async id => (await request.get(`/api/nodes/${id}`)).json()));
    await presentation(request, Object.fromEntries(ids.map(id => [id, 'preview'])), .8);
    await page.goto('/');
    const frame = page.locator(`.container-frame[data-card-id="${group.id}"]`);
    const memberSurfaces = ids.map(id => page.locator(`.world-card[data-card-id="${id}"]`));
    await frame.locator('.container-header').click({ position: { x: 140, y: 20 } });
    await expect(page.locator(`[data-resize-node="${group.id}"]`)).toHaveCount(4);
    const before = (await frame.boundingBox())!;
    const positions = await Promise.all(memberSurfaces.map(surface => surface.boundingBox()));
    await dragCorner(page, group.id, 'top-left', -80, -48, false);
    for (let i = 0; i < memberSurfaces.length; i++) {
      const current = (await memberSurfaces[i].boundingBox())!;
      expect(current.x).toBeCloseTo(positions[i]!.x, 0); expect(current.y).toBeCloseTo(positions[i]!.y, 0);
    }
    await page.mouse.up();
    await expect.poll(async () => (await frame.boundingBox())!.width).toBeCloseTo(before.width + 80, 0);
    for (let i = 0; i < ids.length; i++) {
      expect((await (await request.get(`/api/nodes/${ids[i]}`)).json()).position).toEqual(members[i].position);
      const current = (await memberSurfaces[i].boundingBox())!;
      expect(current.x).toBeCloseTo(positions[i]!.x, 0); expect(current.y).toBeCloseTo(positions[i]!.y, 0);
    }
    await page.keyboard.press('Control+z');
    await expect.poll(async () => (await frame.boundingBox())!.width).toBeCloseTo(before.width, 0);
    await page.keyboard.press('Control+Shift+z');
    await expect.poll(async () => (await frame.boundingBox())!.width).toBeCloseTo(before.width + 80, 0);
    await page.reload();
    await expect.poll(async () => (await frame.boundingBox())!.width).toBeCloseTo(before.width + 80, 0);
    expect((await request.post('/api/nodes/batch-delete', { data: { node_ids: [group.id, ...ids] } })).ok()).toBe(true);
  } finally { for (const id of ids) await request.delete(`/api/nodes/${id}`); }
});

test('a partial glue seam keeps its edge fixed when the dragged corner reaches the seam', async ({ page, request }) => {
  const ids: string[] = [];
  try {
    for (const x of [500, 950]) {
      const card = await (await request.post('/api/nodes', { data: { type: 'text', name: `Partial seam ${x}`, position: { x, y: 450 } } })).json();
      ids.push(card.id);
    }
    const [a, b] = ids;
    const glue = await (await request.get('/api/canvas/glue')).json();
    expect((await request.patch('/api/canvas/glue', { data: { revision: glue.revision, boxes: {
      [a]: { x: 250, y: 150, width: 438, height: 570, level: 'inspector' },
      [b]: { x: 688, y: 300, width: 224, height: 300, level: 'preview' },
    }, bonds: [{ a, b, side: 'right' }] } })).ok()).toBe(true);
    await presentation(request, { [a]: 'inspector', [b]: 'preview' });
    await page.goto('/');
    const surface = page.locator(`.world-card[data-card-id="${a}"]`);
    await expect(page.locator(`.react-flow__node[data-id="${a}"]`)).toHaveClass(/is-glued/);
    await surface.locator('.card-eyebrow').click({ modifiers: ['Shift'] });
    await expect(page.locator(`[data-resize-node="${a}"]`)).toHaveCount(4);
    const before = (await surface.boundingBox())!;
    await dragCorner(page, a, 'bottom-right', 100, -200, false);
    await expect.poll(async () => (await surface.boundingBox())!.height).toBeCloseTo(before.height - 200, 0);
    await page.mouse.up();
    await expect.poll(async () => (await (await request.get('/api/canvas/glue')).json()).boxes[a].height).toBe(370);
    const after = (await surface.boundingBox())!;
    expect(after.width).toBeCloseTo(before.width, 0);
    expect(after.x).toBeCloseTo(before.x, 0);
    expect((await (await request.get(`/api/nodes/${b}`)).json()).position).toEqual({ x: 950, y: 450 });
    await expect(page.locator(`[data-resize-node="${a}"][data-resize-corner="bottom-right"]`)).toHaveCount(0);
  } finally { for (const id of ids) await request.delete(`/api/nodes/${id}`); }
});

test('equipped details resize without changing equipment ownership or stored placement', async ({ page, request }) => {
  const owner = await (await request.post('/api/nodes', { data: { type: 'agent', name: 'Resize owner', position: { x: 300, y: 250 } } })).json();
  try {
    const item = await (await request.post('/api/nodes', { data: { type: 'oaw.barracks.summoner', equipment: { owner_id: owner.id, relationship: 'oaw.barracks.use' } } })).json();
    await presentation(request, { [owner.id]: 'preview', [item.id]: 'inspector' });
    await page.goto('/');
    await page.getByRole('button', { name: 'Equipment for Resize owner', exact: true }).click();
    const surface = page.locator(`.world-card[data-card-id="${item.id}"]`);
    await surface.locator('.card-eyebrow').click();
    const before = (await surface.boundingBox())!;
    await dragCorner(page, item.id, 'top-left', -60, -40);
    await expect.poll(async () => (await surface.boundingBox())!.width).toBeCloseTo(before.width + 60, 0);
    const after = (await surface.boundingBox())!;
    expect(after.x + after.width).toBeCloseTo(before.x + before.width, 0);
    expect(after.y + after.height).toBeCloseTo(before.y + before.height, 0);
    const saved = await (await request.get(`/api/nodes/${item.id}`)).json();
    expect(saved.equipment).toEqual(item.equipment);
    expect(saved.position).toEqual(item.position);
    await expect(page.locator(`[data-equipment-origin="${item.id}"]`)).toHaveCSS('height', '40px');
  } finally { await request.post('/api/nodes/batch-delete', { data: { node_ids: [owner.id] } }); }
});

test('touch uses the same details corner gesture', async ({ page, request }) => {
  const card = await (await request.post('/api/nodes', { data: { type: 'text', name: 'Touch resize', position: { x: 700, y: 500 } } })).json();
  try {
    await presentation(request, { [card.id]: 'inspector' });
    await page.goto('/');
    const surface = page.locator(`.world-card[data-card-id="${card.id}"]`);
    await surface.locator('.card-eyebrow').click();
    const before = (await surface.boundingBox())!;
    const handle = (await page.locator(`[data-resize-node="${card.id}"][data-resize-corner="top-right"]`).boundingBox())!;
    const touch = await page.context().newCDPSession(page);
    const x = handle.x + handle.width / 2, y = handle.y + handle.height / 2;
    await touch.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
    for (let i = 1; i <= 5; i++) await touch.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: x + i * 12, y: y - i * 8 }] });
    await touch.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await expect.poll(async () => (await surface.boundingBox())!.width).toBeCloseTo(before.width + 60, 0);
    const after = (await surface.boundingBox())!;
    expect(after.height).toBeCloseTo(before.height + 40, 0);
    expect(after.y + after.height).toBeCloseTo(before.y + before.height, 0);
    await touch.detach();
  } finally { await request.delete(`/api/nodes/${card.id}`); }
});

test('plugin workspace containers share the corner controller and persist their origin', async ({ page, request }) => {
  const response = await request.post('/api/nodes', { data: { type: 'matcreator.kdg', name: 'Resize graph', position: { x: 250, y: 180 }, size: { width: 1000, height: 650 } } });
  expect(response.ok()).toBe(true);
  const card = await response.json();
  try {
    await presentation(request, {});
    await page.goto('/');
    const frame = page.locator(`.container-frame[data-card-id="${card.id}"]`);
    await frame.locator('.container-header strong').click();
    await expect(page.locator(`[data-resize-node="${card.id}"]`)).toHaveCount(4);
    const before = (await frame.boundingBox())!;
    await dragCorner(page, card.id, 'top-left', -60, -40);
    await expect.poll(async () => (await frame.boundingBox())!.width).toBeCloseTo(before.width + 60, 0);
    await expect.poll(async () => (await (await request.get(`/api/nodes/${card.id}`)).json()).position).toEqual({ x: 190, y: 140 });
    await page.reload();
    await expect.poll(async () => (await frame.boundingBox())!.width).toBeCloseTo(before.width + 60, 0);
    expect((await frame.boundingBox())!.x).toBeCloseTo(before.x - 60, 0);
    expect((await frame.boundingBox())!.y).toBeCloseTo(before.y - 40, 0);
  } finally { expect((await request.delete(`/api/nodes/${card.id}`)).ok()).toBe(true); }
});
