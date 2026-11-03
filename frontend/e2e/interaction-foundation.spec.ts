import { expect, test } from '@playwright/test';

const fixture = '/e2e/fixtures/interaction-foundation.html';
test.describe('touch foundation', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test('sizes real controls for touch and keeps corner tools separate across rotation', async ({ page }) => {
    await page.goto(fixture);
    const root = page.locator('html');
    await expect(root).toHaveAttribute('data-layout', 'compact');
    await expect(root).toHaveAttribute('data-touch-input', 'true');
    const add = page.getByRole('button', { name: 'Add', exact: true });
    await expect(add).toHaveCSS('min-width', '44px');
    await expect(page.getByRole('textbox', { name: 'Draft' })).toHaveCSS('font-size', '16px');
    for (const size of [{ width: 320, height: 568 }, { width: 844, height: 390 }]) {
      await page.setViewportSize(size);
      const controls = (await page.locator('.world-controls').boundingBox())!;
      const tools = (await page.locator('.map-toolbar').boundingBox())!;
      expect(tools.y).toBeGreaterThanOrEqual(controls.y + controls.height);
      expect(tools.x + tools.width).toBeLessThanOrEqual(size.width);
    }
    await expect(root).toHaveAttribute('data-layout', 'regular');
    await expect(add).toHaveCSS('min-height', '44px');
  });

  test('hands a native touch pointer from hold to drag without a second tap', async ({ page }) => {
    await page.addInitScript(() => {
      (window as unknown as { pulses: number[] }).pulses = [];
      Object.defineProperty(navigator, 'vibrate', { value: (duration: number) => {
        (window as unknown as { pulses: number[] }).pulses.push(duration); return true;
      } });
    });
    await page.goto(fixture);
    const card = page.getByRole('button', { name: 'Hold card' });
    const box = (await card.boundingBox())!;
    const session = await page.context().newCDPSession(page);
    const x = box.x + box.width / 2, y = box.y + box.height / 2;
    await session.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
    await expect(card).toHaveAttribute('data-touch-hold', 'active');
    await session.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: x + 60, y: y + 20 }] });
    await session.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await expect(card).toHaveAttribute('data-touch-hold', 'idle');
    const names = await page.evaluate(() => (window as unknown as { interactions: { name: string }[] }).interactions.map(item => item.name));
    expect(names[0]).toBe('hold');
    expect(names).toContain('move');
    expect(names.at(-1)).toBe('release');
    expect(names).not.toContain('click');
    await page.getByRole('button', { name: 'Add', exact: true }).tap();
    await expect(page.locator('#clicks')).toHaveText('1');
    expect(await page.evaluate(() => (window as unknown as { pulses: number[] }).pulses)).toContain(8);
  });
});

test('a narrow mouse window retains desktop control sizes and native text selection', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(fixture);
  await expect(page.locator('html')).toHaveAttribute('data-touch-input', 'false');
  await expect(page.getByRole('button', { name: 'Add', exact: true })).toHaveCSS('width', '32px');
  await expect(page.getByRole('textbox', { name: 'Draft' })).not.toHaveCSS('user-select', 'none');
});
