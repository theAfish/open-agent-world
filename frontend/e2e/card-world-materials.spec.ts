import { expect, test, type Locator, type Page } from '@playwright/test';

async function mountWorldCards(page: Page, fallback: boolean) {
  if (fallback) await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', { value: function(type: string, ...args: unknown[]) {
      return type.includes('webgl') ? null : Reflect.apply(original, this, [type, ...args]);
    } });
  });
  await page.setViewportSize({ width: 1440, height: 780 });
  await page.goto('/?card-finishes');
  await expect(page.locator('[data-preview-finish="rainbow"] [data-material-ready]')).toHaveCount(1);
  await page.evaluate(async () => {
    const load = (path: string) => import(/* @vite-ignore */ path);
    const resource = (suffix: string) => performance.getEntriesByType('resource').find(entry => entry.name.includes(suffix))!.name;
    const { default: React } = await load(resource('/deps/react.js'));
    const { default: ReactDOM } = await load(resource('/deps/react-dom_client.js'));
    const { WorldCardNode } = await load('/src/cards/CardFrame.tsx');
    const { ReactFlow } = await load(resource('/deps/@xyflow_react.js'));
    const { useWorldStore } = await load('/src/state/worldStore.ts');
    const { useNodeSurfaceStore } = await load('/src/state/nodeSurfaces.ts');
    const { TEST_CATALOG } = await load('/src/state/catalog.fixture.ts');
    document.documentElement.dataset.theme = 'light';
    const photo = 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><path fill="#658e7d" d="M0 0h100v100H0z"/><circle fill="#cad6ad" cx="55" cy="35" r="24"/><path fill="#496070" d="m0 90 35-45 65 55H0"/></svg>');
    const cards = ['normal', 'foil', 'rainbow', 'image'].map((finish, index) => ({
      id: 'world-' + finish, type: finish === 'image' ? 'image' : 'agent',
      name: finish === 'image' ? 'Printed artwork' : finish === 'normal' ? 'Normal edition' : finish === 'foil' ? 'Foil edition' : 'Codex',
      finish: finish === 'image' ? 'rainbow' : finish, status: 'idle',
      config: { system_instruction: 'You are Codex working in Open Agent World. Help with the project and keep the print readable.',
        filename: 'Landscape print', preview_url: photo },
      position: { x: 30 + index * 254, y: 24 }, size: { width: 224, height: 300 }, expanded: false,
    }));
    useWorldStore.setState({ cards, edges: [], events: [], catalog: TEST_CATALOG });
    const levels = Object.fromEntries(cards.map(card => [card.id, 'preview']));
    useNodeSurfaceStore.setState({ surfaceLevels: levels, baseLevels: levels });
    document.querySelector('.finish-preview')!.setAttribute('style', 'display:none');
    const mount = document.createElement('div');
    mount.id = 'actual-card-proof';
    mount.style.cssText = 'position:fixed;inset:0;background:var(--canvas);';
    document.body.append(mount);
    ReactDOM.createRoot(mount).render(React.createElement(ReactFlow, {
      defaultNodes: cards.map(card => ({ id: card.id, type: 'worldCard', position: card.position,
        style: { width: 224, height: 300 }, data: { card, surfaceLevel: 'preview', renderLOD: 'full' } })),
      nodeTypes: { worldCard: WorldCardNode }, defaultViewport: { x: 0, y: 50, zoom: 1.35 }, minZoom: .3,
      onInit: (instance: unknown) => Object.assign(window, { worldPrintFlow: instance }),
    }));
  });
  await page.evaluate(() => document.fonts.ready);
}

async function setPose(card: Locator, x: number, y: number) {
  await card.evaluate((element, pose) => element.dispatchEvent(new CustomEvent('card-material-light', {
    detail: { ...pose, active: true, immediate: true },
  })), { x, y });
  await expect(card.locator('[data-material-settled]')).toHaveCount(1);
  await card.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

async function coatedFraction(card: Locator, selector?: string) {
  return card.evaluate((element, selector) => {
    const canvas = element.querySelector('canvas')!;
    const region = selector ? element.querySelector(selector)! : canvas;
    const c = canvas.getBoundingClientRect(), r = region.getBoundingClientRect();
    const data = canvas.getContext('2d')!.getImageData(0, 0, canvas.width, canvas.height).data;
    const insetX = r.width > 8 ? 2 : 0, insetY = r.height > 8 ? 2 : 0;
    let coated = 0, count = 0;
    for (let y = Math.max(0, Math.floor((r.top-c.top)/c.height*canvas.height)+insetY); y < Math.min(canvas.height, (r.bottom-c.top)/c.height*canvas.height)-insetY; y++)
      for (let x = Math.max(0, Math.floor((r.left-c.left)/c.width*canvas.width)+insetX); x < Math.min(canvas.width, (r.right-c.left)/c.width*canvas.width)-insetX; x++) {
        coated += data[(y*canvas.width+x)*4+3] > 3 ? 1 : 0; count++;
      }
    return coated/Math.max(1,count);
  }, selector);
}

async function comparePrint(page: Page, card: Locator, selector: string) {
  const element = card.locator(selector);
  const ink = await element.evaluate(e => getComputedStyle(e).color);
  await card.locator('.card-finish-layer').evaluate(e => { (e as HTMLElement).style.visibility = 'hidden'; });
  const uncoated = await element.screenshot();
  await card.locator('.card-finish-layer').evaluate(e => { (e as HTMLElement).style.removeProperty('visibility'); });
  const coated = await element.screenshot();
  return page.evaluate(async ({ a, b, ink }) => {
    const read = async (base64: string) => {
      const image = new Image(); image.src = 'data:image/png;base64,' + base64; await image.decode();
      const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
      const ctx = canvas.getContext('2d')!; ctx.drawImage(image, 0, 0);
      return ctx.getImageData(0,0,canvas.width,canvas.height).data;
    };
    const [before, after] = await Promise.all([read(a), read(b)]);
    const rgb = ink.match(/[\d.]+/g)!.slice(0,3).map(Number);
    let inkPixels = 0, stableInk = 0, changed = 0;
    for (let i = 0; i < before.length; i += 4) {
      const solidInk = rgb.every((value, channel) => Math.abs(value-before[i+channel]) < 3);
      const delta = Math.max(...[0,1,2].map(channel => Math.abs(before[i+channel]-after[i+channel])));
      if (solidInk) { inkPixels++; stableInk += delta <= 2 ? 1 : 0; }
      changed += delta > 3 ? 1 : 0;
    }
    return { inkPixels, stable: stableInk/Math.max(1,inkPixels), changed };
  }, { a: uncoated.toString('base64'), b: coated.toString('base64'), ink });
}

for (const fallback of [false, true]) test(`real cards have continuous laminate and stable top-print ink (${fallback ? 'CPU' : 'GPU'})`, async ({ page }, info) => {
  await mountWorldCards(page, fallback);
  const card = page.locator('.world-card[data-card-id="world-rainbow"]');
  const photo = page.locator('.world-card[data-card-id="world-image"]');
  const foil = page.locator('.world-card[data-card-id="world-foil"]');
  await expect(card.locator('[data-material-renderer]')).toHaveAttribute('data-material-renderer', fallback ? 'fallback' : 'webgl');
  await setPose(card, .6, -.3);
  expect(await coatedFraction(card)).toBeGreaterThan(.88);
  for (const selector of ['.card-title-group', '.card-kind-icon', '.node-preview-content', '.node-preview-summary p']) {
    expect(await coatedFraction(card, selector)).toBeGreaterThan(.8);
  }
  for (const selector of ['.card-title-group h2', '.node-preview-summary p', '.card-kind-icon']) {
    const print = await comparePrint(page, card, selector);
    expect(print.inkPixels).toBeGreaterThan(15);
    expect(print.stable).toBeGreaterThan(.98);
    expect(print.changed).toBeGreaterThan(30); // Film continues through the spaces between the ink.
  }
  await setPose(photo, .6, -.3);
  const image = await comparePrint(page, photo, '.node-preview-summary img');
  expect(image.changed).toBeGreaterThan(500); // Actual artwork is below the film.
  await expect(card.locator('.card-title-group')).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
  await expect(page.locator('[data-card-id="world-normal"] .world-card-print')).toHaveCount(1);
  await expect(page.locator('[data-card-id="world-normal"] canvas')).toHaveCount(0);
  expect(await coatedFraction(foil, '.world-card-print-rule')).toBeGreaterThan(.6);
  await page.screenshot({ path: info.outputPath('actual-cards-light.png') });
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
  const darkInk = await comparePrint(page, card, '.card-title-group h2');
  expect(darkInk.inkPixels).toBeGreaterThan(15); expect(darkInk.stable).toBeGreaterThan(.98);
  await page.screenshot({ path: info.outputPath('actual-cards-dark.png') });
  await page.evaluate(() => { document.documentElement.dataset.theme = 'light'; });
  for (const lod of ['mid', 'far', 'full']) {
    await page.evaluate(lod => (window as unknown as { worldPrintFlow: {
      setNodes: (update: (nodes: { data: Record<string, unknown> }[]) => unknown[]) => void
    } }).worldPrintFlow.setNodes(nodes => nodes.map(node => ({ ...node, data: { ...node.data, renderLOD: lod } }))), lod);
    await expect(card).toHaveAttribute('data-render-lod', lod);
    await setPose(card, .6, -.3);
    expect(await coatedFraction(card)).toBeGreaterThan(.88);
    await expect(card.getByText('Codex', { exact: true })).toBeVisible();
  }
  await card.getByRole('button', { name: /Collapse/ }).click();
  await expect(card).toHaveAttribute('data-surface-level', 'node');
  await setPose(card, .6, -.3);
  expect(await coatedFraction(card)).toBeGreaterThan(.88);
});
