import { expect, test } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    if (!localStorage.getItem('oaw.locale')) localStorage.setItem('oaw.locale', 'en');
    localStorage.setItem('oaw-onboarding-v1', JSON.stringify({ state: { status: 'skipped' }, version: 1 }));
  });
  await page.emulateMedia({ reducedMotion: 'reduce' });
});

test('six compact controls keep their order and switch the open settings without losing a draft', async ({ page }) => {
  await page.goto('/');
  const actions = page.locator('.top-actions > button');
  await expect(actions).toHaveCount(6);
  await expect(actions.first()).toHaveAccessibleName('Open Pack and Card Library');
  await expect(actions.last()).toHaveAccessibleName('Help');
  for (const width of [1280, 800, 390]) {
    await page.setViewportSize({ width, height: 800 });
    const boxes = await actions.evaluateAll(buttons => buttons.map(button => {
      const box = button.getBoundingClientRect();
      return { x: box.x, y: box.y, width: box.width, right: box.right };
    }));
    expect(new Set(boxes.map(box => Math.round(box.y))).size).toBe(1);
    expect(boxes.every(box => box.width >= 28 && box.width <= 36 && box.x >= 0 && box.right <= width)).toBe(true);
  }
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.getByRole('button', { name: '切换到中文', exact: true }).click();
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN');
  await expect(actions.first()).toHaveAccessibleName('打开卡包与卡片图书馆');
  await expect(actions.last()).toHaveAccessibleName('帮助');
  await page.screenshot({ path: 'test-results/i18n-hud-zh-light.png' });
  await page.getByRole('button', { name: '使用深色主题', exact: true }).click();
  await page.screenshot({ path: 'test-results/i18n-hud-zh-dark.png' });
  await page.getByRole('button', { name: '打开设置', exact: true }).click();
  await page.getByRole('button', { name: '添加连接', exact: true }).click();
  const draft = page.getByRole('textbox', { name: '连接名称', exact: true });
  await draft.fill('Keep my 草稿');
  await page.getByRole('combobox', { name: '界面语言', exact: true }).selectOption('en');
  await expect(page.getByRole('textbox', { name: 'Connection name', exact: true })).toHaveValue('Keep my 草稿');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await page.getByRole('button', { name: 'Open Pack and Card Library', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Pack & Card Library', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Close Library', exact: true }).click();
});

test('WebGL terrain remains visible at both zoom limits in both themes', async ({ page, request }) => {
  test.setTimeout(60_000);
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw.locale': 'en', 'oaw-theme': 'light',
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
      'oaw-canvas-viewport-v1': null,
    },
  } });
  await page.goto('/');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  for (const theme of ['light', 'dark']) {
    if (await page.locator('html').getAttribute('data-theme') !== theme) {
      await page.getByRole('button', { name: `Use ${theme} theme`, exact: true }).click();
    }
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    for (const zoom of [0.12, 2.2]) {
      const control = page.getByRole('button', { name: zoom === .12 ? 'Zoom out' : 'Zoom in', exact: true });
      for (let i = 0; i < 30 && await control.isEnabled(); i++) await control.click();
      await expect(control).toBeDisabled();
      const background = page.locator('.terrain-webgl-background');
      await expect(background).toHaveAttribute('data-terrain-status', 'ready');
      await expect.poll(() => page.locator('#oaw-world-map .react-flow__viewport').evaluate(el =>
        new DOMMatrix(getComputedStyle(el).transform).a)).toBeCloseTo(zoom, 2);
      await expect.poll(() => background.evaluate(el => {
        const s = (el as any).terrainStats;
        return s.visibleTiles > 0 && s.coveredTiles === s.visibleTiles && s.pendingTiles === 0;
      })).toBe(true);
      await page.screenshot({ path: `test-results/terrain-${theme}-${zoom}.png` });
    }
  }
});
