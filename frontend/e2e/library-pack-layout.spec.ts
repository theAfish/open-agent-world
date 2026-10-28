import { expect, test } from '@playwright/test';
import type { LibrarySnapshot } from '../src/state/cardLibrary';
import type { PackInstallations } from '../src/types/packs';

for (const width of [1280, 390]) for (const renderer of ['webgl', 'fallback']) {
  test(`pack opening keeps the Library grid fixed at ${width}px with ${renderer}`, async ({ page, request }, testInfo) => {
    test.setTimeout(60000);
    if (renderer === 'fallback') await page.addInitScript(() => {
      Object.defineProperty(window, 'WebGL2RenderingContext', { value: undefined });
    });
    if (width === 390 && renderer === 'fallback') await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.setViewportSize({ width, height: 900 });
    const profile = await (await request.get('/api/application')).json();
    await request.patch('/api/application/preferences', { data: {
      profile_id: profile.profile_id, generation: profile.generation,
      changes: { 'oaw.locale': 'en', 'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }) },
    } });
    const state = await (await request.get('/api/card-library')).json() as LibrarySnapshot;
    const template = Object.values(state.packs).find(pack => pack.definition.cards.length >= 3)!;
    const presets = ['standard', 'premium', 'paper', 'collector'] as const;
    const versions: PackInstallations['versions'] = [];
    state.packs = Object.fromEntries(Array.from({ length: 8 }, (_, index) => {
      const id = `layout.${index}`, packaging = presets[index % presets.length];
      const owner = index >= 4 ? `layout.owner.${index}` : template.definition.plugin_id;
      if (index >= 4) state.plugins[owner] = { ...state.plugins[template.definition.plugin_id], installed: index !== 4 };
      if (index >= 4 && index <= 6) versions.push({ id: owner, name: `Pack ${index + 1}`, version: '1.2.0', selected: true, loaded: index !== 6,
        environment: index === 5 ? { state: 'environment_failed', error: 'Runtime unavailable' } : null });
      return [id, { ...template, opened: false, definition: { ...template.definition,
        id, plugin_id: owner, artwork_url: index === 7 ? '/missing-pack-cover.png' : null, name: `Pack ${index + 1}: ${packaging}`, packaging } }];
    }));
    state.available_pack_ids = Object.keys(state.packs);
    await page.route('**/api/card-library', route => route.fulfill({ json: state }));
    await page.route('**/api/packs', route => route.fulfill({ json: { restart_required: true, versions } }));
    await page.route('**/api/card-library/actions', async route => {
      state.packs[route.request().postDataJSON().id].opened = true;
      state.revision++;
      await route.fulfill({ json: state });
    });
    let missingArtworkRequests = 0;
    await page.route('**/missing-pack-cover.png', route => { missingArtworkRequests++; return route.fulfill({ status: 404, body: '' }); });
    await page.goto('/');
    await page.getByRole('button', { name: 'Open Pack and Card Library' }).click();
    const library = page.getByRole('dialog', { name: 'Pack & Card Library' });
    await expect(library.getByText('Your collection', { exact: true })).toHaveCount(0);
    await expect(library.locator('.library-flow')).toHaveCount(0);
    expect((await library.locator('.library-header').boundingBox())!.height).toBeLessThanOrEqual(56);
    if (width === 1280) expect((await library.boundingBox())!.width).toBeGreaterThanOrEqual(width - 50);
    await expect(library.locator('.pack-grid > .library-pack')).toHaveCount(8);
    await expect(library.locator('[data-pack-id="layout.4"]')).toHaveAttribute('data-pack-notice', 'warning');
    await expect(library.locator('[data-pack-id="layout.4"] .pack-touch-area')).toHaveAccessibleDescription(/Pack uninstalled/);
    await expect(library.locator('[data-pack-id="layout.5"] .pack-touch-area')).toHaveAccessibleDescription(/Environment failed/);
    await expect(library.locator('[data-pack-id="layout.6"]')).toHaveAttribute('data-pack-notice', 'restart');
    for (let index = 0; index < presets.length; index++) {
      const pack = library.locator(`[data-pack-id="layout.${index}"]`);
      await pack.scrollIntoViewIfNeeded();
      await page.mouse.move(0, 0);
      await expect(pack).toHaveAttribute('data-renderer', renderer);
      const artwork = pack.locator(renderer === 'webgl' ? '.pack-webgl' : '.pack-cover');
      expect((await pack.locator('.pack-touch-area').boundingBox())!.height).toBeLessThan(280);
      if (index === 0) {
        await expect(artwork).toHaveCSS('opacity', '1');
        await page.screenshot({ path: testInfo.outputPath('sealed.png') });
      }
      // Record every frame, including the return to idle, to catch transient reflow.
      const sample = pack.evaluate(element => {
        const body = element.closest('.library-body')!;
        const items = [...body.querySelectorAll('.library-pack, .pack-touch-area')];
        const rects = () => items.map(item => {
          const r = item.getBoundingClientRect();
          return [r.x, r.y, r.width, r.height];
        }).flat();
        const before = rects(), scrollTop = body.scrollTop, started = performance.now();
        let displacement = 0;
        (element.querySelector('.pack-touch-area') as HTMLButtonElement).click();
        return new Promise<number>(resolve => {
          const frame = () => {
            displacement = Math.max(displacement, Math.abs(body.scrollTop - scrollTop),
              ...rects().map((value, i) => Math.abs(value - before[i])));
            if (performance.now() - started < 2400) requestAnimationFrame(frame);
            else resolve(displacement);
          };
          requestAnimationFrame(frame);
        });
      });
      await expect(pack).toHaveClass(/is-revealing/);
      await expect(pack.locator('.pack-touch-area')).toHaveCSS('opacity', '1');
      if (index === 0) {
        await page.waitForTimeout(1250);
        const dip = await artwork.evaluate(element => Number.parseFloat(getComputedStyle(element).translate.split(' ')[1]));
        expect(dip).toBeGreaterThan(48);
        expect(dip).toBeLessThan(90);
        await page.screenshot({ path: testInfo.outputPath('opening.png') });
      }
      expect(await sample, `Pack ${index} moved the grid`).toBeLessThan(.5);
      await expect(pack).toHaveClass(/is-idle/);
      await expect(artwork).toHaveCSS('translate', 'none');
    }
    await page.screenshot({ path: testInfo.outputPath('opened.png') });
    await library.locator('[data-pack-id="layout.4"]').scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath('pack-statuses.png') });
    if (renderer === 'fallback') {
      const last = library.locator('[data-pack-id="layout.7"]');
      await last.scrollIntoViewIfNeeded();
      await expect.poll(() => missingArtworkRequests).toBeGreaterThan(0);
      await expect.poll(() => last.locator('.pack-cover').evaluate((canvas: HTMLCanvasElement) => canvas.getContext('2d')!.getImageData(384, 544, 1, 1).data[3])).toBe(255);
      expect(await page.evaluate(() => performance.getEntriesByType('resource').some(entry => /\/pack3d\/renderer\./.test(entry.name)))).toBe(false);
    }
  });
}
