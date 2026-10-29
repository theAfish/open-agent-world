import { expect, test } from '@playwright/test';

for (const gpu of [true, false]) test(`uses packaging-specific opening movement in ${gpu ? 'WebGL' : 'flat'} mode`, async ({ page }) => {
  test.setTimeout(60_000);
  if (!gpu) await page.addInitScript(() => Object.defineProperty(window, 'WebGL2RenderingContext', { value: undefined }));
  await page.setViewportSize({ width: 1440, height: 1350 });
  for (const reducedMotion of ['no-preference', 'reduce'] as const) {
    await page.emulateMedia({ reducedMotion });
    await page.goto('/?pack-design');
    const studies = page.locator('.pack-design-study');
    await expect(studies.locator(`[data-renderer="${gpu ? 'webgl' : 'fallback'}"]`)).toHaveCount(4);
    for (const packaging of ['standard', 'premium', 'paper', 'collector']) {
      const pack = studies.locator(`[data-packaging="${packaging}"]`);
      await pack.locator('.pack-touch-area').click();
      const movement = await pack.evaluate(element => {
        // Inspect an actual reveal at a stable frame, before its owner settles.
        element.getAnimations({ subtree: true }).forEach(animation => {
          animation.pause(); animation.currentTime = 1100;
        });
        return ['.pack-webgl', '.pack-cover', '.pack-reveal-cards'].map(selector => {
          const surface = element.querySelector(selector);
          return surface ? getComputedStyle(surface).translate : null;
        });
      });
      const displacement = packaging === 'collector' ? '0px' : '0px 72px';
      expect(movement[gpu ? 0 : 1], `${packaging}, ${reducedMotion}`).toBe(displacement);
      if (gpu) expect(movement[2]).toBe(displacement);
      await expect(pack.locator('.pack-touch-area')).toBeEnabled();
    }
  }
});

for (const gpu of [true, false]) test(`shares compact card faces during a ${gpu ? 'WebGL' : 'flat'} reveal`, async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  if (!gpu) await page.addInitScript(() => Object.defineProperty(window, 'WebGL2RenderingContext', { value: undefined }));
  await page.setViewportSize({ width: 1440, height: 1350 });
  await page.goto('/?pack-design');
  const scene = page.locator('.pack-design-opened-scene');
  const pack = scene.locator('.library-pack');
  const progress = page.locator('.pack-design-scrub input');
  const setProgress = async (value: number) => {
    await progress.fill(String(value));
    if (gpu) await expect(pack.locator('.pack-webgl')).toHaveAttribute('data-opening', value.toFixed(3));
  };
  await expect(pack).toHaveAttribute('data-renderer', gpu ? 'webgl' : 'fallback');
  await setProgress(.77);
  // Renderer readiness precedes the native layer's entrance transition.
  await expect(pack.locator('.pack-reveal-cards')).toHaveCSS('opacity', '1');
  await expect(pack.locator('.pack-reveal-cards')).toHaveCSS('transform', 'none');
  const cards = pack.locator('.pack-reveal-card');
  await expect(cards).toHaveCount(1);
  const card = cards.first().locator('.card-stock--compact');
  await expect(card.locator('.card-face-edition b')).toHaveText('OAW');
  await expect(card.locator('.card-face-copy strong')).toHaveText('Explore More');
  await expect(card.locator('[data-design="archival"] .card-face-symbol')).toBeVisible();
  await expect(card.locator('.card-print-art')).toHaveCount(0);
  await expect(card).toHaveAttribute('data-finish', 'foil');
  await expect(card.locator('.card-finish-layer')).toHaveAttribute('data-material-ready', '');
  await expect(card.locator('.card-face-copy small')).toBeHidden();
  await expect(pack.locator('.pack-cover-card')).toHaveCount(0);
  await cards.first().screenshot({ path: testInfo.outputPath('shared-card.png') });
  // Check the composite: valid DOM and zero canvas alpha can still look blank
  // if a GPU mask writes unpremultiplied RGB into the face.
  const printedTitle = await card.locator('.card-face-copy strong').screenshot();
  // Compare against the exact DOM glyphs at this projected size. A fixed count
  // of near-black pixels depends on font weight and subpixel placement.
  const canvas = pack.locator('.pack-webgl');
  await canvas.evaluate(element => { (element as HTMLElement).style.visibility = 'hidden'; });
  const referenceTitle = await card.locator('.card-face-copy strong').screenshot();
  await canvas.evaluate(element => { (element as HTMLElement).style.removeProperty('visibility'); });
  const ink = await page.evaluate(async ([actual, reference]) => {
    const pixels = async (base64: string) => {
      const image = new Image(); image.src = `data:image/png;base64,${base64}`; await image.decode();
      const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
      const context = canvas.getContext('2d')!; context.drawImage(image, 0, 0);
      return context.getImageData(0, 0, canvas.width, canvas.height).data;
    };
    const [composite, print] = await Promise.all([pixels(actual), pixels(reference)]);
    let referenceInk = 0, matchedInk = 0;
    for (let i = 0; i < print.length; i += 4) {
      if (print[i] >= 190 || print[i + 1] >= 190 || print[i + 2] >= 190) continue;
      referenceInk++;
      if ([0, 1, 2].every(channel => Math.abs(print[i + channel] - composite[i + channel]) < 8)) matchedInk++;
    }
    return { referenceInk, matchedInk };
  }, [printedTitle.toString('base64'), referenceTitle.toString('base64')]);
  expect(ink.referenceInk).toBeGreaterThan(5);
  expect(ink.matchedInk / ink.referenceInk).toBeGreaterThan(.9);

  if (gpu) {
    const transparentAtCardCenter = () => pack.locator('.pack-webgl').evaluate((canvas: HTMLCanvasElement) => {
      const card = canvas.parentElement!.querySelector('.pack-reveal-card')!;
      const box = card.getBoundingClientRect(), frame = canvas.getBoundingClientRect();
      const x = Math.round((box.x + box.width / 2 - frame.x) * canvas.width / frame.width);
      const y = Math.round((box.y + box.height / 2 - frame.y) * canvas.height / frame.height);
      return canvas.getContext('2d')!.getImageData(x, y, 1, 1).data[3];
    });
    // A visible face is a depth-tested opening onto the real DOM card.
    await expect.poll(transparentAtCardCenter).toBe(0);
    await setProgress(.05);
    // The same face remains behind the unopened wrapper until it emerges.
    await expect.poll(transparentAtCardCenter).toBe(255);
    await setProgress(.77);
    for (const viewport of [{ width: 600, height: 780 }, { width: 1280, height: 600 }]) {
      await page.setViewportSize(viewport);
      await scene.scrollIntoViewIfNeeded();
      await expect.poll(transparentAtCardCenter).toBe(0);
      await scene.screenshot({ path: testInfo.outputPath(`shared-card-${viewport.width}-${viewport.height}.png`) });
    }
  }
  await setProgress(1);
  await expect(cards).toHaveCount(0);
  await setProgress(0);
  await expect(cards).toHaveCount(0);
  expect(errors).toEqual([]);
});
