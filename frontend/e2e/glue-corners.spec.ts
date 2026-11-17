import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { resetTutorialProfile } from './tutorial-profile';

test.afterEach(async ({ request }) => {
  const world = await (await request.get('/api/world')).json();
  for (const node of world.nodes.filter((node: { id: string }) => node.id.startsWith('glue-corners-'))) {
    expect((await request.delete(`/api/nodes/${node.id}`)).ok()).toBe(true);
  }
});

async function seed(page: Page, request: APIRequestContext, corner = false) {
  await resetTutorialProfile(request);
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation, changes: {
    'oaw-canvas-viewport-v1': JSON.stringify({ state: { viewport: { x: 0, y: 0, zoom: 1, width: 1280, height: 800 } }, version: 0 }),
    'oaw-progressive-tutorials-v1': JSON.stringify({ state: { enabled: false }, version: 1 }),
  } } })).ok()).toBe(true);
  const prefix = `glue-corners-${Date.now()}`;
  const ids = { a: `${prefix}-a`, b: `${prefix}-b`, c: `${prefix}-c` };
  const boxes = {
    [ids.a]: { x: 180, y: 140, width: 224, height: corner ? 600 : 300, level: 'preview' },
    [ids.b]: { x: 404, y: 140, width: 224, height: 300, level: 'preview' },
  };
  for (const [id, position] of [[ids.a, { x: 244, y: 242 }], [ids.b, { x: 468, y: 242 }], [ids.c, { x: 864, y: 542 }]] as const) {
    expect((await request.post('/api/nodes', { data: { id, type: 'text', name: id, position } })).ok()).toBe(true);
  }
  const glue = await (await request.get('/api/canvas/glue')).json();
  expect((await request.patch('/api/canvas/glue', { data: { revision: glue.revision, boxes, bonds: [{ a: ids.a, b: ids.b, side: 'right' }] } })).ok()).toBe(true);
  await page.goto('/');
  const nodes = Object.fromEntries(Object.entries(ids).map(([key, id]) => [key, page.locator(`.react-flow__node[data-id="${id}"]`)]));
  await expect(nodes.a).toHaveClass(/is-glued/, { timeout: 20_000 });
  await expect(nodes.c).toBeVisible();
  return { ids, nodes, cleanup: async () => { for (const id of Object.values(ids)) await request.delete(`/api/nodes/${id}`); } };
}

test('glues both edges of an inside corner, keeps shadows and moves every peer after reload', async ({ page, request }) => {
  const { ids, nodes, cleanup } = await seed(page, request, true);
  try {
    await page.getByRole('button', { name: 'Glue', exact: true }).click();
    const source = (await nodes.c.boundingBox())!, target = (await nodes.b.boundingBox())!;
    await page.mouse.move(source.x + 35, source.y + 25); await page.mouse.down();
    // XYFlow starts tracking displacement after its 5px drag threshold.
    await page.mouse.move(source.x + 41, source.y + 25);
    const [approach] = await Promise.all([
      nodes.c.evaluate(async element => {
        const frames: { x: number; y: number }[] = [];
        const end = performance.now() + 300;
        while (performance.now() < end) {
          await new Promise(requestAnimationFrame);
          const { x, y } = element.getBoundingClientRect();
          frames.push({ x, y });
        }
        return frames;
      }),
      page.mouse.move(target.x + 8 + 41, target.y + target.height + 10 + 25),
    ]);
    await expect(page.locator('.glue-seam.is-preview')).toHaveCount(2);
    expect(approach.some(frame => frame.x > target.x + .5 && frame.x < target.x + 7.5
      && frame.y > target.y + target.height + .5 && frame.y < target.y + target.height + 9.5)).toBe(true);
    const aligned = async () => {
      const a = (await nodes.a.boundingBox())!, b = (await nodes.b.boundingBox())!, c = (await nodes.c.boundingBox())!;
      return Math.max(Math.abs(c.x - a.x - a.width), Math.abs(c.y - b.y - b.height));
    };
    // Both edges settle while held, without persisting a bond until release.
    await expect.poll(aligned).toBeLessThan(1);
    expect((await (await request.get('/api/canvas/glue')).json()).bonds).toHaveLength(1);
    await page.mouse.move(target.x + 12 + 41, target.y + target.height + 13 + 25);
    await expect.poll(aligned).toBeLessThan(1);
    // Leaving and re-entering uses raw pointer coordinates, avoiding snap lock-in.
    await page.mouse.move(target.x + 60 + 41, target.y + target.height + 55 + 25);
    await expect(page.locator('.glue-seam.is-preview')).toHaveCount(0);
    await expect.poll(async () => (await nodes.c.boundingBox())!.x).toBeCloseTo(target.x + 60, 0);
    await page.mouse.move(target.x + 8 + 41, target.y + target.height + 10 + 25);
    await expect.poll(aligned).toBeLessThan(1);
    await page.mouse.up();
    await expect.poll(async () => (await (await request.get('/api/canvas/glue')).json()).bonds.length).toBe(3);
    await expect.poll(async () => {
      const a = (await nodes.a.boundingBox())!, b = (await nodes.b.boundingBox())!, c = (await nodes.c.boundingBox())!;
      return Math.max(Math.abs(c.x - a.x - a.width), Math.abs(c.y - b.y - b.height));
    }).toBeLessThan(1);
    for (const node of Object.values(nodes)) {
      const shadows = await node.locator('.world-card').evaluate(element => getComputedStyle(element).boxShadow.split(/,(?![^(]*\))/));
      expect(shadows.every(shadow => shadow.includes('inset'))).toBe(true);
    }
    await expect(page.locator('.glue-shadow')).toHaveCount(1);
    await expect(page.locator('.glue-shadow rect')).toHaveCount(3);
    await expect(page.locator('.glue-shadow')).toHaveCSS('pointer-events', 'none');
    await page.screenshot({ path: 'test-results/glue-inside-corner.png' });
    await page.reload();
    await expect(nodes.c).toHaveClass(/is-glued/);
    const before = await Promise.all(Object.values(nodes).map(node => node.boundingBox()));
    const c = before[2]!;
    await page.mouse.move(c.x + 35, c.y + 25); await page.mouse.down();
    await page.mouse.move(c.x + 41, c.y + 25);
    await page.mouse.move(c.x + 71, c.y + 45, { steps: 10 }); await page.mouse.up();
    await expect.poll(async () => (await nodes.a.boundingBox())!.x - before[0]!.x).toBeCloseTo(30, 0);
    for (const [index, node] of Object.values(nodes).entries()) {
      expect((await node.boundingBox())!.y - before[index]!.y).toBeCloseTo(20, 0);
    }
    await page.getByRole('button', { name: 'Detach glue', exact: true }).click();
    await expect(nodes.c).not.toHaveClass(/is-glued/);
    await expect(page.locator('.glue-shadow rect')).toHaveCount(2);
    expect(await nodes.c.locator('.world-card').evaluate(element => getComputedStyle(element).boxShadow.split(/,(?![^(]*\))/)
      .some(shadow => !shadow.includes('inset')))).toBe(true);
    await expect.poll(async () => (await (await request.get('/api/canvas/glue')).json()).bonds).toEqual([{ a: ids.a, b: ids.b, side: 'right' }]);
  } finally { await cleanup(); }
});

test('side-by-side glue eases into flush top and bottom edges while held', async ({ page, request }) => {
  const { nodes, cleanup } = await seed(page, request);
  try {
    await page.getByRole('button', { name: 'Glue', exact: true }).click();
    const source = (await nodes.c.boundingBox())!, target = (await nodes.b.boundingBox())!;
    await page.mouse.move(source.x + 35, source.y + 25); await page.mouse.down();
    await page.mouse.move(source.x + 41, source.y + 25);
    await page.mouse.move(target.x + target.width + 8 + 41, target.y + 30 + 25);
    await expect(page.locator('.glue-seam.is-preview')).toHaveCount(1);
    await expect.poll(async () => (await nodes.c.boundingBox())!.y).toBeCloseTo(target.y + 30, 0);
    const [frames] = await Promise.all([
      nodes.c.evaluate(async element => {
        const frames: number[] = [];
        const end = performance.now() + 300;
        while (performance.now() < end) {
          await new Promise(requestAnimationFrame);
          frames.push(element.getBoundingClientRect().y);
        }
        return frames;
      }),
      page.mouse.move(target.x + target.width + 8 + 41, target.y + 9 + 25),
    ]);
    expect(frames.some(y => y > target.y + .5 && y < target.y + 8.5)).toBe(true);
    await expect.poll(async () => (await nodes.c.boundingBox())!.y).toBeCloseTo(target.y, 0);
    expect((await (await request.get('/api/canvas/glue')).json()).bonds).toHaveLength(1);
    await page.mouse.up();
    await expect(nodes.c).toHaveClass(/is-glued/);
    for (const theme of ['light', 'dark']) {
      await page.evaluate(theme => { document.documentElement.dataset.theme = theme; }, theme);
      await page.screenshot({ path: test.info().outputPath(`glue-shadows-${theme}.png`) });
    }
  } finally { await cleanup(); }
});

test('reduced motion snaps immediately and cancelling restores the original position', async ({ page, request }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const { nodes, cleanup } = await seed(page, request, true);
  try {
    await page.getByRole('button', { name: 'Glue', exact: true }).click();
    const source = (await nodes.c.boundingBox())!, target = (await nodes.b.boundingBox())!;
    await page.mouse.move(source.x + 35, source.y + 25); await page.mouse.down();
    await page.mouse.move(source.x + 41, source.y + 25);
    await page.mouse.move(target.x + 8 + 41, target.y + target.height + 10 + 25);
    await expect(page.locator('.glue-seam.is-preview')).toHaveCount(2);
    const snapped = (await nodes.c.boundingBox())!;
    expect(snapped.x).toBeCloseTo(target.x, 0);
    expect(snapped.y).toBeCloseTo(target.y + target.height, 0);
    await page.keyboard.press('Escape');
    await page.mouse.up();
    await expect(page.locator('.glue-seam.is-preview')).toHaveCount(0);
    await expect.poll(async () => (await nodes.c.boundingBox())!.x).toBeCloseTo(source.x, 0);
    expect((await (await request.get('/api/canvas/glue')).json()).bonds).toHaveLength(1);
    await expect(nodes.c).not.toHaveClass(/is-glued/);
  } finally { await cleanup(); }
});

test('dragging an unglued member of a mixed selection also moves its selected glued peers', async ({ page, request }) => {
  const { nodes, cleanup } = await seed(page, request);
  try {
    await nodes.a.click({ modifiers: ['ControlOrMeta'], position: { x: 35, y: 25 } });
    await nodes.c.click({ modifiers: ['ControlOrMeta'], position: { x: 35, y: 25 } });
    await expect(nodes.a).toHaveClass(/selected/);
    await expect(nodes.c).toHaveClass(/selected/);
    const before = await Promise.all(Object.values(nodes).map(node => node.boundingBox()));
    const c = before[2]!;
    await page.mouse.move(c.x + 35, c.y + 25); await page.mouse.down();
    await page.mouse.move(c.x + 41, c.y + 25);
    await page.mouse.move(c.x + 71, c.y + 45, { steps: 10 }); await page.mouse.up();
    await expect.poll(async () => (await nodes.b.boundingBox())!.x - before[1]!.x).toBeCloseTo(30, 0);
    await expect(nodes.c).not.toHaveClass(/is-glued/);
    await page.reload();
    await expect(nodes.a).toHaveClass(/is-glued/);
    await expect(nodes.c).not.toHaveClass(/is-glued/);
    expect((await nodes.b.boundingBox())!.x - before[1]!.x).toBeCloseTo(30, 0);
  } finally { await cleanup(); }
});

test('a failed position save does not persist the proposed glue or leave a false glued state', async ({ page, request }) => {
  const { ids, nodes, cleanup } = await seed(page, request);
  try {
    const before = await (await request.get('/api/canvas/glue')).json();
    await page.route('**/api/nodes/batch-update', route => route.fulfill({ status: 500, json: { detail: 'Position write failed' } }));
    const saves: unknown[] = [];
    page.on('request', req => { if (req.method() === 'PATCH' && req.url().endsWith('/api/canvas/glue')) saves.push(req.postDataJSON()); });
    await page.getByRole('button', { name: 'Glue', exact: true }).click();
    const source = (await nodes.c.boundingBox())!, target = (await nodes.b.boundingBox())!;
    await page.mouse.move(source.x + 35, source.y + 25); await page.mouse.down();
    await page.mouse.move(source.x + 41, source.y + 25);
    await page.mouse.move(target.x + target.width + 8 + 41, target.y + 25, { steps: 15 });
    await expect(page.locator('.glue-seam.is-preview')).toHaveCount(1);
    await page.mouse.up();
    await expect(page.getByText(/card positions? (?:was|were) not saved/)).toBeVisible();
    await expect(nodes.c).not.toHaveClass(/is-glued/);
    await expect.poll(async () => (await nodes.c.boundingBox())!.x).toBeCloseTo(source.x, 0);
    expect(saves).toEqual([]);
    expect(await (await request.get('/api/canvas/glue')).json()).toEqual(before);
    expect((await (await request.get(`/api/nodes/${ids.c}`)).json()).position.x).toBe(864);
  } finally { await cleanup(); }
});
