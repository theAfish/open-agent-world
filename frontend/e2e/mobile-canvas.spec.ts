import { expect, test, type APIRequestContext, type Locator, type Page, type CDPSession } from '@playwright/test';

test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

async function setup(page: Page, request: APIRequestContext) {
  const cards = [];
  for (const [name, x, y] of [['Touch source', 100, 280], ['Touch target', 235, 420]] as const) {
    const response = await request.post('/api/nodes', { data: { type: 'agent', name, position: { x, y } } });
    expect(response.ok()).toBe(true); cards.push(await response.json());
  }
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw.locale': 'en', 'oaw-active-workspace-v1': null,
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
      'oaw-canvas-viewport-v1': JSON.stringify({ state: { viewport: { x: 0, y: 0, zoom: 1, width: 390, height: 844 } }, version: 0 }),
      'oaw-node-surfaces-v1': JSON.stringify({ state: { surfaceLevels: Object.fromEntries(cards.map(card => [card.id, 'node'])) }, version: 3 }),
    },
  } })).ok()).toBe(true);
  await page.goto('/');
  const elements = cards.map(card => page.locator(`.react-flow__node[data-id="${card.id}"]`));
  for (const element of elements) await expect(element).toBeVisible();
  return { cards, elements, session: await page.context().newCDPSession(page) };
}
async function center(element: Locator) {
  const box = (await element.boundingBox())!;
  return { x: box.x + box.width / 2, y: box.y + box.height / 2, id: 1 };
}
async function touch(session: CDPSession, type: 'touchStart' | 'touchMove' | 'touchEnd' | 'touchCancel', points: { x: number; y: number; id: number }[]) {
  await session.send('Input.dispatchTouchEvent', { type, touchPoints: points });
}

test('mobile hold connects in one gesture, keeps positions stable and rejects duplicate relationships', async ({ page, request }) => {
  const { cards, elements, session } = await setup(page, request);
  try {
    const start = await center(elements[0]), end = await center(elements[1]);
    await touch(session, 'touchStart', [start]);
    await expect(elements[0]).toHaveAttribute('data-touch-connection', 'source');
    await touch(session, 'touchMove', [end]);
    await expect(elements[1]).toHaveAttribute('data-touch-connection', 'valid');
    await expect(page.locator('.semantic-connection-path')).toBeVisible();
    await touch(session, 'touchEnd', []);
    const chooser = page.getByRole('dialog', { name: 'Choose a capability' });
    await expect(chooser).toBeVisible();
    await chooser.getByRole('button', { name: 'Grant capability' }).tap();
    await expect(chooser).toHaveCount(0);
    await expect(page.locator('.semantic-edge-path')).toHaveCount(1);
    await page.getByRole('button', { name: 'World menu', exact: true }).tap();
    await expect(page.getByRole('button', { name: 'Open settings', exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Hide world controls', exact: true }).tap();
    await expect(page.getByRole('button', { name: 'Open settings', exact: true })).toBeHidden();
    for (const card of cards) {
      const saved = await (await request.get(`/api/nodes/${card.id}`)).json();
      expect(saved.position).toEqual(card.position);
    }
    await touch(session, 'touchStart', [await center(elements[0])]);
    await expect(elements[0]).toHaveAttribute('data-touch-connection', 'source');
    await touch(session, 'touchMove', [await center(elements[1])]);
    await expect(elements[1]).toHaveAttribute('data-touch-connection', 'invalid');
    await touch(session, 'touchEnd', []);
    await expect(chooser).toHaveCount(0);
    await expect(page.locator('.semantic-connection-path')).toHaveCount(0);
    await expect(page.locator('.semantic-edge-path')).toHaveCount(1);
  } finally { for (const card of cards) await request.delete(`/api/nodes/${card.id}`); }
});

test('mobile browsing pans over cards, a second finger cancels a link, and holding the crown picks up a card', async ({ page, request }, info) => {
  const { cards, elements, session } = await setup(page, request);
  const camera = () => page.locator('#oaw-world-map .react-flow__viewport').evaluate(element => {
    const matrix = new DOMMatrix(getComputedStyle(element).transform); return { x: matrix.e, y: matrix.f, zoom: matrix.a };
  });
  try {
    const before = await camera();
    const start = await center(elements[0]);
    await touch(session, 'touchStart', [start]);
    await touch(session, 'touchMove', [{ ...start, x: start.x + 30, y: start.y + 30 }]);
    await touch(session, 'touchEnd', []);
    await expect.poll(async () => (await camera()).x).not.toBe(before.x);
    await expect(page.locator('.touch-connection-hint')).toHaveCount(0);
    expect((await (await request.get(`/api/nodes/${cards[0].id}`)).json()).position).toEqual(cards[0].position);

    const first = await center(elements[0]);
    await touch(session, 'touchStart', [first]);
    await expect(elements[0]).toHaveAttribute('data-touch-connection', 'source');
    const second = { x: first.x + 60, y: first.y + 120, id: 2 };
    await touch(session, 'touchStart', [first, second]);
    await expect(page.locator('.semantic-connection-path')).toHaveCount(0);
    const zoom = (await camera()).zoom;
    await touch(session, 'touchMove', [{ ...first, x: first.x - 15 }, { ...second, x: second.x + 15, y: second.y + 20 }]);
    await touch(session, 'touchEnd', []);
    await expect.poll(async () => (await camera()).zoom).not.toBe(zoom);

    const position = await center(elements[0].locator('.touch-card-grip'));
    const steadyCamera = await camera();
    await touch(session, 'touchStart', [position]);
    await expect(elements[0]).toHaveAttribute('data-touch-move', 'active');
    await expect(page.locator('.semantic-connection-path')).toHaveCount(0);
    await page.screenshot({ path: info.outputPath('card-picked-up.png') });
    for (let step = 1; step <= 5; step++) await touch(session, 'touchMove', [{ ...position, x: position.x + step * 5, y: position.y + step * 6 }]);
    await touch(session, 'touchEnd', []);
    await expect.poll(async () => (await (await request.get(`/api/nodes/${cards[0].id}`)).json()).position.x).not.toBe(cards[0].position.x);
    const saved = await (await request.get(`/api/nodes/${cards[0].id}`)).json();
    expect(saved.position.x).toBeCloseTo(cards[0].position.x + 25 / steadyCamera.zoom, 0);
    expect(saved.position.y).toBeCloseTo(cards[0].position.y + 30 / steadyCamera.zoom, 0);
    expect(await camera()).toEqual(steadyCamera);
    await expect(elements[0]).not.toHaveAttribute('data-touch-move', 'active');
    await expect(page.locator('.touch-connection-hint')).toHaveCount(0);
  } finally { for (const card of cards) await request.delete(`/api/nodes/${card.id}`); }
});

test('picking up a card cancels without saving on second finger, touch cancellation or Escape', async ({ page, request }) => {
  const { cards, elements, session } = await setup(page, request);
  const writes: string[] = [];
  page.on('request', event => { if (event.method() === 'POST' && event.url().endsWith('/api/nodes/batch-update')) writes.push(event.url()); });
  try {
    const initial = (await elements[0].boundingBox())!;
    for (const cancel of ['second-finger', 'cancel', 'escape'] as const) {
      const position = await center(elements[0].locator('.touch-card-grip'));
      await touch(session, 'touchStart', [position]);
      await expect(elements[0]).toHaveAttribute('data-touch-move', 'active');
      await touch(session, 'touchMove', [{ ...position, x: position.x + 25, y: position.y + 30 }]);
      await expect.poll(async () => (await elements[0].boundingBox())!.x).toBeCloseTo(initial.x + 25, 0);
      if (cancel === 'second-finger') await touch(session, 'touchStart', [{ ...position, x: position.x + 25, y: position.y + 30 }, { x: 300, y: 520, id: 2 }]);
      else if (cancel === 'cancel') await touch(session, 'touchCancel', []);
      else await page.keyboard.press('Escape');
      await expect(elements[0]).not.toHaveAttribute('data-touch-move', 'active');
      await expect.poll(async () => (await elements[0].boundingBox())!.x).toBeCloseTo(initial.x, 0);
      if (cancel !== 'cancel') await touch(session, 'touchEnd', []);
      expect((await (await request.get(`/api/nodes/${cards[0].id}`)).json()).position).toEqual(cards[0].position);
    }
    expect(writes).toHaveLength(0);
    await expect(page.locator('.semantic-connection-path')).toHaveCount(0);
  } finally { for (const card of cards) await request.delete(`/api/nodes/${card.id}`); }
});

test('mobile connection pans at the screen edge and cancellation stops navigation without moving cards', async ({ page, request }) => {
  const { cards, elements, session } = await setup(page, request);
  const camera = () => page.locator('#oaw-world-map .react-flow__viewport').first().evaluate(element => getComputedStyle(element).transform);
  try {
    await touch(session, 'touchStart', [await center(elements[0])]);
    await expect(elements[0]).toHaveAttribute('data-touch-connection', 'source');
    const before = await camera();
    await touch(session, 'touchMove', [{ x: 385, y: 360, id: 1 }]);
    await expect.poll(camera).not.toBe(before);
    await touch(session, 'touchCancel', []);
    await expect(page.locator('.semantic-connection-path')).toHaveCount(0);
    const stopped = await camera();
    await page.waitForTimeout(100);
    expect(await camera()).toBe(stopped);
    for (const card of cards) expect((await (await request.get(`/api/nodes/${card.id}`)).json()).position).toEqual(card.position);
  } finally { for (const card of cards) await request.delete(`/api/nodes/${card.id}`); }
});

test('compact mouse browsing pans while a crown hold deliberately moves the card', async ({ page, request }) => {
  const { cards, elements } = await setup(page, request);
  const camera = () => page.locator('#oaw-world-map .react-flow__viewport').first().evaluate(element => getComputedStyle(element).transform);
  const mouseDrag = async (start: { x: number; y: number }) => {
    const before = await camera();
    await page.mouse.move(start.x, start.y);
    await page.mouse.down();
    await page.mouse.move(start.x + 35, start.y + 30, { steps: 5 });
    await page.mouse.up();
    await expect.poll(camera).not.toBe(before);
  };
  try {
    await mouseDrag({ x: 30, y: 360 });
    await mouseDrag(await center(elements[0]));
    await expect(page.locator('html')).toHaveAttribute('data-input', 'mouse');
    for (const card of cards) expect((await (await request.get(`/api/nodes/${card.id}`)).json()).position).toEqual(card.position);
    const position = await center(elements[0].locator('.touch-card-grip'));
    const before = await camera();
    await page.mouse.move(position.x, position.y);
    await page.mouse.down();
    await expect(elements[0]).toHaveAttribute('data-touch-move', 'active');
    await page.mouse.move(position.x + 25, position.y + 30, { steps: 5 });
    await page.mouse.up();
    await expect.poll(async () => (await (await request.get(`/api/nodes/${cards[0].id}`)).json()).position.x).toBe(cards[0].position.x + 25);
    expect(await camera()).toBe(before);
  } finally { for (const card of cards) await request.delete(`/api/nodes/${card.id}`); }
});

test('a scaled preview keeps a full grip target and moves its glued neighbour without a mode switch', async ({ page, request }, info) => {
  const cards = [];
  for (const [name, x] of [['Glued left', 84], ['Glued right', 308]] as const) {
    const response = await request.post('/api/nodes', { data: { type: 'text', name, position: { x, y: 302 } } });
    expect(response.ok()).toBe(true); cards.push(await response.json());
  }
  const [a, b] = cards.map(card => card.id);
  const profile = await (await request.get('/api/application')).json();
  try {
    expect((await request.patch('/api/application/preferences', { data: {
      profile_id: profile.profile_id, generation: profile.generation, changes: {
        'oaw.locale': 'en', 'oaw-active-workspace-v1': null,
        'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
        'oaw-canvas-viewport-v1': JSON.stringify({ state: { viewport: { x: 0, y: 0, zoom: .55, width: 390, height: 844 } } }),
        'oaw-node-surfaces-v1': JSON.stringify({ version: 4, state: { surfaceLevels: { [a]: 'preview', [b]: 'preview' } } }),
      },
    } })).ok()).toBe(true);
    const glue = await (await request.get('/api/canvas/glue')).json();
    expect((await request.patch('/api/canvas/glue', { data: { revision: glue.revision,
      boxes: { [a]: { x: 20, y: 200, width: 224, height: 300, level: 'preview' },
        [b]: { x: 244, y: 200, width: 224, height: 300, level: 'preview' } },
      bonds: [{ a, b, side: 'right' }],
    } })).ok()).toBe(true);
    await page.goto('/');
    const source = page.locator(`.react-flow__node[data-id="${a}"]`);
    const grip = source.locator('.touch-card-grip');
    await expect(grip).toBeVisible();
    expect((await grip.boundingBox())!.width).toBeCloseTo(44, 0);
    expect((await grip.boundingBox())!.height).toBeCloseTo(44, 0);
    const position = await center(grip);
    const session = await page.context().newCDPSession(page);
    // A pickup without movement neither opens details nor changes saved positions.
    await touch(session, 'touchStart', [position]);
    await expect(source).toHaveAttribute('data-touch-move', 'active');
    await touch(session, 'touchEnd', []);
    await expect(page.locator('.mobile-card-surface')).toHaveCount(0);
    expect((await (await request.get(`/api/nodes/${a}`)).json()).position).toEqual(cards[0].position);
    await touch(session, 'touchStart', [position]);
    await expect(source).toHaveAttribute('data-touch-move', 'active');
    await page.screenshot({ path: info.outputPath('preview-card-picked-up.png') });
    await touch(session, 'touchMove', [{ ...position, x: position.x + 22, y: position.y + 33 }]);
    await touch(session, 'touchEnd', []);
    for (const card of cards) {
      await expect.poll(async () => (await (await request.get(`/api/nodes/${card.id}`)).json()).position.x).toBeCloseTo(card.position.x + 40, 0);
      expect((await (await request.get(`/api/nodes/${card.id}`)).json()).position.y).toBeCloseTo(card.position.y + 60, 0);
    }
    await expect.poll(async () => (await (await request.get('/api/canvas/glue')).json()).boxes[a].x).toBeCloseTo(60, 0);
    expect((await (await request.get('/api/canvas/glue')).json()).bonds).toEqual([{ a, b, side: 'right' }]);
    await expect(page.locator('.mobile-card-surface')).toHaveCount(0);
  } finally { for (const card of cards) await request.delete(`/api/nodes/${card.id}`); }
});

test.describe('Edge device toolbar switched on after loading the desktop canvas', () => {
  test.use({ viewport: { width: 1280, height: 800 }, hasTouch: false, isMobile: false });

  test('touch pan and pinch start without reloading or remounting the canvas', async ({ page, request }) => {
    const { cards, elements, session } = await setup(page, request);
    const renderer = page.locator('#oaw-world-map > .react-flow__renderer');
    const camera = () => page.locator('#oaw-world-map .react-flow__viewport').first().evaluate(element => {
      const matrix = new DOMMatrix(getComputedStyle(element).transform);
      return { x: matrix.e, zoom: matrix.a };
    });
    try {
      await expect(page.locator('html')).toHaveAttribute('data-touch-input', 'false');
      await renderer.evaluate(element => { element.setAttribute('data-kept-during-emulation', 'true'); });
      await session.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 2 });
      await page.setViewportSize({ width: 390, height: 844 });
      await expect(page.locator('html')).toHaveAttribute('data-layout', 'compact');
      await expect(page.locator('html')).toHaveAttribute('data-touch-input', 'true');

      // This is a native touch gesture, which is what the F12 mouse becomes
      // while the device toolbar is enabled. No page reload occurs between modes.
      let before = await camera();
      await touch(session, 'touchStart', [{ x: 30, y: 360, id: 1 }]);
      await touch(session, 'touchMove', [{ x: 65, y: 390, id: 1 }]);
      await touch(session, 'touchEnd', []);
      await expect.poll(async () => (await camera()).x).not.toBe(before.x);

      before = await camera();
      const start = await center(elements[0]);
      await touch(session, 'touchStart', [start]);
      await touch(session, 'touchMove', [{ ...start, x: start.x + 30, y: start.y + 25 }]);
      await touch(session, 'touchEnd', []);
      await expect.poll(async () => (await camera()).x).not.toBe(before.x);

      before = await camera();
      await touch(session, 'touchStart', [{ x: 35, y: 200, id: 1 }, { x: 210, y: 240, id: 2 }]);
      await touch(session, 'touchMove', [{ x: 20, y: 190, id: 1 }, { x: 240, y: 250, id: 2 }]);
      await touch(session, 'touchEnd', []);
      await expect.poll(async () => (await camera()).zoom).not.toBe(before.zoom);
      await expect(renderer).toHaveAttribute('data-kept-during-emulation', 'true');
      for (const card of cards) expect((await (await request.get(`/api/nodes/${card.id}`)).json()).position).toEqual(card.position);
    } finally {
      await session.send('Emulation.setTouchEmulationEnabled', { enabled: false });
      for (const card of cards) await request.delete(`/api/nodes/${card.id}`);
    }
  });
});
