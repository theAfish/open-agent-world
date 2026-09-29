import { expect, test, type Page, type APIRequestContext } from '@playwright/test';
import { resetTutorialProfile } from './tutorial-profile';
import { prepareTutorialDeck } from './tutorial-deck';

async function prepareDeck(page: Page, request: APIRequestContext) {
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation,
    changes: { 'oaw.locale': 'en', 'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }) },
  } })).ok()).toBe(true);
  let library = await (await request.get('/api/card-library')).json();
  for (const id of library.available_pack_ids) library = await (await request.post('/api/card-library/actions', {
    data: { action: 'open_pack', id, expected_revision: library.revision },
  })).json();
  await page.goto('/');
  await expect(page.locator('[data-tutorial="deck"]')).toBeVisible();
  library = await (await request.get('/api/card-library')).json();
  expect((await request.post('/api/card-library/actions', { data: {
    action: 'update_deck', id: library.active_deck_id, entries: [{ kind: 'node', id: 'text' }], expected_revision: library.revision,
  } })).ok()).toBe(true);
  await page.reload();
}

async function beginDrag(page: Page) {
  const tray = page.locator('[data-tutorial="deck"]');
  await tray.hover();
  const source = tray.locator('[data-palette-card="text"]');
  await source.hover();
  const box = (await source.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(800, 300, { steps: 5 });
  await expect(page.locator('.palette-drag-preview')).toBeVisible();
}

test('held Deck preview tracks every frame without instantiating; release creates exactly once', async ({ page, request }) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1600, height: 1000 });
  const ids: string[] = [];
  const createdRequests: string[] = [];
  page.on('request', r => { if (r.method() === 'POST' && r.url().endsWith('/api/card-library/nodes')) createdRequests.push(r.url()); });
  try {
    for (let i = 0; i < 30; i++) {
      const card = await (await request.post('/api/nodes', { data: {
        type: i % 5 === 0 ? 'agent' : 'text', name: `Held drag ${i}`,
        position: { x: 200 + i % 6 * 280, y: 180 + Math.floor(i / 6) * 300 }, size: { width: 96, height: 96 },
      } })).json();
      ids.push(card.id);
    }
    await prepareDeck(page, request);
    await page.evaluate(() => {
      const w = window as any;
      w.nativeDragStarts = 0;
      document.addEventListener('dragstart', () => w.nativeDragStarts++);
    });
    await beginDrag(page);
    const first = (await page.locator('.palette-drag-preview').boundingBox())!;
    const offset = { x: 800 - first.x, y: 300 - first.y };
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Performance.enable');
    const before = await cdp.send('Performance.getMetrics');
    await page.evaluate(() => {
      const w = window as any;
      w.dragFrames = []; w.trackDragFrames = true;
      let previous = performance.now();
      const sample = (now: number) => {
        w.dragFrames.push(now - previous); previous = now;
        if (w.trackDragFrames) requestAnimationFrame(sample);
      };
      requestAnimationFrame(sample);
    });
    let maxError = 0;
    for (let i = 1; i <= 60; i++) {
      const point = { x: 800 + Math.sin(i / 9) * 260, y: 300 + Math.cos(i / 9) * 130 };
      await page.mouse.move(point.x, point.y);
      const box = await page.evaluate(() => new Promise<{ x: number; y: number }>(resolve => {
        requestAnimationFrame(() => {
          const rect = document.querySelector('.palette-drag-preview')!.getBoundingClientRect();
          resolve({ x: rect.x, y: rect.y });
        });
      }));
      maxError = Math.max(maxError, Math.abs(box.x + offset.x - point.x), Math.abs(box.y + offset.y - point.y));
    }
    const frames = await page.evaluate(() => {
      const w = window as any; w.trackDragFrames = false;
      const gaps = w.dragFrames.slice(1).sort((a: number, b: number) => a - b);
      return { nativeDragStarts: w.nativeDragStarts, frames: gaps.length, p95: gaps[Math.floor(gaps.length * .95)], max: gaps.at(-1) };
    });
    const after = await cdp.send('Performance.getMetrics');
    console.log('DECK_HELD_PREVIEW', JSON.stringify({ ...frames, maxError,
      scriptMs: 1000 * (after.metrics.find(m => m.name === 'ScriptDuration')!.value - before.metrics.find(m => m.name === 'ScriptDuration')!.value) }));
    expect(frames.nativeDragStarts).toBe(0);
    expect(maxError).toBeLessThan(1.1); // Chromium rounds input coordinates to device pixels.
    expect(createdRequests).toHaveLength(0);
    await page.screenshot({ path: '../.outputs/deck-held-preview.png' });
    await page.keyboard.press('Escape');
    await page.mouse.up();
    await expect(page.locator('.palette-drag-preview')).toHaveCount(0);
    expect(createdRequests).toHaveLength(0);
    await beginDrag(page);
    const response = page.waitForResponse(r => r.url().endsWith('/api/card-library/nodes') && r.request().method() === 'POST');
    await page.mouse.move(1100, 250);
    await page.mouse.up();
    const placed = await response;
    expect(placed.status()).toBe(201);
    const card = await placed.json(); ids.push(card.id);
    await expect(page.locator(`[data-card-id="${card.id}"]`)).toBeVisible();
    await expect(page.locator('.palette-drag-preview')).toHaveCount(0);
    expect(createdRequests).toHaveLength(1);
  } finally {
    await page.mouse.up();
    for (const id of ids.reverse()) await request.delete(`/api/nodes/${id}`);
  }
});

test('Deck pointer drag cancels cleanly and preserves deck transfer and discard', async ({ page, request }) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  await prepareDeck(page, request);
  const creates: string[] = [];
  page.on('request', r => { if (r.method() === 'POST' && r.url().endsWith('/api/card-library/nodes')) creates.push(r.url()); });
  for (const cancel of ['pointercancel', 'blur', 'lostpointercapture']) {
    await beginDrag(page);
    await page.evaluate(type => {
      if (type === 'lostpointercapture') document.querySelector('[data-palette-card="text"]')!.dispatchEvent(new PointerEvent(type, { pointerId: 1 }));
      else if (type === 'pointercancel') window.dispatchEvent(new PointerEvent(type, { pointerId: 1 }));
      else window.dispatchEvent(new Event(type));
    }, cancel);
    await page.mouse.up();
    await expect(page.locator('.palette-drag-preview')).toHaveCount(0);
    await expect(page.locator('body')).not.toHaveClass(/is-palette-dragging/);
  }
  let library = await (await request.get('/api/card-library')).json();
  const sourceId = library.active_deck_id;
  library = await (await request.post('/api/card-library/actions', { data: {
    action: 'create_deck', name: 'Pointer destination', icon: 'folder', expected_revision: library.revision,
  } })).json();
  const targetId = library.active_deck_id;
  await request.post('/api/card-library/actions', { data: { action: 'activate_deck', id: sourceId, expected_revision: library.revision } });
  await page.reload();
  await beginDrag(page);
  const target = page.locator(`[role="tab"][data-deck-destination="${targetId}"]`);
  const box = (await target.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await expect(target).toHaveClass(/is-drop-target/);
  await page.mouse.up();
  await expect.poll(async () => (await (await request.get('/api/card-library')).json()).decks.find((d: any) => d.id === targetId).entries).toEqual([{ kind: 'node', id: 'text' }]);
  await target.click();
  await beginDrag(page);
  const trash = page.getByRole('region', { name: 'Discard card' });
  const trashBox = (await trash.boundingBox())!;
  await page.mouse.move(trashBox.x + trashBox.width / 2, trashBox.y + trashBox.height / 2);
  await expect(trash).toHaveClass(/is-active/);
  await page.mouse.up();
  await expect.poll(async () => (await (await request.get('/api/card-library')).json()).decks.find((d: any) => d.id === targetId).entries).toEqual([]);
  expect(creates).toHaveLength(0);
});

test('Deck still supports click, keyboard activation and touch placement', async ({ page, request }) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  await prepareDeck(page, request);
  const ids: string[] = [];
  const source = page.locator('[data-palette-card="text"]');
  const created = () => page.waitForResponse(r => r.url().endsWith('/api/card-library/nodes') && r.request().method() === 'POST');
  try {
    await page.locator('[data-tutorial="deck"]').hover();
    let response = created();
    await source.click();
    ids.push((await (await response).json()).id);
    response = created();
    await source.focus();
    await page.keyboard.press('Enter');
    ids.push((await (await response).json()).id);
    await expect(page.locator('.palette-drag-preview')).toHaveCount(0);
    await page.locator('[data-tutorial="deck"]').hover();
    const box = (await source.boundingBox())!;
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: box.x + box.width / 2, y: box.y + box.height / 2 }] });
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: 1000, y: 220 }] });
    await expect(page.locator('.palette-drag-preview')).toBeVisible();
    response = created();
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    ids.push((await (await response).json()).id);
    await expect(page.locator('.palette-drag-preview')).toHaveCount(0);
    expect(ids).toHaveLength(3);
  } finally { for (const id of ids) await request.delete(`/api/nodes/${id}`); }
});

test('tutorial pointer placement keeps the drop boundary and Escape cancels before pausing', async ({ page, request }) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  await resetTutorialProfile(request);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const originalIds = new Set((await (await request.get('/api/nodes')).json()).map((node: { id: string }) => node.id));
  try {
    await page.goto('/');
    await page.getByRole('button', { name: /^Start Tutorial/ }).click();
    await page.getByRole('button', { name: 'Let’s go', exact: true }).click();
    await page.mouse.move(160, 140); await page.mouse.down(); await page.mouse.move(280, 190, { steps: 10 }); await page.mouse.up();
    await expect(page.locator('.tutorial-bubble')).toHaveAttribute('data-step', 'zoom');
    await page.mouse.move(170, 200); await page.mouse.wheel(0, -240);
    await expect(page.locator('.tutorial-bubble')).toHaveAttribute('data-step', 'deck');
    await prepareTutorialDeck(page);
    await page.getByRole('button', { name: 'Show me', exact: true }).click();
    await page.locator('.tutorial-next').filter({ hasText: 'Continue' }).click();
    await expect(page.locator('.tutorial-bubble')).toHaveAttribute('data-step', 'place');
    await beginDrag(page);
    await page.keyboard.press('Escape'); await page.mouse.up();
    await expect(page.locator('.palette-drag-preview')).toHaveCount(0);
    await expect(page.locator('.tutorial-bubble')).toHaveAttribute('data-step', 'place');
    await beginDrag(page);
    const trash = (await page.getByRole('region', { name: 'Discard card' }).boundingBox())!;
    await page.mouse.move(trash.x + trash.width / 2, trash.y + trash.height / 2); await page.mouse.up();
    await expect(page.locator('.tutorial-bubble')).toHaveAttribute('data-step', 'place');
    await expect(page.locator('[data-palette-card="text"]')).toHaveCount(1);
    await beginDrag(page); await page.mouse.up();
    await expect(page.locator('.tutorial-bubble')).toHaveAttribute('data-step', 'move');
  } finally {
    await page.mouse.up();
    const nodes = await (await request.get('/api/nodes')).json();
    for (const node of nodes) if (!originalIds.has(node.id)) await request.delete(`/api/nodes/${node.id}`);
  }
});
