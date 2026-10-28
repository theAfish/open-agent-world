import { expect, test } from '@playwright/test';
import type { StorePack } from '../src/types/packs';

for (const width of [1280, 390]) for (const renderer of ['webgl', 'fallback']) {
  test(`Library and Store share compact layouts at ${width}px with ${renderer}`, async ({ page, request }, testInfo) => {
    test.setTimeout(60000);
    await page.setViewportSize({ width, height: 900 });
    if (renderer === 'fallback') await page.addInitScript(() => {
      Object.defineProperty(window, 'WebGL2RenderingContext', { value: undefined });
    });
    const profile = await (await request.get('/api/application')).json();
    await request.patch('/api/application/preferences', { data: {
      profile_id: profile.profile_id, generation: profile.generation,
      changes: { 'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en' },
    } });
    const packs: StorePack[] = ['standard', 'premium', 'paper', 'collector'].map(packaging => ({
      id: `preview.${packaging}`, name: packaging === 'paper' ? 'A very long multilingual Pack name · 多语言科学工作流与知识库'
        : packaging === 'premium' ? 'Scientific workflows · 多语言知识库与实验分析' : `${packaging} Pack`,
      summary: 'A collection of possibilities.', description: `Store preview fixture.\n\n${'A long description for scientific workflows and shared knowledge. '.repeat(12)}`,
      latest_version: '1.0.0', available_version: '1.0.0', installed_version: packaging === 'premium' ? '0.9.0' : packaging === 'paper' ? '1.0.0' : null,
      loaded_version: packaging === 'premium' ? '0.9.0' : null,
      can_install: packaging !== 'paper', update_available: packaging === 'premium', restart_required: packaging === 'paper',
      preview: { packaging: packaging as NonNullable<StorePack['preview']>['packaging'], count: 3 },
    }));
    const mutations: string[] = [], errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/store/packs**', route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      if (req.method() !== 'GET') mutations.push(path);
      const pack = packs.find(pack => path.startsWith(`/api/store/packs/${pack.id}`));
      if (!pack) return route.fulfill({ json: { items: packs, next_cursor: null } });
      if (path.includes('/versions/')) return route.fulfill({ json: {
        ...pack, pack_id: pack.id, version: '1.0.0', preview: { packaging: 'collector', count: 3 },
        manifest: { id: pack.id, version: '1.0.0', compatibility: { oaw: '>=0.1.0' },
          dependencies: { packs: [] }, runtime: { sandbox: { python: [] } } },
      } });
      return route.fulfill({ json: { ...pack, versions: ['1.0.0'], versions_next_cursor: null } });
    });
    await page.goto('/');
    await page.getByRole('button', { name: 'Open Pack and Card Library' }).click();
    const library = page.getByRole('dialog', { name: 'Pack & Card Library' });
    const libraryPack = library.locator('.pack-grid > .library-pack').first();
    await expect(libraryPack).toBeVisible();
    const librarySize = await libraryPack.locator('.pack-touch-area').boundingBox();
    await page.screenshot({ path: testInfo.outputPath('library-catalog.png') });
    await library.locator('.pack-grid .pack-catalog-link').first().click();
    const installedPreview = library.locator('.installed-pack-detail .pack-detail-header .library-pack');
    await expect(installedPreview).toHaveAttribute('data-renderer', renderer);
    if (renderer === 'webgl') await expect(installedPreview.locator('.pack-webgl')).toHaveCSS('opacity', '1');
    await expect(library.getByRole('complementary', { name: 'Pack actions' })).toBeVisible();
    expect(await library.locator('.library-body').evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
    await page.screenshot({ path: testInfo.outputPath('library-details.png') });
    await library.getByRole('button', { name: 'Back to packs' }).click();
    await library.getByRole('button', { name: 'Store', exact: true }).click();
    for (const pack of packs) {
      const surface = library.locator(`[data-pack-id="${pack.id}"]`);
      await surface.scrollIntoViewIfNeeded();
      await expect(surface).toHaveAttribute('data-renderer', renderer, { timeout: 15000 });
      await expect(surface).toHaveAttribute('data-packaging', pack.preview!.packaging);
      if (renderer === 'fallback') await expect(surface.locator('.pack-cover')).toBeInViewport({ ratio: 1 });
    }
    const storeSize = await library.locator('.store-pack .pack-touch-area').first().boundingBox();
    expect(storeSize!.height).toBe(218);
    expect(storeSize!.height).toBeCloseTo(librarySize!.height, 2);
    expect(Math.abs(storeSize!.width - librarySize!.width)).toBeLessThan(1);
    expect((await library.locator('.pack-catalog-copy').allTextContents()).join(' ')).not.toContain(packs[0].summary);
    await expect(library.locator('.store-pack .library-pack').first()).toHaveAttribute('data-pack-version', '1.0.0');
    await expect(library.locator('.store-pack .pack-touch-area').first()).toHaveAccessibleDescription(/Version 1.0.0/);
    await expect(library.locator('[data-pack-id="preview.premium"]')).toHaveAttribute('data-pack-notice', 'update');
    await expect(library.locator('[data-pack-id="preview.paper"]')).toHaveAttribute('data-pack-notice', 'restart');
    await expect(library.locator('.pack-catalog-copy .pack-tag, .pack-catalog-copy .store-installation')).toHaveCount(0);
    await library.locator('.pack-store').evaluate(element => element.closest('.library-body')!.scrollTop = 0);
    await page.screenshot({ path: testInfo.outputPath('store-procedural-packs.png') });
    const premium = library.locator('[data-pack-id="preview.premium"]');
    const touch = premium.getByRole('button', { name: `View ${packs[1].name} details` });
    await touch.focus();
    if (renderer === 'webgl') {
      await touch.press('ArrowRight');
      await expect(premium.locator('canvas')).toHaveAttribute('data-yaw', '8.0');
    }
    await touch.press('Enter');
    await expect(library.locator('.store-detail .library-pack')).toHaveAttribute('data-packaging', 'collector');
    await expect(library.locator('.store-detail .library-pack')).toHaveAttribute('data-renderer', renderer);
    if (renderer === 'webgl') await expect(library.locator('.store-detail .pack-webgl')).toHaveCSS('opacity', '1');
    await expect(library.getByRole('complementary', { name: 'Install Pack' })).toBeVisible();
    await expect(library.getByRole('heading', { name: 'About this Pack' })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('store-details.png') });
    expect(await library.locator('.library-body').evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
    await expect(library.getByRole('button', { name: 'Update', exact: true })).toBeEnabled();
    expect(mutations).toEqual([]);
    await library.getByRole('button', { name: 'Back to Store' }).click();
    await expect(library.locator('[data-pack-id="preview.premium"]')).toHaveAttribute('data-packaging', 'premium');
    expect(errors).toEqual([]);
  });
}
