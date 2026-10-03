import { expect, test, type Page } from '@playwright/test';

async function stats(page: Page) {
  return page.evaluate(async () => {
    // Import the same Vite URL, including any HMR timestamp.
    const source = performance.getEntriesByType('resource').find(entry => /\/pack3d\/renderer\.ts(?:\?|$)/.test(entry.name))!.name;
    return (await import(/* @vite-ignore */ source)).packRendererStats() as {
      contexts: number; views: number; renders: number; textures: number; geometries: number;
      models: { id: string; surface: string; materials: { name: string; roughness: number; metalness: number }[] }[];
    };
  });
}

test('regional PBR, geometry inspection, drag gestures and an idle shared GPU', async ({ page }, testInfo) => {
  test.setTimeout(60000);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', m => { if (m.type() === 'error' && /THREE|shader/i.test(m.text())) errors.push(m.text()); });
  await page.setViewportSize({ width: 1440, height: 1350 });
  await page.goto('/?pack-design');
  await expect(page.locator('[data-renderer="webgl"]')).toHaveCount(5);
  const initial = await stats(page);
  expect(initial.contexts).toBe(1); expect(initial.views).toBe(5);
  const premium = initial.models.find(model => model.id === 'preview.premium')!;
  expect(premium.materials.find(m => m.name === 'pearl-laminate')!.metalness).toBe(0);
  expect(premium.materials.find(m => m.name === 'holographic-foil')!.metalness).toBeGreaterThan(.9);
  expect(initial.models.find(m => m.id === 'preview.paper')!.materials.find(m => m.name === 'recycled-paper')!.roughness).toBeGreaterThan(.9);
  const studies = page.locator('.pack-design-study'), first = studies.first(), canvas = first.locator('canvas');
  for (const [name, yaw] of [['侧面', '75.0'], ['背面', '180.0'], ['俯视', '-25.0'], ['正面', '0.0']] as const) {
    await page.getByRole('button', { name, exact: true }).click();
    await expect(canvas).toHaveAttribute('data-yaw', yaw);
    await page.screenshot({ path: testInfo.outputPath('pack-' + name + '.png') });
  }
  for (const [name, surface] of [['素模', 'clay'], ['网格', 'wireframe'], ['成品', 'material']] as const) {
    await page.getByRole('button', { name, exact: true }).click();
    await expect.poll(async () => (await stats(page)).models[0].surface).toBe(surface);
  }
  const foil = studies.nth(1).locator('canvas');
  const beforeLight = await foil.evaluate(el => (el as HTMLCanvasElement).toDataURL());
  await page.getByRole('slider', { name: '光照角度' }).press('End');
  await expect.poll(() => foil.evaluate(el => (el as HTMLCanvasElement).toDataURL())).not.toBe(beforeLight);
  const button = first.locator('.pack-touch-area'), box = (await button.boundingBox())!;
  await page.mouse.move(box.x + box.width * .88, box.y + box.height * .88);
  await expect.poll(async () => Number(await canvas.getAttribute('data-tilt-yaw'))).toBeGreaterThan(9);
  await expect.poll(async () => Number(await canvas.getAttribute('data-tilt-pitch'))).toBeGreaterThan(7);
  await page.mouse.move(box.x + box.width * .12, box.y + box.height * .12);
  await expect.poll(async () => Number(await canvas.getAttribute('data-tilt-yaw'))).toBeLessThan(-9);
  await expect.poll(async () => Number(await canvas.getAttribute('data-tilt-pitch'))).toBeLessThan(-7);
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2); await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 70, box.y + box.height / 2 + 30, { steps: 8 }); await page.mouse.up();
  await expect(first.locator('.library-pack')).not.toHaveClass(/is-opened/);
  await expect.poll(async () => Number(await canvas.getAttribute('data-yaw'))).toBeGreaterThan(30);
  await button.focus(); await page.keyboard.press('Home'); await expect(canvas).toHaveAttribute('data-yaw', '0.0');
  await page.keyboard.press('ArrowRight'); await expect(canvas).toHaveAttribute('data-yaw', '15.0');
  await page.mouse.move(0, 0); await page.waitForTimeout(400);
  const idle = await stats(page); await page.waitForTimeout(300);
  expect((await stats(page)).renders).toBe(idle.renders);
  await expect(canvas).toHaveAttribute('data-tilt-yaw', '0.00');
  await expect(canvas).toHaveAttribute('data-tilt-pitch', '0.00');
  for (let i = 0; i < 3; i++) {
    await page.getByRole('button', { name: '重置拆包' }).click();
    await expect(page.locator('[data-renderer="webgl"]')).toHaveCount(5);
  }
  await page.waitForTimeout(400);
  const reset = await stats(page);
  expect(reset.contexts).toBe(1); expect(reset.views).toBe(5);
  expect(reset.textures).toBeLessThanOrEqual(idle.textures + 1);
  expect(reset.geometries).toBeLessThanOrEqual(idle.geometries + 1);
  expect(errors).toEqual([]);
});

test('presets open, scrub, reset and fit small screens without backend requests', async ({ page }, testInfo) => {
  test.setTimeout(60000);
  const apiRequests: string[] = [];
  page.on('request', r => { if (new URL(r.url()).pathname.startsWith('/api/')) apiRequests.push(r.url()); });
  await page.setViewportSize({ width: 1440, height: 1350 });
  await page.goto('/?pack-design');
  const studies = page.locator('.pack-design-study');
  await expect(studies.locator('[data-renderer="webgl"]')).toHaveCount(4);
  for (const preset of ['standard', 'premium', 'paper', 'collector']) {
    const study = studies.filter({ has: page.locator('[data-packaging="' + preset + '"]') });
    await study.locator('.pack-design-choose').click();
    await expect(page.locator('.pack-design-config pre')).toContainText('packaging="' + preset + '"');
    await expect(page.locator('.pack-design-opened .library-pack')).toHaveAttribute('data-packaging', preset);
    await study.locator('.pack-touch-area').click();
    await expect(study.locator('.library-pack')).toHaveClass(/is-opened is-revealing/);
    await expect(study.locator('.pack-drawn-card').first()).toBeHidden();
    await expect(study.locator('.pack-touch-area')).toBeDisabled();
    await expect(study.locator('.pack-touch-area')).toBeEnabled();
    await expect(study.locator('canvas')).toHaveAttribute('data-opening', '1.000');
  }
  await page.getByRole('slider', { name: '拆包进度' }).press('End');
  await expect(page.locator('.pack-design-opened canvas')).toHaveAttribute('data-opening', '1.000');
  await page.getByRole('slider', { name: '拆包进度' }).press('Home');
  await expect(page.locator('.pack-design-opened canvas')).toHaveAttribute('data-opening', '0.000');
  await page.getByRole('button', { name: '俯视', exact: true }).click();
  await page.screenshot({ path: testInfo.outputPath('opened-interiors.png') });
  await page.getByRole('button', { name: '重置拆包' }).click();
  await expect(studies.locator('.library-pack.is-opened')).toHaveCount(0);
  await page.getByRole('button', { name: '深色预览' }).click();
  await page.screenshot({ path: testInfo.outputPath('dark.png') });
  await page.getByLabel('卡库尺寸').check();
  await page.screenshot({ path: testInfo.outputPath('library-size.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByLabel('卡库尺寸').uncheck();
  expect(await page.locator('.pack-design-preview').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await studies.first().locator('.pack-touch-area').click();
  await expect(studies.first().locator('canvas')).toHaveAttribute('data-opening', '0.600');
  await page.screenshot({ path: testInfo.outputPath('mobile-reduced-motion.png') });
  await expect(studies.first().locator('.pack-touch-area')).toBeEnabled();
  await expect(studies.first().locator('canvas')).toHaveAttribute('data-opening', '1.000');
  expect(apiRequests).toEqual([]);
});

test('keeps opening accessible when WebGL is unavailable', async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(window, 'WebGL2RenderingContext', { value: undefined }));
  await page.goto('/?pack-design');
  const pack = page.locator('.pack-design-study').first().locator('.library-pack');
  await expect(pack).toHaveAttribute('data-renderer', 'fallback');
  await expect(pack.locator('.pack-title')).toBeVisible();
  await pack.locator('button').focus(); await page.keyboard.press('Enter');
  await expect(pack).toHaveClass(/is-opened/);
  await expect(pack.locator('button')).toBeEnabled();
});

test('waits for the first 3D frame without flashing the legacy cover', async ({ page }) => {
  let release!: () => void;
  const blocked = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/pack3d/renderer.ts*', async route => { await blocked; await route.continue(); });
  await page.goto('/?pack-design', { waitUntil: 'domcontentloaded' });
  const pack = page.locator('.pack-design-study').first().locator('.library-pack');
  await expect(pack).toHaveAttribute('data-renderer', 'loading');
  await expect(pack.locator('.pack-fallback')).toBeHidden();
  await expect(pack.locator('canvas')).toHaveCSS('opacity', '0');
  await expect(pack.locator('.pack-loading')).toBeVisible();
  release();
  await expect(pack).toHaveAttribute('data-renderer', 'webgl');
  await expect(pack.locator('canvas')).toHaveCSS('opacity', '1');
  await expect(pack.locator('.pack-fallback')).toBeHidden();
});

test('recovers after a lost GPU context', async ({ page }) => {
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (...args: Parameters<typeof original>) {
      const result = original.apply(this, args);
      if (args[0] === 'webgl2') (window as unknown as { packTestExtension: unknown }).packTestExtension = (result as WebGL2RenderingContext).getExtension('WEBGL_lose_context');
      return result;
    } as typeof original;
  });
  await page.goto('/?pack-design');
  const pack = page.locator('.pack-design-study').first().locator('.library-pack');
  await expect(pack).toHaveAttribute('data-renderer', 'webgl');
  await page.evaluate(() => (window as unknown as { packTestExtension: WEBGL_lose_context }).packTestExtension.loseContext());
  await expect(pack).toHaveAttribute('data-renderer', 'fallback');
  await expect(pack.locator('.pack-title')).toBeVisible();
  await page.waitForTimeout(200);
  await page.evaluate(() => (window as unknown as { packTestExtension: WEBGL_lose_context }).packTestExtension.restoreContext());
  await expect(pack).toHaveAttribute('data-renderer', 'webgl');
});

test('skips unchanged views, restores cached frames and releases hidden GPU resources', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1350 });
  await page.goto('/?pack-design');
  await expect(page.locator('[data-renderer="webgl"]')).toHaveCount(5);
  await page.waitForTimeout(500);
  const idle = await stats(page);
  await page.getByRole('button', { name: '深色预览' }).click();
  await page.waitForTimeout(300);
  expect((await stats(page)).renders).toBe(idle.renders);
  const density = await page.locator('.pack-webgl').first().evaluate((canvas: HTMLCanvasElement) => canvas.width / canvas.clientWidth);
  expect(density).toBeCloseTo(1, 1);

  // Observe a remount at the moment the cached bitmap appears, before the
  // shared renderer has drawn any of the new canvases.
  const restored = await page.evaluate(async () => {
    const source = performance.getEntriesByType('resource').find(entry => /\/pack3d\/renderer\.ts(?:\?|$)/.test(entry.name))!.name;
    const renderer = await import(/* @vite-ignore */ source);
    const before = renderer.packRendererStats().renders;
    const previous = document.querySelector('.pack-webgl') as HTMLCanvasElement;
    const pixels = previous.toDataURL();
    return new Promise<{ samePixels: boolean; renders: number }>(resolve => {
      const observer = new MutationObserver(() => {
        const canvas = document.querySelector('[data-renderer="webgl"] .pack-webgl') as HTMLCanvasElement | null;
        if (!canvas || canvas === previous) return;
        observer.disconnect(); resolve({ samePixels: canvas.toDataURL() === pixels, renders: renderer.packRendererStats().renders - before });
      });
      observer.observe(document.body, { childList: true, attributes: true, subtree: true });
      (Array.from(document.querySelectorAll('button')).find(button => button.textContent?.includes('重置拆包'))!).click();
    });
  });
  expect(restored).toEqual({ samePixels: true, renders: 0 });
  const hide = (hidden: boolean) => page.locator('.pack-design-grid, .pack-design-details').evaluateAll((elements, hidden) => elements.forEach(element => { (element as HTMLElement).style.display = hidden ? 'none' : ''; }), hidden);
  await hide(true);
  await expect.poll(async () => (await stats(page)).contexts).toBe(0);
  await hide(false);
  await expect.poll(async () => (await stats(page)).models.length).toBe(5);
  await expect(page.locator('[data-renderer="webgl"]')).toHaveCount(5);
});
