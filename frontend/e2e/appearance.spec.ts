import { expect, test } from '@playwright/test';

test.beforeEach(async ({ request, page }) => {
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation,
    changes: {
      'oaw.locale': 'en', 'oaw-theme': 'light',
      'oaw-appearance': JSON.stringify({ textSize: 100, wheelShortcut: true }),
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
      'oaw-canvas-viewport-v1': JSON.stringify({ version: 0, state: {
        viewport: { x: 0, y: 0, zoom: 1, width: 1280, height: 800 }, mapPins: [],
      } }),
    },
  } })).ok()).toBe(true);
  await page.goto('/');
  await page.getByRole('button', { name: 'Open settings', exact: true }).click();
  await page.getByRole('button', { name: 'Appearance', exact: true }).click();
});

test('scales text independently, persists after reload and keeps canvas zoom separate', async ({ page, request }, info) => {
  const dialog = page.getByRole('dialog', { name: 'Settings', exact: true });
  const preview = dialog.locator('.appearance-preview');
  const slider = dialog.getByRole('slider', { name: 'Text size' });
  const closeIcon = dialog.getByRole('button', { name: 'Close settings' }).locator('svg');
  const width = (await dialog.boundingBox())!.width;
  await expect(preview).toHaveCSS('font-size', '13px');
  await slider.fill('150');
  await expect(preview).toHaveCSS('font-size', '19.5px');
  expect((await dialog.boundingBox())!.width).toBe(width);
  await expect(closeIcon).toHaveCSS('width', '16px');
  await dialog.getByRole('combobox', { name: 'Theme', exact: true }).click();
  await page.getByRole('listbox', { name: 'Theme' }).getByRole('option', { name: 'Dark', exact: true }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.screenshot({ path: info.outputPath('appearance-dark-150.png'), animations: 'disabled' });
  await expect.poll(async () => {
    const profile = await (await request.get('/api/application')).json();
    return JSON.parse(profile.values['oaw-appearance']).textSize;
  }).toBe(150);
  await page.reload();
  await page.getByRole('button', { name: 'Open settings', exact: true }).click();
  await page.getByRole('button', { name: 'Appearance', exact: true }).click();
  await expect(slider).toHaveValue('150');
  await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  const viewport = page.locator('#oaw-world-map .react-flow__viewport').first();
  const transform = await viewport.getAttribute('style');
  await page.mouse.move(650, 160);
  await page.keyboard.down('Control');
  await page.mouse.wheel(0, -100);
  await expect(page.locator('html')).toHaveCSS('font-size', '25.6px');
  await page.keyboard.up('Control');
  expect(await viewport.getAttribute('style')).toBe(transform);
  await page.mouse.wheel(0, -100);
  await expect(viewport).not.toHaveAttribute('style', transform!);
  await expect(page.locator('html')).toHaveCSS('font-size', '25.6px');
});

test('keeps maximum text size and reset accessible on a narrow screen in both languages', async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 700 });
  const dialog = page.getByRole('dialog', { name: 'Settings', exact: true });
  await dialog.getByRole('slider', { name: 'Text size' }).fill('200');
  await expect(dialog.getByRole('button', { name: 'Reset text size' })).toBeInViewport();
  await expect(dialog.getByRole('button', { name: 'Close', exact: true })).toBeInViewport();
  expect(await dialog.locator('.settings-content').evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('appearance-narrow-200.png'), animations: 'disabled' });
  await dialog.getByLabel('Language', { exact: true }).selectOption('zh-CN');
  const chineseDialog = page.getByRole('dialog', { name: '设置', exact: true });
  await expect(chineseDialog.getByRole('heading', { name: '外观' })).toBeVisible();
  await chineseDialog.getByLabel('Ctrl + 鼠标滚轮调整文字大小').uncheck();
  await page.screenshot({ path: info.outputPath('appearance-chinese-200.png'), animations: 'disabled' });
  await chineseDialog.getByRole('button', { name: '恢复默认字号' }).click();
  await expect(chineseDialog.getByRole('slider', { name: '文字大小' })).toHaveValue('100');
});
