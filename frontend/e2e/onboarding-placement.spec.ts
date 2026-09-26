import { resetTutorialProfile } from './tutorial-profile';
import { prepareTutorialDeck } from './tutorial-deck';
import { expect, test } from '@playwright/test';

test('placement stays hidden until its single flight starts', async ({ page, request }) => {
  await resetTutorialProfile(request);
  const initialIds: string[] = (await (await request.get('/api/nodes')).json()).map((card: { id: string }) => card.id);
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.goto('/');
  if (initialIds.length) {
    await page.getByRole('button', { name: 'Help', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Tutorial', exact: true }).click();
  } else await page.getByRole('button', { name: /^Start Tutorial/ }).click();
  await page.locator('.tutorial-next').click();
  await page.mouse.move(160, 140); await page.mouse.down();
  await page.mouse.move(280, 190, { steps: 10 }); await page.mouse.up();
  await expect(page.locator('.tutorial-bubble')).toHaveAttribute('data-step', 'zoom');
  await page.mouse.move(170, 200); await page.mouse.wheel(0, -240);
  await expect(page.locator('.tutorial-bubble')).toHaveAttribute('data-step', 'deck');
  await prepareTutorialDeck(page, true);
  await expect(page.locator('.tutorial-bubble')).toHaveAttribute('data-step', 'place-demo');
  await page.evaluate(initialIds => {
    const result = { flights: 0, hidden: 0, premature: 0, flying: 0, anchors: [] as string[], positions: [] as number[] };
    Object.assign(window, { placementResult: result });
    const original = Element.prototype.animate;
    Element.prototype.animate = function (...args: Parameters<Element['animate']>) {
      const animation = original.apply(this, args);
      if (this.matches('.tutorial-placement-flight')) result.flights++;
      return animation;
    };
    const sample = () => {
      const node = [...document.querySelectorAll<HTMLElement>('.world-canvas .react-flow__node')].find(node => !initialIds.includes(node.dataset.id!));
      if (node) {
        const visible = getComputedStyle(node).visibility === 'visible';
        if (!result.flights) visible ? result.premature++ : result.hidden++;
        else if (document.querySelector('.tutorial-placement-flight')) {
          result.flying++;
          const guide = document.querySelector<HTMLElement>('.tutorial-guide')!;
          result.anchors.push(guide.style.getPropertyValue('--guide-x') + ',' + guide.style.getPropertyValue('--guide-y'));
          result.positions.push(node.getBoundingClientRect().x);
        }
      }
      if (document.querySelector('.tutorial-bubble')?.getAttribute('data-step') === 'place-demo') requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  }, initialIds);
  await page.getByRole('button', { name: 'Show me', exact: true }).click();
  await page.waitForSelector('.tutorial-placement-flight');
  await page.screenshot({ path: 'test-results/tutorial-placement-flight.png' });
  await expect(page.locator('.tutorial-bubble')).toHaveAttribute('data-reviewing', 'true');
  await expect(page.locator('.tutorial-next')).toBeEnabled();
  await expect(page.locator('.tutorial-bubble')).toHaveAttribute('data-step', 'place-demo');
  await page.locator('.tutorial-next').click();
  await expect(page.locator('.tutorial-bubble')).toHaveAttribute('data-step', 'place');
  await expect(page.locator('.tutorial-placement-arrow')).toHaveAttribute('data-card', 'text');
  await expect(page.locator('.tutorial-placement-arrow')).toBeVisible();
  await expect(page.locator('.tutorial-spotlight-mask > rect')).toHaveAttribute('opacity', '0');
  await expect(page.locator('.deck-stage')).toHaveCSS('opacity', '1');
  await expect(page.locator('[data-palette-card="text"]')).toBeInViewport();
  await page.screenshot({ path: 'test-results/tutorial-placement-arrow.png' });
  await page.locator('[data-tutorial="settings"]').click();
  await expect(page.getByRole('dialog', { name: 'Settings', exact: true })).toHaveCount(0);
  await page.locator('[data-palette-card="agent"]').click();
  await expect(page.locator('.tutorial-bubble')).toHaveAttribute('data-step', 'place');
  await page.keyboard.press('Control+v');
  await page.getByRole('button', { name: 'Pause tutorial', exact: true }).click();
  await page.locator('[data-tutorial="settings"]').click();
  await expect(page.getByRole('dialog', { name: 'Settings', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Close settings', exact: true }).click();
  await page.getByRole('button', { name: 'Resume', exact: true }).click();
  await expect(page.locator('.tutorial-bubble')).toHaveAttribute('data-step', 'place');
  const result = await page.evaluate(() => (window as unknown as { placementResult: { flights: number; hidden: number; premature: number; flying: number; anchors: string[]; positions: number[] } }).placementResult);
  expect(result.flights).toBe(1);
  expect(result.hidden).toBeGreaterThan(0);
  expect(result.premature).toBe(0);
  expect(result.flying).toBeGreaterThan(0);
  expect(new Set(result.anchors).size).toBeLessThanOrEqual(2);
  expect(Math.max(...result.positions) - Math.min(...result.positions)).toBeLessThan(1);
  const added = (await (await request.get('/api/nodes')).json()).filter((card: { id: string }) => !initialIds.includes(card.id));
  expect(added).toHaveLength(1);
  await expect(page.locator(`.world-canvas .react-flow__node[data-id="${added[0].id}"]`)).toBeVisible();
  await request.delete(`/api/nodes/${added[0].id}`);
});
