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
  for (const material of ['银箔', '虹彩', '星光', '镭射']) {
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
  await expect(cards.first().locator('.card-face-copy small')).toBeHidden();
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
