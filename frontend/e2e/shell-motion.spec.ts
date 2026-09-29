import { expect, test, type Page } from '@playwright/test';
import { resetTutorialProfile } from './tutorial-profile';
import type { LibrarySnapshot } from '../src/state/cardLibrary';

async function viewport(page: Page) {
  return page.locator('#oaw-world-map .react-flow__viewport').first().evaluate(element => {
    const matrix = new DOMMatrix(getComputedStyle(element).transform);
    return { x: matrix.e, y: matrix.f, zoom: matrix.a };
  });
}

test.beforeEach(async ({ request }) => {
  await resetTutorialProfile(request);
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: { 'oaw.locale': 'en' },
  } });
});

async function enter(page: Page) {
  await page.goto('/');
  await page.getByRole('button', { name: 'Start blank', exact: true }).click();
  await expect(page.locator('.onboarding-layer.is-welcome')).toHaveCount(0);
  await expect(page.locator('.top-bar')).toBeVisible();
}

test('welcome leaves in several directions while canvas controls enter', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('.top-bar')).toBeHidden();
  await expect(page.getByRole('button', { name: 'Start blank', exact: true })).toBeEnabled();
  const samples = await page.evaluate(async () => {
    const start = [...document.querySelectorAll<HTMLButtonElement>('.welcome-action')].find(button => button.textContent?.includes('Start blank'))!;
    start.click();
    const samples: { leaving: boolean; inert: boolean; logo: number; left: string; right: string; hud: number }[] = [];
    const end = performance.now() + 650;
    while (performance.now() < end) {
      await new Promise(requestAnimationFrame);
      const layer = document.querySelector<HTMLElement>('.onboarding-layer.is-welcome');
      const read = (selector: string) => { const el = document.querySelector(selector); return el ? getComputedStyle(el) : undefined; };
      samples.push({ leaving: !!layer?.classList.contains('is-leaving'), inert: !!layer?.inert,
        logo: Number(read('.onboarding-logo-ring')?.opacity ?? 0),
        left: read('.welcome-action:nth-child(2)')?.translate ?? '',
        right: read('.welcome-action:nth-child(3)')?.translate ?? '',
        hud: Number(read('.top-bar')?.opacity),
      });
    }
    return samples;
  });
  expect(samples.some(s => s.leaving && s.inert && s.logo > 0 && s.logo < 1)).toBe(true);
  expect(samples.some(s => s.left.startsWith('-') && parseFloat(s.right) > 0)).toBe(true);
  expect(samples.some(s => s.hud > 0 && s.hud < 1)).toBe(true);
  await expect(page.locator('.onboarding-layer.is-welcome')).toHaveCount(0);
  await page.screenshot({ path: 'test-results/motion-canvas-entered.png' });
});

test('dialogs animate both ways and deck contents stay above the tray bottom', async ({ page, request }) => {
  let snapshot: LibrarySnapshot = await (await request.get('/api/card-library')).json();
  const pack = Object.values(snapshot.packs).find(pack => pack.definition.cards.includes('text'))!;
  snapshot = await (await request.post('/api/card-library/actions', { data: {
    action: 'open_pack', id: pack.definition.id, expected_revision: snapshot.revision,
  } })).json();
  expect((await request.post('/api/card-library/actions', { data: {
    action: 'update_deck', id: snapshot.active_deck_id,
    entries: ['text', 'conversation', 'sandbox'].map(id => ({ kind: 'node', id })), expected_revision: snapshot.revision,
  } })).ok()).toBe(true);
  await enter(page);
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 800 });
    await page.getByRole('button', { name: 'Open Pack and Card Library', exact: true }).click();
    const library = page.getByRole('dialog', { name: 'Pack & Card Library' });
    await expect(library).toBeVisible();
    await expect(page.locator('.deck-stage [data-palette-card]')).toHaveCount(3);
    await expect.poll(() => page.locator('.deck-stage').evaluate(el => el.getBoundingClientRect().height)).toBeGreaterThan(100);
    await expect.poll(() => library.evaluate(el => el.getAnimations().filter(a => a.playState === 'running').length)).toBe(0);
    await page.screenshot({ path: `test-results/motion-library-${width}.png` });
    const frames = await page.evaluate(async () => {
      document.querySelector<HTMLButtonElement>('[aria-label="Close Library"]')!.click();
      const samples: { closing: boolean; opacity: number; height: number; leaked: boolean }[] = [];
      const end = performance.now() + 400;
      while (performance.now() < end) {
        await new Promise(requestAnimationFrame);
        const stage = document.querySelector<HTMLElement>('.deck-stage')!;
        const modal = document.querySelector<HTMLElement>('.card-library-modal')!;
        const box = stage.getBoundingClientRect();
        // Hit testing obeys clipping even when children's layout boxes overflow.
        const leaked = [box.left + 25, box.left + box.width / 2, box.right - 25].some(x =>
          stage.contains(document.elementFromPoint(x, Math.min(innerHeight - 1, box.bottom + 5))));
        samples.push({ closing: modal.dataset.motion === 'closing' && modal.inert,
          opacity: Number(getComputedStyle(modal).opacity), height: box.height, leaked });
      }
      return samples;
    });
    expect(frames.some(f => f.closing && f.opacity > 0 && f.opacity < 1)).toBe(true);
    expect(frames.some(f => f.height > 5 && f.height < 100)).toBe(true);
    expect(frames.some(f => f.leaked)).toBe(false);
    await expect(library).toBeHidden();
    await page.screenshot({ path: `test-results/motion-deck-collapsed-${width}.png` });
    await page.locator('.deck-tabs').hover();
    await expect.poll(() => page.locator('.deck-stage').evaluate(el => el.getBoundingClientRect().height)).toBeGreaterThan(100);
    await page.locator('[data-palette-card="text"]').hover();
    await page.mouse.move(width / 2, 300);
    await expect.poll(() => page.locator('.deck-stage').evaluate(el => el.getBoundingClientRect().height)).toBe(0);
  }
  await page.getByRole('button', { name: 'Open settings', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Settings', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Close settings', exact: true }).click();
  await expect(page.locator('.settings-backdrop')).toHaveAttribute('data-motion', 'closing');
  await page.getByRole('button', { name: 'Open settings', exact: true }).click();
  await expect(page.locator('.settings-backdrop')).toHaveAttribute('data-motion', 'open');
  await page.getByRole('button', { name: 'Close settings', exact: true }).click();
  await expect(page.locator('.settings-backdrop')).toHaveCount(0);
});

test('wheel keeps its original gain and pointer anchor, accumulates input and yields to dragging', async ({ page }) => {
  await enter(page);
  const before = await viewport(page);
  const samples = await page.evaluate(async () => {
    const flow = document.querySelector('#oaw-world-map')!;
    const surface = flow.querySelector('.react-flow__viewport')!;
    flow.dispatchEvent(new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: -120, clientX: 630, clientY: 310 }));
    const samples: { x: number; y: number; zoom: number }[] = [];
    const end = performance.now() + 260;
    while (performance.now() < end) {
      await new Promise(requestAnimationFrame);
      const m = new DOMMatrix(getComputedStyle(surface).transform);
      samples.push({ x: m.e, y: m.f, zoom: m.a });
    }
    return samples;
  });
  const expected = before.zoom * 2 ** 0.24;
  expect(samples.at(-1)!.zoom).toBeCloseTo(expected, 4);
  expect(samples.filter(s => s.zoom > before.zoom + 0.0001 && s.zoom < expected - 0.0001).length).toBeGreaterThan(2);
  for (const sample of samples) {
    expect((630 - sample.x) / sample.zoom).toBeCloseTo((630 - before.x) / before.zoom, 2);
    expect((310 - sample.y) / sample.zoom).toBeCloseTo((310 - before.y) / before.zoom, 2);
  }
  // Events arriving before a paint still accumulate their entire original strength.
  await page.evaluate(() => {
    for (const deltaY of [-30, -30, -30]) document.querySelector('#oaw-world-map')!.dispatchEvent(
      new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY, clientX: 630, clientY: 310 }));
  });
  await expect.poll(async () => (await viewport(page)).zoom).toBeCloseTo(expected * 2 ** 0.18, 4);
  await page.mouse.move(630, 310);
  await page.mouse.wheel(0, -200);
  await page.mouse.down();
  const interrupted = await viewport(page);
  await page.mouse.move(690, 350, { steps: 8 });
  await page.mouse.up();
  const dragged = await viewport(page);
  expect(dragged.zoom).toBeCloseTo(interrupted.zoom, 4);
  expect(dragged.x - interrupted.x).toBeCloseTo(60, 0);
  expect(dragged.y - interrupted.y).toBeCloseTo(40, 0);
  await page.getByRole('button', { name: 'Zoom in', exact: true }).click();
  await expect.poll(async () => (await viewport(page)).zoom).toBeCloseTo(dragged.zoom * 1.2, 4);
  const takeoverZoom = await page.evaluate(async () => {
    document.querySelector<HTMLButtonElement>('.react-flow__controls-zoomout')!.click();
    await new Promise(requestAnimationFrame);
    await new Promise(requestAnimationFrame);
    const zoom = new DOMMatrix(getComputedStyle(document.querySelector('#oaw-world-map .react-flow__viewport')!).transform).a;
    document.querySelector('#oaw-world-map')!.dispatchEvent(new WheelEvent('wheel', {
      bubbles: true, cancelable: true, deltaY: 120, clientX: 630, clientY: 310,
    }));
    return zoom * 2 ** -0.24;
  });
  await expect.poll(async () => (await viewport(page)).zoom).toBeCloseTo(takeoverZoom, 4);
});

test('reduced motion enters and closes immediately and retains the same zoom strength', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await enter(page);
  const before = await viewport(page);
  await page.mouse.move(630, 310);
  await page.mouse.wheel(0, -120);
  await expect.poll(async () => (await viewport(page)).zoom).toBeCloseTo(before.zoom * 2 ** 0.24, 4);
  await page.getByRole('button', { name: 'Open settings', exact: true }).click();
  await page.getByRole('button', { name: 'Close settings', exact: true }).click();
  await expect(page.locator('.settings-backdrop')).toHaveCount(0);
});
