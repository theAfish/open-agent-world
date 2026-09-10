import { expect, test } from '@playwright/test';

test('local map stays centered at 130% of the viewport during pan and zoom', async ({ page, request }) => {
  const created: string[] = [];
  try {
    for (const x of [100, 1800]) {
      const response = await request.post('/api/nodes', { data: { type: 'text', name: 'Local map check', position: { x, y: 100 } } });
      expect(response.ok()).toBe(true);
      created.push((await response.json()).id);
    }
    await page.goto('/');
    const map = page.locator('.world-minimap');
    await expect(map).toBeVisible();
    await expect(map.locator('rect:not(.local-minimap-viewport)')).toHaveCount(2);
    const geometry = () => page.evaluate(() => {
      const world = document.querySelector('#oaw-world-map')!;
      const viewport = world.querySelector('.react-flow__viewport')!;
      const map = world.querySelector('.world-minimap')!;
      const svg = map.querySelector('svg')!;
      const transform = new DOMMatrix(getComputedStyle(viewport).transform);
      const mask = map.querySelector('.local-minimap-viewport')!;
      const numbers = ['x', 'y', 'width', 'height'].map(key => Number(mask.getAttribute(key)));
      return {
        frame: [map.clientWidth, map.clientHeight],
        svg: [svg.width.baseVal.value, svg.height.baseVal.value],
        actual: numbers.slice(0, 4),
        bounds: [svg.viewBox.baseVal.x, svg.viewBox.baseVal.y, svg.viewBox.baseVal.width, svg.viewBox.baseVal.height],
        expected: [-transform.e / transform.a, -transform.f / transform.a, world.clientWidth / transform.a, world.clientHeight / transform.a],
        stroke: getComputedStyle(mask).stroke,
        background: getComputedStyle(map).backgroundColor,
        canvas: getComputedStyle(document.documentElement).getPropertyValue('--canvas').trim(),
      };
    });
    const check = async () => {
      await expect.poll(async () => {
        const state = await geometry();
        return state.actual.every((value, index) => Math.abs(value - state.expected[index]) < 0.1);
      }).toBe(true);
      const state = await geometry();
      state.svg.forEach((value, index) => expect(Math.abs(value - state.frame[index])).toBeLessThanOrEqual(0.5));
      expect(state.bounds[2]).toBeCloseTo(state.actual[2] * 1.3, 2);
      expect(state.bounds[3]).toBeCloseTo(state.actual[3] * 1.3, 2);
      expect(state.bounds[0]).toBeCloseTo(state.actual[0] - state.actual[2] * 0.15, 2);
      expect(state.bounds[1]).toBeCloseTo(state.actual[1] - state.actual[3] * 0.15, 2);
      expect(state.stroke).not.toBe('none');
    };
    await check();
    const before = await geometry();
    await page.mouse.move(950, 180);
    await page.mouse.down();
    await page.mouse.move(1040, 230, { steps: 8 });
    await page.mouse.up();
    await check();
    expect((await geometry()).actual).not.toEqual(before.actual);
    await page.locator('.world-controls').getByRole('button', { name: /zoom in/i }).click();
    await expect.poll(async () => (await geometry()).actual[2]).toBeLessThan(before.actual[2]);
    await check();
    const box = (await map.boundingBox())!;
    const beforeMapPan = await geometry();
    await page.mouse.move(box.x + 65, box.y + 42);
    await page.mouse.down();
    await page.mouse.move(box.x + 85, box.y + 48, { steps: 8 });
    await page.mouse.up();
    await check();
    expect((await geometry()).actual).not.toEqual(beforeMapPan.actual);
    const beforeWheel = await geometry();
    await page.mouse.wheel(0, -400);
    await page.waitForTimeout(250);
    expect((await geometry()).actual).toEqual(beforeWheel.actual);
    await page.setViewportSize({ width: 1100, height: 900 });
    await check();
    for (const theme of ['light', 'dark']) {
      await page.evaluate(theme => document.documentElement.dataset.theme = theme, theme);
      await check();
      expect((await geometry()).background).not.toBe('rgb(255, 255, 255)');
    }
  } finally {
    for (const id of created) await request.delete(`/api/nodes/${id}`);
  }
});
