import { expect, test } from '@playwright/test';

for (const width of [1440, 390]) test(`pack rotation keeps its scale and pivot at ${width}px`, async ({ page }, testInfo) => {
  test.setTimeout(60000);
  await page.setViewportSize({ width, height: 1350 });
  await page.goto('/?pack-design');
  const studies = page.locator('.pack-design-study');
  await expect(studies.first().locator('.library-pack')).toHaveAttribute('data-renderer', 'webgl');
  // Observe real camera matrices at the GPU boundary without replacing the renderer.
  await page.evaluate(async () => {
    const source = performance.getEntriesByType('resource').find(entry => /\/three\.js(?:\?|$)/.test(entry.name))!.name;
    const { Object3D } = await import(/* @vite-ignore */ source);
    const onBeforeRender = Object3D.prototype.onBeforeRender;
    const projections = new Map<string, string>();
    const probe = { frames: 0, changes: [] as string[] };
    (window as unknown as { packRotationProbe: typeof probe }).packRotationProbe = probe;
    Object3D.prototype.onBeforeRender = function (this: import('three').Object3D, ...args: Parameters<import('three').Object3D['onBeforeRender']>) {
      const [, scene, camera] = args;
      const pack = scene.children.find(object => object.name.startsWith('pack:'));
      if (pack && (camera as import('three').OrthographicCamera).near === .1) {
        const projection = JSON.stringify([camera.projectionMatrix.elements, camera.matrixWorld.elements]);
        const previous = projections.get(pack.uuid);
        if (previous && previous !== projection && !probe.changes.includes(pack.name)) probe.changes.push(pack.name);
        projections.set(pack.uuid, projection); probe.frames++;
      }
      return onBeforeRender.apply(this, args);
    };
  });
  for (let index = 0; index < 4; index++) {
    const study = studies.nth(index), button = study.locator('.pack-touch-area'), canvas = study.locator('.pack-webgl');
    await button.scrollIntoViewIfNeeded();
    await expect(study.locator('.library-pack')).toHaveAttribute('data-renderer', 'webgl');
    await button.focus();
    await button.press('Home');
    await expect(canvas).toHaveAttribute('data-yaw', '-18.0');
    for (let turn = 1; turn <= 24; turn++) {
      await button.press('ArrowRight');
      await expect(canvas).toHaveAttribute('data-yaw', `${(-18 + turn * 15).toFixed(1)}`);
    }
    for (const [key, pitch] of [['ArrowDown', '75.0'], ['ArrowUp', '-75.0']] as const) {
      for (let tilt = 0; tilt < 15; tilt++) await button.press(key);
      await expect(canvas).toHaveAttribute('data-pitch', pitch);
    }
    await button.press('Home');
    const box = (await button.boundingBox())!;
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 70, box.y + box.height / 2 + 30, { steps: 8 });
    await page.mouse.up();
    await expect(canvas).toHaveAttribute('data-yaw', '20.5');
    await expect(study.locator('.library-pack')).not.toHaveClass(/is-opened/);
    await study.screenshot({ path: testInfo.outputPath(`rotation-${index}.png`) });
  }
  const probe = await page.evaluate(() => (window as unknown as { packRotationProbe: { frames: number; changes: string[] } }).packRotationProbe);
  expect(probe.frames).toBeGreaterThan(100);
  expect(probe.changes).toEqual([]);
});
