import { expect, test } from '@playwright/test';

test('factory laminate protects glyphs rather than unprinted boxes and keeps control styles outside editor chrome', async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.goto('/?card-studio');
  await page.getByRole('navigation', { name: '设计步骤' }).getByRole('button', { name: '选择油墨第 1 层', exact: true }).click();
  await page.getByRole('button', { name: '预设', exact: true }).click();
  await page.getByRole('button', { name: '应用 Utility 版式', exact: true }).click();
  await page.getByRole('navigation', { name: '设计步骤' }).getByRole('button', { name: '选择油墨第 1 层', exact: true }).click();
  await page.locator('.process-add-menu > summary').click();
  await page.getByRole('button', { name: '添加覆膜', exact: true }).click();
  await page.getByLabel('覆膜类型', {exact:true}).selectOption('holo');
  await page.getByRole('navigation', { name: '设计步骤' }).getByRole('button', { name: '成品', exact: true }).click();
  const card = page.locator('.face-stage-size .factory-artwork');
  await expect(card.locator('[data-material-ready]')).toHaveCount(1);
  const proof = await card.evaluate(async host => {
    // Vite's development module uses the same production mask builder as the card.
    // @ts-expect-error Runtime import belongs to the browser development server.
    const { createMaterialMask } = await import('/src/cards/cardMaterialMask.ts');
    const artwork = host as HTMLElement, layer = artwork.querySelector<HTMLElement>('.card-finish-layer')!;
    const mask = createMaterialMask(artwork, layer, layer.offsetWidth, layer.offsetHeight);
    const pixels = mask.protection.getContext('2d')!.getImageData(0, 0, mask.protection.width, mask.protection.height).data;
    const origin = layer.getBoundingClientRect(), sx = layer.offsetWidth / origin.width, sy = layer.offsetHeight / origin.height;
    const coverage = (selector: string) => {
      const box = artwork.querySelector(selector)!.getBoundingClientRect();
      const left = Math.max(0, Math.ceil((box.left - origin.left) * sx)), top = Math.max(0, Math.ceil((box.top - origin.top) * sy));
      const right = Math.min(mask.protection.width, Math.floor((box.right - origin.left) * sx)), bottom = Math.min(mask.protection.height, Math.floor((box.bottom - origin.top) * sy));
      let white = 0, count = 0;
      for (let y = top; y < bottom; y++) for (let x = left; x < right; x++) { white += pixels[(y * mask.protection.width + x) * 4] / 255; count++; }
      return white / count;
    };
    const properties = ['fontSize', 'color', 'padding', 'borderWidth', 'borderRadius', 'minHeight', 'backgroundColor', 'boxShadow', 'gap'] as const;
    const controls = ['.factory-face-action', '.factory-face-fields input', '.factory-face-fields label'];
    const styles = (root: HTMLElement) => controls.map(selector => {
      const element = root.querySelector(selector);
      if (!element) return null;
      const style = getComputedStyle(element);
      return Object.fromEntries(properties.map(property => [property, style[property]]));
    });
    const editor = styles(artwork), live = document.createElement('div');
    live.className = 'factory-designed-card'; live.style.cssText = `position:absolute;left:-10000px;top:0;width:${artwork.offsetWidth}px;height:${artwork.offsetHeight}px`;
    live.append(artwork.cloneNode(true)); document.body.append(live);
    const outsideEditor = styles(live); live.remove();
    const icon = coverage('[data-kind=icon]'), title = coverage('[data-kind=title]');
    const originalTitle = artwork.querySelector<HTMLElement>('[data-kind=title]')!;
    const overlay = document.createElement('div');
    overlay.dataset.faceElement = 'test-cover'; overlay.dataset.kind = 'illustration'; overlay.className = 'factory-art-element'; overlay.style.cssText = originalTitle.style.cssText;
    overlay.innerHTML = '<span class="factory-illustration-placeholder"></span>';
    originalTitle.parentElement!.append(overlay);
    const covered = createMaterialMask(artwork, layer, layer.offsetWidth, layer.offsetHeight).protection.getContext('2d')!;
    const coveredPixels = covered.getImageData(0, 0, mask.protection.width, mask.protection.height).data;
    let removedInk = 0;
    for (let i = 0; i < pixels.length; i += 4) if (pixels[i] > coveredPixels[i]) removedInk++;
    overlay.remove();
    return { icon, title, editor, outsideEditor, removedInk };
  });
  expect(proof.icon).toBeGreaterThan(.005);
  expect(proof.icon).toBeLessThan(.5);
  expect(proof.title).toBeGreaterThan(.025);
  expect(proof.title).toBeLessThan(.7);
  expect(proof.editor).toEqual(proof.outsideEditor);
  expect(proof.removedInk).toBeGreaterThan(30);
  expect(errors).toEqual([]);
  await page.screenshot({ path: info.outputPath('continuous-factory-laminate.png'), fullPage: true });
});
