import { expect, test } from '@playwright/test';

for (const width of [1440, 390]) test(`pack rotation keeps its scale and pivot at ${width}px`, async ({ page }, testInfo) => {
  test.setTimeout(60000);
  await page.setViewportSize({ width, height: 1350 });
  await page.goto('/?pack-design');
  const studies = page.locator('.pack-design-study');
  await expect(studies.first().locator('.library-pack')).toHaveAttribute('data-renderer', 'webgl');
  if (width === 390) await studies.locator('.pack-touch-area').evaluateAll(buttons => {
    // A narrow catalog cell must let the package extend across its neighbours.
    buttons.forEach(button => { Object.assign((button as HTMLElement).style, { width: '110px', marginInline: 'auto', display: 'block' }); });
  });
  // Observe real camera matrices at the GPU boundary without replacing the renderer.
  await page.evaluate(async () => {
    const source = performance.getEntriesByType('resource').find(entry => /\/three\.js(?:\?|$)/.test(entry.name))!.name;
    const { Box3, Object3D, Vector3 } = await import(/* @vite-ignore */ source);
    const onBeforeRender = Object3D.prototype.onBeforeRender;
    const projections = new Map<string, string>();
    const probe = { frames: 0, changes: [] as string[], clipped: [] as string[] };
    (window as unknown as { packRotationProbe: typeof probe }).packRotationProbe = probe;
    Object3D.prototype.onBeforeRender = function (this: import('three').Object3D, ...args: Parameters<import('three').Object3D['onBeforeRender']>) {
      const [, scene, camera] = args;
      const pack = scene.children.find(object => object.name.startsWith('pack:'));
      if (pack && this === pack.children[0]?.children[0] && (camera as import('three').OrthographicCamera).near === .1) {
        const projection = JSON.stringify([camera.projectionMatrix.elements, camera.matrixWorld.elements]);
        const previous = projections.get(pack.uuid);
        if (previous && previous !== projection && !probe.changes.includes(pack.name)) probe.changes.push(pack.name);
        projections.set(pack.uuid, projection); probe.frames++;
        const bounds = new Box3().setFromObject(pack);
        for (const x of [bounds.min.x, bounds.max.x]) for (const y of [bounds.min.y, bounds.max.y]) for (const z of [bounds.min.z, bounds.max.z]) {
          const point = new Vector3(x, y, z).project(camera);
          if ((Math.abs(point.x) > 1 || Math.abs(point.y) > 1) && !probe.clipped.includes(pack.name)) probe.clipped.push(pack.name);
        }
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
    if (width === 390) {
      const overflow = await canvas.evaluate((canvas: HTMLCanvasElement) => {
        const frame = canvas.parentElement!.getBoundingClientRect(), area = canvas.getBoundingClientRect();
        const pixels = canvas.getContext('2d')!.getImageData(0, 0, canvas.width, canvas.height).data;
        const left = (frame.left - area.left) / area.width * canvas.width;
        const right = (frame.right - area.left) / area.width * canvas.width;
        for (let index = 3; index < pixels.length; index += 4) {
          const x = Math.floor(index / 4) % canvas.width;
          if (pixels[index] !== 255 || (x >= left && x <= right)) continue;
          const point = { x: Math.floor(area.x + (x + .5) / canvas.width * area.width),
            y: Math.floor(area.y + (Math.floor(index / 4 / canvas.width) + .5) / canvas.height * area.height) };
          if (point.x >= 0 && point.x < innerWidth && point.y >= 0 && point.y < innerHeight) return point;
        }
        return null;
      });
      expect(overflow).not.toBeNull();
      const visible = await page.screenshot({ clip: { ...overflow!, width: 1, height: 1 } });
      await canvas.evaluate(element => { element.style.visibility = 'hidden'; });
      const hidden = await page.screenshot({ clip: { ...overflow!, width: 1, height: 1 } });
      await canvas.evaluate(element => { element.style.removeProperty('visibility'); });
      expect(visible.equals(hidden)).toBe(false);
      await expect(study.locator('.library-pack')).toHaveCSS('z-index', '2');
    }
    await study.screenshot({ path: testInfo.outputPath(`rotation-${index}.png`) });
  }
  const probe = await page.evaluate(() => (window as unknown as { packRotationProbe: { frames: number; changes: string[]; clipped: string[] } }).packRotationProbe);
  expect(probe.frames).toBeGreaterThan(100);
  expect(probe.changes).toEqual([]);
  expect(probe.clipped).toEqual([]);
});
