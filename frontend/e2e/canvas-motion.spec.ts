import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

async function prepare(request: APIRequestContext, levels: Record<string, string> = {}) {
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw.locale': 'en', 'oaw-theme': 'light', 'oaw-active-workspace-v1': null,
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
      'oaw-node-surfaces-v1': JSON.stringify({ state: { surfaceLevels: levels }, version: 3 }),
      'oaw-canvas-viewport-v1': JSON.stringify({ state: { viewport: { x: 0, y: 0, zoom: 1, width: 1800, height: 1000 } }, version: 0 }),
    },
  } })).ok()).toBe(true);
}

function frames(page: Page, duration = 700) {
  return page.evaluate(async duration => {
    const samples: {
      cards: { id: string | undefined; phase: string | undefined; opacity: number; scale: string }[];
      exits: { inert: boolean; hidden: boolean; cardIds: number; opacity: number }[];
    }[] = [];
    const end = performance.now() + duration;
    while (performance.now() < end) {
      await new Promise(requestAnimationFrame);
      samples.push({
        cards: [...document.querySelectorAll<HTMLElement>('[data-canvas-motion]')].map(element => ({
          id: element.dataset.cardId, phase: element.dataset.canvasMotion,
          opacity: Number(getComputedStyle(element).opacity), scale: getComputedStyle(element).scale,
        })),
        exits: [...document.querySelectorAll<HTMLElement>('.canvas-card-exit')].map(element => ({
          inert: element.inert, hidden: element.getAttribute('aria-hidden') === 'true',
          cardIds: element.querySelectorAll('[data-card-id]').length,
          opacity: Number(getComputedStyle(element).opacity),
        })),
      });
    }
    return samples;
  }, duration);
}

test.use({ viewport: { width: 1800, height: 1000 } });

test('live cards enter, stay still after dragging, and leave as an inert visual copy', async ({ page, request }) => {
  const existing = await (await request.post('/api/nodes', { data: { type: 'text', name: 'Canvas note', position: { x: 500, y: 450 } } })).json();
  const ids = [existing.id];
  try {
    await prepare(request, { [existing.id]: 'inspector' });
    await page.goto('/');
    const initial = page.locator(`.world-card[data-card-id="${existing.id}"]`);
    await expect(initial).toBeVisible();
    expect((await frames(page, 150)).every(frame => frame.cards.length === 0)).toBe(true);

    const [appearing, response] = await Promise.all([
      frames(page),
      request.post('/api/nodes', { data: { type: 'text', name: 'New card', position: { x: 1100, y: 450 } } }),
    ]);
    expect(response.ok()).toBe(true);
    const created = await response.json();
    ids.push(created.id);
    const card = page.locator(`.world-card[data-card-id="${created.id}"]`);
    await expect(card).toBeVisible();
    expect(appearing.some(frame => frame.cards.some(item => item.id === created.id && item.phase === 'enter' && item.opacity > 0 && item.opacity < 1))).toBe(true);

    const box = (await card.boundingBox())!;
    // XYFlow establishes the drag origin after crossing its 5px threshold.
    await page.mouse.move(box.x + 24, box.y + 30);
    await page.mouse.down();
    await page.mouse.move(box.x + 30, box.y + 30);
    await page.mouse.move(box.x + 110, box.y + 70, { steps: 10 });
    const dropped = (await card.boundingBox())!;
    const [landing] = await Promise.all([card.evaluate(async element => {
      const samples: { x: number; y: number; width: number; height: number; scale: string }[] = [];
      const end = performance.now() + 500;
      while (performance.now() < end) {
        await new Promise(requestAnimationFrame);
        const { x, y, width, height } = element.getBoundingClientRect();
        samples.push({ x, y, width, height, scale: getComputedStyle(element).scale });
      }
      return samples;
    }), page.mouse.up()]);
    expect(landing.every(sample => (sample.scale === 'none' || sample.scale === '1')
      && Math.abs(sample.x - dropped.x) < .5 && Math.abs(sample.y - dropped.y) < .5
      && Math.abs(sample.width - dropped.width) < .5 && Math.abs(sample.height - dropped.height) < .5)).toBe(true);
    await expect.poll(async () => (await (await request.get(`/api/nodes/${created.id}`)).json()).position.x).toBeCloseTo(created.position.x + 80, 0);

    await initial.locator('.card-eyebrow').click();
    await expect(initial).toHaveClass(/is-selected/);
    const handles = page.locator(`[data-resize-node="${existing.id}"]`);
    await expect(handles).toHaveCount(4);
    for (const theme of ['light', 'dark']) {
      await page.evaluate(theme => { document.documentElement.dataset.theme = theme; }, theme);
      await page.screenshot({ path: test.info().outputPath(`canvas-selection-${theme}.png`) });
    }

    const [leaving] = await Promise.all([
      frames(page),
      page.evaluate(async id => {
        const modulePath = '/src/state/worldStore.ts';
        const { useWorldStore } = await import(modulePath);
        await useWorldStore.getState().deleteCard(id);
      }, created.id),
    ]);
    expect(leaving.some(frame => frame.exits.some(exit => exit.opacity > 0 && exit.opacity < 1))).toBe(true);
    expect(leaving.flatMap(frame => frame.exits).every(exit => exit.inert && exit.hidden && exit.cardIds === 0)).toBe(true);
    await expect(card).toHaveCount(0);
    await expect(page.locator('.canvas-card-exit')).toHaveCount(0);
    await page.keyboard.press('Control+z');
    await expect(card).toBeVisible();
    await expect(card).not.toHaveAttribute('data-canvas-motion');
    await page.mouse.click(1700, 150);
    for (let i = 0; i < 2; i++) {
      await page.mouse.move(1700, 150);
      await page.mouse.down();
      await page.mouse.move(100, 150, { steps: 10 });
      await page.mouse.up();
    }
    await expect(card).toHaveCount(0);
    const [returning] = await Promise.all([frames(page), (async () => {
      for (let i = 0; i < 2; i++) {
        await page.mouse.move(100, 150);
        await page.mouse.down();
        await page.mouse.move(1700, 150, { steps: 10 });
        await page.mouse.up();
      }
    })()]);
    await expect(card).toBeVisible();
    expect(returning.every(frame => frame.cards.length === 0)).toBe(true);
    await page.reload();
    await expect(card).toBeVisible();
    expect((await frames(page, 200)).every(frame => frame.cards.length === 0 && frame.exits.length === 0)).toBe(true);
  } finally {
    await request.post('/api/nodes/batch-delete', { data: { node_ids: ids } });
  }
});

test('connections lock onto targets and animate only when confirmed, respecting reduced motion', async ({ page, request }) => {
  const ids: string[] = [];
  const sampleConnection = () => page.evaluate(async () => {
    const samples: { animated: boolean; radius: number }[] = [];
    const end = performance.now() + 700;
    while (performance.now() < end) {
      await new Promise(requestAnimationFrame);
      const elements = [...document.querySelectorAll('.semantic-edge-path, .semantic-edge-endpoint')];
      samples.push({
        animated: elements.some(element => element.getAnimations().length > 0),
        radius: Math.max(0, ...elements.filter(element => element.matches('circle'))
          .map(element => parseFloat(getComputedStyle(element).r))),
      });
    }
    return samples;
  });
  try {
    for (const [name, x, y] of [['Source', 500, 450], ['Target', 1100, 530]] as const) {
      const response = await request.post('/api/nodes', { data: { type: 'agent', name, position: { x, y } } });
      expect(response.ok()).toBe(true);
      ids.push((await response.json()).id);
    }
    await prepare(request);
    await page.goto('/');
    const source = page.locator(`.world-card[data-card-id="${ids[0]}"]`);
    const target = page.locator(`.world-card[data-card-id="${ids[1]}"]`);
    await expect(source).toBeVisible();
    await expect(target).toBeVisible();
    const edge = page.locator(`.semantic-edge-path[data-source-id="${ids[0]}"][data-target-id="${ids[1]}"]`);
    for (const reducedMotion of ['no-preference', 'reduce'] as const) {
      await page.emulateMedia({ reducedMotion });
      const start = (await source.locator('[data-connection-side="right"]').boundingBox())!;
      const end = (await target.boundingBox())!;
      await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2);
      await page.mouse.down();
      await page.mouse.move(end.x + end.width / 2, end.y + end.height / 2, { steps: 10 });
      const lock = page.locator('.semantic-connection.is-valid .semantic-connection-lock');
      await expect(lock).toBeVisible();
      await expect(page.locator('.semantic-connection.is-valid .semantic-connection-path')).toHaveCSS('stroke-dasharray', 'none');
      if (reducedMotion === 'reduce') await expect(lock).toHaveCSS('animation-name', 'none');
      await page.mouse.up();
      const dialog = page.getByRole('dialog', { name: 'Choose a capability' });
      await expect(dialog).toBeVisible();
      await expect(edge).toHaveCount(0);
      const [samples] = await Promise.all([
        sampleConnection(), dialog.getByRole('button', { name: 'Grant capability' }).click(),
      ]);
      await expect(edge).toBeVisible();
      if (reducedMotion === 'no-preference') {
        // Selected endpoints rest at 5.25px; the confirmation grows beyond that.
        expect(samples.some(sample => sample.animated && sample.radius > 5.25)).toBe(true);
        // WorldCanvas retains loaded edges (onlyRenderVisibleElements=false),
        // while card surfaces are culled. Check actual viewport departure;
        // panning is not an edge deletion.
        for (let i = 0; i < 2; i++) {
          await page.mouse.move(1700, 150); await page.mouse.down();
          await page.mouse.move(100, 150, { steps: 10 }); await page.mouse.up();
        }
        await expect(edge).not.toBeInViewport();
        const [returning] = await Promise.all([sampleConnection(), (async () => {
          for (let i = 0; i < 2; i++) {
            await page.mouse.move(100, 150); await page.mouse.down();
            await page.mouse.move(1700, 150, { steps: 10 }); await page.mouse.up();
          }
        })()]);
        await expect(edge).toBeVisible();
        await expect(edge).toBeInViewport();
        expect(returning.every(sample => !sample.animated)).toBe(true);
        const id = await edge.getAttribute('data-edge-id');
        expect((await request.delete(`/api/edges/${id}`)).ok()).toBe(true);
        await expect(edge).toHaveCount(0);
      } else {
        // Selection remains visible at its static 5.25px radius in reduced mode.
        expect(samples.every(sample => !sample.animated && sample.radius <= 5.25)).toBe(true);
      }
    }
  } finally {
    await request.post('/api/nodes/batch-delete', { data: { node_ids: ids } });
  }
});

test('reduced motion keeps creation, placement and deletion immediate', async ({ page, request }) => {
  await prepare(request);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await expect(page.locator('#oaw-world-map')).toBeVisible();
  const [appearing, response] = await Promise.all([
    frames(page),
    request.post('/api/nodes', { data: { type: 'text', name: 'Quiet card', position: { x: 700, y: 450 } } }),
  ]);
  const created = await response.json();
  try {
    const card = page.locator(`.world-card[data-card-id="${created.id}"]`);
    await expect(card).toBeVisible();
    expect(appearing.every(frame => frame.cards.length === 0 && frame.exits.length === 0)).toBe(true);
    const box = (await card.boundingBox())!;
    await page.mouse.move(box.x + 30, box.y + 30);
    await page.mouse.down();
    await page.mouse.move(box.x + 90, box.y + 70, { steps: 8 });
    const [landing] = await Promise.all([frames(page, 350), page.mouse.up()]);
    expect(landing.every(frame => frame.cards.length === 0)).toBe(true);
    const [leaving] = await Promise.all([
      frames(page, 350),
      page.evaluate(async id => {
        const modulePath = '/src/state/worldStore.ts';
        const { useWorldStore } = await import(modulePath);
        await useWorldStore.getState().deleteCard(id);
      }, created.id),
    ]);
    expect(leaving.every(frame => frame.exits.length === 0)).toBe(true);
    await expect(card).toHaveCount(0);
  } finally { await request.delete(`/api/nodes/${created.id}`); }
});
