import { expect, test } from '@playwright/test';

test('bundled documentation works with no external network, native modal focus and responsive layouts', async ({ page }) => {
  test.setTimeout(90_000);
  await page.addInitScript(() => {
    localStorage.setItem('oaw.locale', 'en');
    localStorage.setItem('oaw-onboarding-v1', JSON.stringify({ state: { status: 'skipped' }, version: 1 }));
  });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const external: string[] = [];
  await page.route('**/*', route => {
    if (new URL(route.request().url()).hostname !== '127.0.0.1') {
      external.push(route.request().url()); return route.abort();
    }
    return route.continue();
  });
  await page.goto('/');
  const help = page.getByRole('button', { name: 'Help', exact: true });
  await help.click();
  await page.getByRole('menuitem', { name: 'Documentation' }).click();
  const dialog = page.getByRole('dialog', { name: 'Documentation' });
  await expect(dialog).toBeVisible();
  expect((await dialog.boundingBox())!.width).toBeGreaterThan(1000);
  await expect(dialog.getByRole('heading', { name: 'Use Open Agent World', level: 1 })).toBeVisible();
  await dialog.getByRole('link', { name: 'Build your first team' }).click();
  const image = dialog.getByRole('img', { name: 'Connecting cards and choosing a relationship' });
  await image.scrollIntoViewIfNeeded();
  await expect.poll(() => image.evaluate(element => (element as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  await dialog.getByRole('button', { name: 'Previous topic' }).click();
  const search = dialog.getByRole('searchbox', { name: 'Search documentation' });
  await search.fill('Install Pack from File');
  await dialog.getByRole('button', { name: 'Packs and cards', exact: true }).click();
  await expect(dialog.getByRole('heading', { name: 'Install a local Pack' })).toBeVisible();
  await search.fill('');
  await page.screenshot({ path: 'test-results/documentation-light.png' });
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(help).toBeFocused();
  await page.getByRole('button', { name: 'Use dark theme', exact: true }).click();
  await page.getByRole('button', { name: '切换到中文', exact: true }).click();
  await page.getByRole('button', { name: '帮助', exact: true }).click();
  await page.getByRole('menuitem', { name: '文档', exact: true }).click();
  const chinese = page.getByRole('dialog', { name: '文档', exact: true });
  await expect(chinese.getByRole('heading', { name: '使用入门', level: 1 })).toBeVisible();
  await page.screenshot({ path: 'test-results/documentation-zh-dark.png' });
  for (const width of [800, 390]) {
    await page.setViewportSize({ width, height: 800 });
    const bounds = await chinese.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
    expect(await chinese.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    await chinese.getByRole('searchbox').fill('oawpack');
    await chinese.getByRole('button', { name: 'Packs and cards', exact: true }).click();
    await expect(chinese.getByRole('heading', { name: 'Packs and cards', level: 1 })).toBeVisible();
    await chinese.getByRole('searchbox').fill('');
    await page.screenshot({ path: `test-results/documentation-${width}.png` });
  }
  expect(external).toEqual([]);
});
