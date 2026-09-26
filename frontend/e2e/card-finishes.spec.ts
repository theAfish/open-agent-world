import { expect, test } from '@playwright/test';

test('cards tilt beneath a fixed light and retain readable ink in both themes', async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/?card-finishes');
  const cards = page.locator('[data-preview-finish]');
  await expect(cards).toHaveCount(5);
  await expect(page.locator('[data-preview-finish="normal"] .card-finish-layer')).toHaveCount(0);
  const rainbow = page.locator('[data-preview-finish="rainbow"]');
  const box = (await rainbow.boundingBox())!;
  await page.mouse.move(box.x + box.width * .76, box.y + box.height * .29);
  await expect.poll(() => rainbow.evaluate(element => element.style.getPropertyValue('--pointer-x'))).not.toBe('');
  await expect(rainbow).toHaveAttribute('data-card-tilting', 'true');
  const firstTransform = await rainbow.evaluate(element => getComputedStyle(element).transform);
  await page.mouse.move(box.x + box.width * .24, box.y + box.height * .71);
  await expect.poll(() => rainbow.evaluate(element => getComputedStyle(element).transform)).not.toBe(firstTransform);
  expect(await rainbow.evaluate(element => element.style.getPropertyValue('--finish-light-x'))).toBe('32%');
  expect(await rainbow.evaluate(element => element.style.getPropertyValue('--finish-light-y'))).toBe('24%');
  await page.screenshot({ path: testInfo.outputPath('finishes-light.png'), fullPage: true });
  await page.getByLabel('Dark card stock').check();
  await page.screenshot({ path: testInfo.outputPath('finishes-dark.png'), fullPage: true });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect(rainbow).not.toHaveAttribute('data-card-tilting');
  await expect(rainbow).toHaveCSS('transform', 'none');
  await page.getByRole('button', { name: 'Replay light' }).click();
  await expect.poll(() => page.evaluate(() => document.getAnimations().filter(animation => animation.playState === 'running').length)).toBe(0);
  for (const card of await cards.all()) await expect(card.getByText('Research Agent')).toBeVisible();
  expect(errors).toEqual([]);
});

test('ordinary and thumbnail cards tilt, then settle on leave', async ({ page }) => {
  await page.goto('/?card-finishes');
  const normal = page.locator('[data-preview-finish="normal"]').first();
  await normal.hover({ position: { x: 25, y: 30 } });
  await expect(normal).toHaveAttribute('data-card-tilting', 'true');
  await page.mouse.move(1, 1);
  await expect(normal).not.toHaveAttribute('data-card-tilting');
  await page.getByLabel('200 thumbnails').check();
  await normal.hover({ position: { x: 20, y: 25 } });
  await expect(normal).toHaveAttribute('data-card-tilting', 'true');
  await page.mouse.move(1, 1);
  await expect(normal).not.toHaveAttribute('data-card-tilting');
});

test('200 passive cards settle without animation callbacks or running animations', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1800, height: 2800 });
  await page.addInitScript(() => {
    const request = window.requestAnimationFrame.bind(window);
    const metrics = { callbacks: 0 };
    Object.assign(window, { finishMetrics: metrics });
    window.requestAnimationFrame = callback => request(time => { metrics.callbacks++; callback(time); });
  });
  await page.goto('/?card-finishes');
  await page.getByLabel('200 thumbnails').check();
  await expect(page.locator('[data-preview-finish]')).toHaveCount(200);
  await page.mouse.move(1, 1);
  const measurement = await page.evaluate(async () => {
    const metrics = (window as unknown as { finishMetrics: { callbacks: number } }).finishMetrics;
    const before = metrics.callbacks;
    await new Promise(resolve => setTimeout(resolve, 350));
    return { cards: document.querySelectorAll('[data-preview-finish]').length, idleCallbacks: metrics.callbacks - before,
      runningAnimations: document.getAnimations().filter(animation => animation.playState === 'running').length };
  });
  expect(measurement.idleCallbacks).toBe(0);
  expect(measurement.runningAnimations).toBe(0);
  await testInfo.attach('passive-material-performance', { body: JSON.stringify(measurement), contentType: 'application/json' });
  await page.screenshot({ path: testInfo.outputPath('finishes-200.png'), fullPage: true });
});
