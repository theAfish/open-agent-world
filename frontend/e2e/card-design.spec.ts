import { expect, test } from '@playwright/test';

test('design presets stay readable across materials, themes and thumbnail sizes', async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/?card-design');
  const cards = page.locator('.design-example');
  await expect(cards).toHaveCount(6);
  const image = cards.locator('.card-face-image');
  await expect.poll(() => image.evaluate(element => (element as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  await page.screenshot({ path: testInfo.outputPath('card-design-light.png'), fullPage: true });
  for (const material of ['烫箔', '虹彩', '星光', '镭射']) {
    await page.getByRole('button', { name: material, exact: true }).click();
    await expect(cards.locator('.card-finish-layer')).toHaveCount(6);
    for (const title of await cards.locator('.card-face-copy strong').all()) await expect(title).toBeVisible();
  }
  await page.screenshot({ path: testInfo.outputPath('card-design-laser.png'), fullPage: true });
  await page.getByRole('button', { name: '深色主题' }).click();
  await page.screenshot({ path: testInfo.outputPath('card-design-dark.png'), fullPage: true });
  await page.getByRole('button', { name: '原纸', exact: true }).click();
  await expect(cards.locator('.card-finish-layer')).toHaveCount(0);
  await page.getByRole('button', { name: '深色主题' }).click();
  await page.getByLabel('缩略卡').check();
  await expect(cards.nth(3).locator('.card-face-copy small')).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('card-design-thumbnails.png'), fullPage: true });
  await page.getByLabel('缩略卡').uncheck();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
  for (const card of await cards.all()) {
    expect(await card.evaluate(element => {
      const box = element.getBoundingClientRect();
      const title = element.querySelector('.card-face-copy strong')!.getBoundingClientRect();
      return title.top >= box.top && title.bottom <= box.bottom && title.left >= box.left && title.right <= box.right;
    })).toBe(true);
  }
  await page.screenshot({ path: testInfo.outputPath('card-design-mobile.png'), fullPage: true });
  expect(errors).toEqual([]);
});

for (const fallback of [false, true]) test(`default collection laminate covers the whole stock without changing authored masks (${fallback ? 'CPU' : 'GPU'})`, async ({ page }, info) => {
  if (fallback) await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (type: string, ...args: unknown[]) {
      if (type === 'webgl' || type === 'webgl2') return null;
      return original.call(this, type, ...args);
    } as typeof original;
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/?card-design');
  await page.getByRole('button', { name: '虹彩', exact: true }).click();
  await expect(page.locator('[data-material-ready]')).toHaveCount(6);
  const cards = page.locator('.design-example');
  const coverage = (index: number) => cards.nth(index).locator('canvas').evaluate(element => {
    const canvas = element as HTMLCanvasElement, context = canvas.getContext('2d')!;
    // Include the paper margins and the formerly excluded brand/title bands.
    return [[.04, .5], [.96, .5], [.5, .04], [.5, .96], [.5, .12], [.5, .86]].map(([x, y]) =>
      context.getImageData(Math.floor(x * canvas.width), Math.floor(y * canvas.height), 1, 1).data[3]);
  });
  for (const compact of [false, true]) {
    await page.getByLabel('缩略卡').setChecked(compact);
    for (const index of [0, 1, 2]) {
      await expect(cards.nth(index).locator('[data-material-renderer]')).toHaveAttribute('data-material-renderer', fallback ? 'fallback' : 'webgl');
      await expect.poll(async () => (await coverage(index)).every(alpha => alpha > 2)).toBe(true);
    }
    // The authored image face still coats its art, leaving paper and copy matte.
    await expect.poll(async () => {
      const authored = await cards.nth(3).locator('canvas').evaluate(element => {
        const canvas = element as HTMLCanvasElement, context = canvas.getContext('2d')!;
        const bounds = canvas.getBoundingClientRect(), host = canvas.closest('.card-stock')!;
        const alpha = (selector: string) => {
          const box = host.querySelector(selector)!.getBoundingClientRect();
          return context.getImageData(Math.floor((box.x + box.width / 2 - bounds.x) / bounds.width * canvas.width),
            Math.floor((box.y + box.height / 2 - bounds.y) / bounds.height * canvas.height), 1, 1).data[3];
        };
        return { title: alpha('.card-face-copy'), brand: alpha('.card-face-edition'), artwork: alpha('.card-face-art') };
      });
      return authored.title === 0 && authored.brand === 0 && authored.artwork > 2 && (await coverage(3))[0] === 0;
    }).toBe(true);
  }
  await page.getByLabel('缩略卡').uncheck();
  await page.getByRole('button', { name: '深色主题' }).click();
  await page.mouse.move(1, 1);
  await page.screenshot({ path: info.outputPath('continuous-default-laminate.png'), animations: 'disabled' });
  await page.getByRole('button', { name: '原纸', exact: true }).click();
  await expect(page.locator('.card-finish-layer')).toHaveCount(0);
});
