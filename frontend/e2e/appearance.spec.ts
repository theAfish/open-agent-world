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
  await dialog.getByRole('button', { name: 'Reset text size' }).scrollIntoViewIfNeeded();
  await expect(dialog.getByRole('button', { name: 'Reset text size' })).toBeInViewport();
  await expect(dialog.getByRole('button', { name: 'Close', exact: true })).toBeInViewport();
  expect(await dialog.locator('.settings-content').evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('appearance-narrow-200.png'), animations: 'disabled' });
  await dialog.getByLabel('Language', { exact: true }).selectOption('zh-CN');
  const chineseDialog = page.getByRole('dialog', { name: '设置', exact: true });
  await chineseDialog.getByRole('heading', { name: '外观' }).scrollIntoViewIfNeeded();
  await expect(chineseDialog.getByRole('heading', { name: '外观' })).toBeVisible();
  await chineseDialog.getByLabel('Ctrl + 鼠标滚轮调整文字大小').uncheck();
  await page.screenshot({ path: info.outputPath('appearance-chinese-200.png'), animations: 'disabled' });
  await chineseDialog.getByRole('button', { name: '恢复默认字号' }).click();
  await expect(chineseDialog.getByRole('slider', { name: '文字大小' })).toHaveValue('100');
});

test('changes palettes and canvas backgrounds independently in light and dark modes', async ({ page }, info) => {
  const dialog = page.getByRole('dialog', { name: 'Settings', exact: true });
  const root = page.locator('html');
  await dialog.getByRole('radio', { name: 'Ocean', exact: true }).click();
  await dialog.getByRole('radio', { name: 'Grid', exact: true }).click();
  await expect(root).toHaveAttribute('data-palette', 'oaw/ocean');
  await expect(root).toHaveAttribute('data-background', 'oaw/grid');
  await expect(page.locator('.terrain-webgl-background')).toHaveCount(0);
  await expect(page.locator('.canvas-background[data-background-kind="grid"] svg.react-flow__background')).toHaveCount(1);
  const oceanAccent = await root.evaluate(element => getComputedStyle(element).getPropertyValue('--accent').trim());
  await page.screenshot({ path: info.outputPath('skins-ocean-grid-light.png'), animations: 'disabled' });
  await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('skins-canvas-ocean-grid.png'), animations: 'disabled' });
  await page.getByRole('button', { name: 'Open settings', exact: true }).click();
  await page.getByRole('button', { name: 'Appearance', exact: true }).click();

  await dialog.getByRole('combobox', { name: 'Theme', exact: true }).click();
  await page.getByRole('listbox', { name: 'Theme' }).getByRole('option', { name: 'Dark', exact: true }).click();
  await expect(root).toHaveAttribute('data-theme', 'dark');
  await expect.poll(() => root.evaluate(element => getComputedStyle(element).getPropertyValue('--accent').trim())).not.toBe(oceanAccent);
  await expect(root).toHaveAttribute('data-background', 'oaw/grid');
  await dialog.getByRole('radio', { name: 'Forest', exact: true }).click();
  await dialog.getByRole('radio', { name: 'Gradient', exact: true }).click();
  await expect(root).toHaveAttribute('data-palette', 'oaw/forest');
  await expect(root).toHaveAttribute('data-background', 'oaw/gradient');
  await expect(page.locator('.canvas-background[data-background-kind="gradient"]')).toHaveCSS('background-image', /linear-gradient/);
  await page.screenshot({ path: info.outputPath('skins-forest-gradient-dark.png'), animations: 'disabled' });
  await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('skins-canvas-forest-gradient.png'), animations: 'disabled' });
  await page.getByRole('button', { name: 'Open settings', exact: true }).click();
  await page.getByRole('button', { name: 'Appearance', exact: true }).click();

  await page.setViewportSize({ width: 390, height: 700 });
  await dialog.getByRole('radio', { name: 'Forest', exact: true }).scrollIntoViewIfNeeded();
  expect(await dialog.locator('.settings-content').evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('skins-narrow-dark.png'), animations: 'disabled' });
  await dialog.getByRole('radio', { name: 'Terrain', exact: true }).click();
  await expect(page.locator('.terrain-webgl-background')).toHaveCount(1);
});

const blueprintMod = {
  schemaVersion: 1, id: 'acme.blueprint', name: 'Blueprint',
  palettes: [{ id: 'drafting', name: 'Drafting', light: { '--accent': '#1766a3' }, dark: { '--accent': '#80bfff' } }],
  backgrounds: [{ id: 'squared', name: 'Squared paper', kind: 'grid', spacing: 32 }],
};

test('imports, persists and exports a portable skin mod, with fallback on removal', async ({ page, request }) => {
  const dialog = page.getByRole('dialog', { name: 'Settings', exact: true });
  await dialog.locator('input[type="file"][aria-label="Import skin mod"]').setInputFiles({
    name: 'blueprint.oawskin.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(blueprintMod)),
  });
  await dialog.getByRole('radio', { name: 'Drafting', exact: true }).click();
  await dialog.getByRole('radio', { name: 'Squared paper', exact: true }).click();
  await expect(page.locator('html')).toHaveAttribute('data-palette', 'acme.blueprint/drafting');
  await expect.poll(() => page.locator('html').evaluate(element => element.style.getPropertyValue('--accent'))).toBe('#1766a3');
  await expect.poll(async () => {
    const profile = await (await request.get('/api/application')).json();
    const saved = JSON.parse(profile.values['oaw-appearance']);
    return { paletteId: saved.paletteId, backgroundId: saved.backgroundId, mods: saved.skinMods?.map((mod: { id: string }) => mod.id) ?? [] };
  }).toEqual({ paletteId: 'acme.blueprint/drafting', backgroundId: 'acme.blueprint/squared', mods: ['acme.blueprint'] });

  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-palette', 'acme.blueprint/drafting');
  await expect(page.locator('html')).toHaveAttribute('data-background', 'acme.blueprint/squared');
  await page.getByRole('button', { name: 'Open settings', exact: true }).click();
  await page.getByRole('button', { name: 'Appearance', exact: true }).click();
  await expect(dialog.getByRole('radio', { name: 'Drafting', exact: true })).toBeChecked();
  await expect(dialog.getByRole('radio', { name: 'Squared paper', exact: true })).toBeChecked();
  const downloadEvent = page.waitForEvent('download');
  await dialog.getByRole('button', { name: 'Export skin mod', exact: true }).click();
  const download = await downloadEvent;
  const stream = await download.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream!) chunks.push(Buffer.from(chunk));
  const exportedBytes = Buffer.concat(chunks);
  const exported = JSON.parse(exportedBytes.toString('utf8'));
  expect(exported).toMatchObject({ schemaVersion: 1,
    palettes: [{ id: 'palette', light: { '--accent': '#1766a3' }, dark: { '--accent': '#80bfff' } }],
    backgrounds: [{ id: 'background', kind: 'grid', spacing: 32 }],
  });
  expect(exported.id).not.toBe(blueprintMod.id);
  await dialog.getByRole('button', { name: 'Remove Blueprint', exact: true }).click();
  await expect(dialog.getByRole('radio', { name: 'Drafting', exact: true })).toHaveCount(0);
  await expect(page.locator('html')).toHaveAttribute('data-palette', 'oaw/paper');
  await expect(page.locator('html')).toHaveAttribute('data-background', 'oaw/terrain');
  await expect.poll(() => page.locator('html').evaluate(element => element.style.getPropertyValue('--accent'))).toBe('');
  await dialog.locator('input[type="file"][aria-label="Import skin mod"]').setInputFiles({
    name: download.suggestedFilename(), mimeType: 'application/json', buffer: exportedBytes,
  });
  await dialog.getByRole('radio', { name: 'Drafting', exact: true }).click();
  await dialog.getByRole('radio', { name: 'Squared paper', exact: true }).click();
  await expect(page.locator('html')).toHaveAttribute('data-palette', `${exported.id}/palette`);
  await expect(page.locator('html')).toHaveAttribute('data-background', `${exported.id}/background`);
});

test('rejects CSS URLs in imported data without changing the selected skin', async ({ page }) => {
  const dialog = page.getByRole('dialog', { name: 'Settings', exact: true });
  await dialog.getByRole('radio', { name: 'Ocean', exact: true }).click();
  await dialog.locator('input[type="file"][aria-label="Import skin mod"]').setInputFiles({
    name: 'invalid.oawskin.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify({
      ...blueprintMod,
      palettes: [{ ...blueprintMod.palettes[0], light: { '--accent': 'url(https://example.invalid/tracking.svg)' } }],
    })),
  });
  await expect(dialog.getByRole('alert')).toBeVisible();
  await expect(dialog.getByRole('radio', { name: 'Drafting', exact: true })).toHaveCount(0);
  await expect(page.locator('html')).toHaveAttribute('data-palette', 'oaw/ocean');
});
