import { expect, test, type Page } from '@playwright/test';

async function mountProcessCard(page: Page, fallback: boolean) {
  if (fallback) await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', { value: function(type: string, ...args: unknown[]) {
      return type.includes('webgl') ? null : Reflect.apply(original, this, [type, ...args]);
    } });
  });
  await page.goto('/?card-materials');
  await page.evaluate(async () => {
    const load = (path: string) => import(/* @vite-ignore */ path);
    const resource = (suffix: string) => performance.getEntriesByType('resource').find(entry => entry.name.includes(suffix))!.name;
    const { default: React } = await load(resource('/deps/react.js'));
    const { default: ReactDOM } = await load(resource('/deps/react-dom_client.js'));
    const { FaceArtwork } = await load('/src/factory/FaceArtwork.tsx');
    const { faceStudio } = await load('/src/factory/faceDesign.ts');
    const { newProductionLayer } = await load('/src/cards/cardProduction.ts');
    const face = { title: 'GOLD / 01', description: 'Printed card', variant: 'icon', tone: 'sand', color: '#617b66', icon: 'sparkles', finish: 'normal', layout: 'stack', help_text: '', button_label: 'Start' };
    const surface = faceStudio(face).modes.preview;
    const foil = { ...newProductionLayer('foil'), id: 'gold', strength: 1, color: '#dfaa32', mask: { ...newProductionLayer('foil').mask, source: 'text' } };
    const film = { ...newProductionLayer('laminate'), id: 'film', strength: 1, film: 'holo', mask: { ...newProductionLayer('laminate').mask, source: 'all' } };
    surface.design.production.layers = [foil, film];
    const mount = document.createElement('div'); mount.id = 'process-render-proof';
    mount.style.cssText = `position:fixed;left:40px;top:40px;width:${surface.width}px;height:${surface.height}px;z-index:10000;`;
    document.body.append(mount);
    const root = ReactDOM.createRoot(mount);
    const render = () => root.render(React.createElement(FaceArtwork, { face, surface, interactive: false }));
    Object.assign(window, { processRenderProof: { surface, render, foil, film } }); render();
  });
  await expect(page.locator('#process-render-proof [data-material-settled]')).toHaveCount(2);
  await page.evaluate(() => document.fonts.ready);
}

async function composite(page: Page) {
  return page.locator('#process-render-proof').evaluate(host => {
    const canvases = [...host.querySelectorAll('canvas')];
    const output = document.createElement('canvas'); output.width = canvases[0].width; output.height = canvases[0].height;
    const context = output.getContext('2d')!; context.fillStyle = '#eee8db'; context.fillRect(0, 0, output.width, output.height);
    canvases.forEach(canvas => context.drawImage(canvas, 0, 0));
    return output.toDataURL();
  });
}

for (const fallback of [false, true]) test(`authored process masks and order compose on the real face (${fallback ? 'CPU' : 'GPU'})`, async ({ page }, info) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await mountProcessCard(page, fallback);
  const host = page.locator('#process-render-proof');
  await expect(host.locator('[data-process-kind=foil]')).toHaveAttribute('data-material-renderer', fallback ? 'fallback' : 'webgl');
  const glyph = await host.locator('[data-process-kind=foil] canvas').evaluate(node => {
    const canvas = node as HTMLCanvasElement, pixels = canvas.getContext('2d')!.getImageData(0, 0, canvas.width, canvas.height).data;
    const title = canvas.closest('.factory-artwork')!.querySelector('[data-kind=title]')!;
    const a = canvas.getBoundingClientRect(), b = title.getBoundingClientRect();
    let covered = 0, empty = 0;
    for (let y = Math.ceil((b.top - a.top) / a.height * canvas.height); y < (b.bottom - a.top) / a.height * canvas.height; y++)
      for (let x = Math.ceil((b.left - a.left) / a.width * canvas.width); x < (b.right - a.left) / a.width * canvas.width; x++) {
        if (pixels[(y * canvas.width + x) * 4 + 3] > 5) covered++; else empty++;
      }
    return { covered, empty };
  });
  expect(glyph.covered).toBeGreaterThan(100); expect(glyph.empty).toBeGreaterThan(glyph.covered);
  const original = await composite(page);
  await page.evaluate(() => {
    const proof = (window as unknown as { processRenderProof: { surface: any; render(): void; foil: any; film: any } }).processRenderProof;
    proof.surface.design.production = { ...proof.surface.design.production, layers: [proof.film, proof.foil] }; proof.render();
  });
  await expect(host.locator('[data-process-layer]').first()).toHaveAttribute('data-process-layer', 'film');
  await expect(host.locator('[data-material-settled]')).toHaveCount(2);
  expect(await composite(page)).not.toBe(original);
  await host.screenshot({ path: info.outputPath(`ordered-${fallback ? 'cpu' : 'gpu'}.png`) });
  const relief = async (direction: string) => {
    await page.evaluate(direction => {
      const proof = (window as unknown as { processRenderProof: { surface: any; render(): void; foil: any } }).processRenderProof;
      proof.surface.design.production = { ...proof.surface.design.production, layers: [{ ...proof.foil, id: 'relief', kind: 'emboss', relief: direction }] }; proof.render();
    }, direction);
    await expect(host.locator('[data-process-relief]')).toHaveAttribute('data-process-relief', direction);
    await expect(host.locator('[data-material-settled]')).toHaveCount(1);
    return host.locator('canvas').evaluate(node => (node as HTMLCanvasElement).toDataURL());
  };
  expect(await relief('raised')).not.toBe(await relief('recessed'));
  for (const kind of ['foil', 'uv']) {
    const finishAt = async (roughness: number) => {
      await page.evaluate(({ kind, roughness }) => {
        const proof = (window as unknown as { processRenderProof: { surface: any; render(): void; foil: any } }).processRenderProof;
        proof.surface.design.production = { ...proof.surface.design.production, layers: [{ ...proof.foil, id: `${kind}-${roughness}`, kind, roughness, mask: { ...proof.foil.mask, source: 'all' } }] }; proof.render();
      }, { kind, roughness });
      await expect(host.locator('[data-process-layer]')).toHaveAttribute('data-process-layer', `${kind}-${roughness}`);
      await expect(host.locator('[data-material-settled]')).toHaveCount(1);
      return host.locator('canvas').evaluate(node => (node as HTMLCanvasElement).toDataURL());
    };
    expect(await finishAt(.08)).not.toBe(await finishAt(.9));
  }
  expect(errors).toEqual([]);
});

test('PNG, element, SVG and preset plates select exact coverage independently', async ({ page }) => {
  await mountProcessCard(page, false);
  const result = await page.locator('#process-render-proof .factory-artwork').evaluate(async hostElement => {
    const load = (path: string) => import(/* @vite-ignore */ path);
    const { createProductionLayerMask } = await load('/src/cards/productionLayerMask.ts');
    const { newProductionLayer } = await load('/src/cards/cardProduction.ts');
    const host = hostElement as HTMLElement, layer = host.querySelector<HTMLElement>('[data-process-layer]')!;
    const width = 120, height = 160, base = newProductionLayer('foil').mask;
    const source = document.createElement('canvas'); source.width = 2; source.height = 2;
    const context = source.getContext('2d')!;
    context.fillStyle = '#000'; context.fillRect(0, 0, 1, 1); context.fillStyle = '#fff'; context.fillRect(1, 0, 1, 1);
    context.fillStyle = '#808080'; context.fillRect(0, 1, 1, 1);
    const image = new Image(); image.src = source.toDataURL(); await image.decode();
    const mask = (config: Record<string, unknown>) => createProductionLayerMask(host, layer, width, height, { ...base, ...config }, image).regions.getContext('2d')!.getImageData(0, 0, width, height).data as Uint8ClampedArray;
    const at = (pixels: Uint8ClampedArray, x: number, y: number) => pixels[(y * width + x) * 4];
    const alpha = mask({ source: 'png', channel: 'alpha', fit: 'stretch' });
    const luma = mask({ source: 'png', channel: 'luminance', fit: 'stretch' });
    const inverse = mask({ source: 'png', channel: 'luminance', fit: 'stretch', invert: true });
    const sample = (pixels: Uint8ClampedArray) => [[15, 20], [105, 20], [15, 140], [105, 140]].map(([x, y]) => at(pixels, x, y));
    const total = (pixels: Uint8ClampedArray) => pixels.reduce((sum, value, index) => index % 4 === 0 ? sum + value : sum, 0);
    const elements = mask({ source: 'elements', elementIds: ['title'] });
    host.querySelector<HTMLElement>('[data-face-element=title]')!.style.opacity = '0';
    const unprintedLettering = mask({ source: 'elements', elementIds: ['title'] });
    const none = mask({ source: 'elements', elementIds: ['no-such-layer'] });
    const shapes = mask({ source: 'shapes' });
    const presets = ['border', 'corners', 'diagonal', 'dots'].map(preset => total(mask({ source: 'preset', preset })));
    return { alpha: sample(alpha), luma: sample(luma), inverse: sample(inverse), elements: total(elements), unprintedLettering: total(unprintedLettering), none: total(none), shapes: total(shapes), presets };
  });
  expect(result.alpha).toEqual([255, 255, 255, 0]);
  expect(result.luma).toEqual([0, 255, 128, 0]);
  expect(result.inverse).toEqual([255, 0, 127, 255]);
  expect(result.elements).toBeGreaterThan(100); expect(result.none).toBe(0); expect(result.shapes).toBeGreaterThan(100);
  expect(result.unprintedLettering).toBe(result.elements);
  expect(result.presets.every(value => value > 100)).toBe(true); expect(new Set(result.presets).size).toBe(4);
});
