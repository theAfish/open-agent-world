import { expect, test } from '@playwright/test';

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
  const cards = pack.locator('.pack-reveal-card');
  await expect(cards).toHaveCount(1);
  const card = cards.first().locator('.card-stock--compact');
  await expect(card.locator('.card-face-edition b')).toHaveText('OAW');
  await expect(card.locator('.card-face-copy strong')).toHaveText('Explore More');
  await expect(card.locator('.card-print-art')).toBeVisible();
  await expect(card).toHaveAttribute('data-finish', 'foil');
  await expect(card.locator('.card-finish-layer')).toHaveAttribute('data-material-ready', '');
  await expect(card.locator('.card-face-copy small')).toBeHidden();
  await expect(pack.locator('.pack-cover-card')).toHaveCount(0);
  await cards.first().screenshot({ path: testInfo.outputPath('shared-card.png') });
  // Check the composite: valid DOM and zero canvas alpha can still look blank
  // if a GPU mask writes unpremultiplied RGB into the face.
  const printedTitle = await card.locator('.card-face-copy strong').screenshot();
  const darkPixels = await page.evaluate(async base64 => {
    const image = new Image(); image.src = `data:image/png;base64,${base64}`; await image.decode();
    const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
    const context = canvas.getContext('2d')!; context.drawImage(image, 0, 0);
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
    let count = 0;
    for (let i = 0; i < pixels.length; i += 4) if (pixels[i] < 120 && pixels[i + 1] < 120 && pixels[i + 2] < 120) count++;
    return count;
  }, printedTitle.toString('base64'));
  expect(darkPixels).toBeGreaterThan(5);

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
