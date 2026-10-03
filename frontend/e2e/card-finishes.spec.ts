import { expect, test } from '@playwright/test';

test('laser engraving stays attached while its reflected spectrum and groove lighting move', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 820, height: 900 });
  await page.goto('/?card-finishes');
  const laser = page.locator('[data-preview-finish="laser"]');
  await laser.scrollIntoViewIfNeeded();
  const pattern = laser.locator('.card-finish-pattern');
  const reflection = laser.locator('.card-finish-base');
  const grooveLight = laser.locator('.card-finish-sheen');
  const plate = await pattern.evaluate(element => {
    const style = getComputedStyle(element);
    return { image: style.maskImage, position: style.maskPosition, size: style.maskSize };
  });
  // A computed URL alone does not prove the shared engraving asset loaded.
  expect(await page.evaluate(async mask => {
    const image = new Image();
    image.src = mask.slice(5, -2);
    await image.decode();
    return image.naturalWidth > 0;
  }, plate.image)).toBe(true);
  await laser.screenshot({ path: testInfo.outputPath('laser-rest.png') });
  await laser.hover({ position: { x: 55, y: 75 } });
  await expect(laser).toHaveAttribute('data-finish-active', 'true');
  const firstReflection = await reflection.evaluate(element => getComputedStyle(element).backgroundPosition);
  const firstGrooveLight = await grooveLight.evaluate(element => getComputedStyle(element, '::before').backgroundPosition);
  await laser.screenshot({ path: testInfo.outputPath('laser-left.png') });
  const box = (await laser.boundingBox())!;
  await page.mouse.move(box.x + box.width * .8, box.y + box.height * .8);
  await expect.poll(() => reflection.evaluate(element => getComputedStyle(element).backgroundPosition)).not.toBe(firstReflection);
  await expect.poll(() => grooveLight.evaluate(element => getComputedStyle(element, '::before').backgroundPosition)).not.toBe(firstGrooveLight);
  expect(await pattern.evaluate(element => {
    const style = getComputedStyle(element);
    return { image: style.maskImage, position: style.maskPosition, size: style.maskSize };
  })).toEqual(plate);
  await laser.screenshot({ path: testInfo.outputPath('laser-right.png') });
  await page.mouse.move(1, 1);
  await expect(laser).not.toHaveAttribute('data-finish-active');
  await expect(laser.getByText('Research Agent')).toBeVisible();
});

test('cards tilt beneath a fixed light and retain readable ink in both themes', async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/?card-finishes');
  const cards = page.locator('[data-preview-finish]');
  await expect(cards).toHaveCount(5);
  await page.getByLabel('Dark card stock').uncheck();
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
  await expect(page.locator('.card-material-canvas')).toHaveCount(160);
  await expect(page.locator('[data-material-ready]')).toHaveCount(160);
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

test('physical materials share one GPU context, respond to tilt, and leave the copy matte', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1800, height: 900 });
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    const metrics = { contexts: 0 };
    Object.assign(window, { materialMetrics: metrics });
    Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', { value: function(type: string, ...args: unknown[]) {
      if (type === 'webgl') metrics.contexts++;
      return Reflect.apply(original, this, [type, ...args]);
    } });
  });
  await page.goto('/?card-finishes');
  await expect(page.locator('[data-material-ready]')).toHaveCount(4);
  const signature = (finish: string) => page.locator(`[data-preview-finish="${finish}"] canvas`).evaluate(element => {
    const canvas = element as HTMLCanvasElement;
    const ctx = canvas.getContext('2d')!;
    const bytes = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    let hash = 2166136261;
    for (let i = 0; i < bytes.length; i += 19) hash = Math.imul(hash ^ bytes[i],16777619);
    const alpha = (y: number) => ctx.getImageData(Math.floor(canvas.width*.75), Math.floor(canvas.height*y), 1, 1).data[3];
    const samples = Array.from({ length: Math.floor(bytes.length / 127) }, (_, index) => bytes[index * 127]);
    return { hash, samples, art: alpha(.35), copy: alpha(.9), width: canvas.width, height: canvas.height };
  });
  for (const finish of ['foil','rainbow','starlight','laser']) {
    const card = page.locator(`[data-preview-finish="${finish}"]`);
    const rest = await signature(finish);
    expect(rest.art).toBeGreaterThan(180);
    expect(rest.copy).toBeLessThan(25);
    expect(rest.width).toBeLessThanOrEqual(640);
    expect(rest.height).toBeLessThanOrEqual(800);
    await card.hover({ position: { x: 50, y: 65 } });
    await expect.poll(async () => (await signature(finish)).hash).not.toBe(rest.hash);
    await page.mouse.move(1,1);
    // Canvas GPU/CPU readback can round premultiplied colour channels differently by one unit.
    await expect.poll(async () => {
      const current = await signature(finish);
      return current.samples.reduce((sum, value, index) => sum + Math.abs(value-rest.samples[index]),0) / rest.samples.length;
    }, { message: `${finish} restores its rest pose` }).toBeLessThan(1.5);
  }
  expect(await page.evaluate(() => (window as unknown as { materialMetrics: { contexts: number } }).materialMetrics.contexts)).toBe(1);
  await page.screenshot({ path: testInfo.outputPath('physical-materials-dark.png'), fullPage: true, animations: 'disabled' });
});

test('unavailable WebGL falls back to readable, interactive print plates', async ({ page }) => {
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', { value: function(type: string, ...args: unknown[]) {
      return type === 'webgl' ? null : Reflect.apply(original, this, [type, ...args]);
    } });
  });
  await page.goto('/?card-finishes');
  await expect(page.locator('[data-material-ready]')).toHaveCount(0);
  const card = page.locator('[data-preview-finish="rainbow"]');
  await expect(card.locator('.card-finish-pattern')).toBeVisible();
  await expect(card.getByText('Research Agent')).toBeVisible();
  await card.hover();
  await expect(card).toHaveAttribute('data-card-tilting','true');
});

test('holo uses the same physical print in the showcase, hand and draggable world card', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1600, height: 1100 });
  await page.goto('/?card-finishes');
  await expect(page.locator('[data-preview-finish="rainbow"] [data-material-ready]')).toHaveCount(1);
  // Mount the production card components in an isolated, backend-free test canvas.
  await page.evaluate(async () => {
    const load = (path: string) => import(/* @vite-ignore */ path);
    document.documentElement.dataset.theme = 'dark';
    const resource = (suffix: string) => performance.getEntriesByType('resource').find(entry => entry.name.includes(suffix))!.name;
    const { default: React } = await load(resource('/deps/react.js'));
    const { default: ReactDOM } = await load(resource('/deps/react-dom_client.js'));
    const { CardStock, CardFace } = await load('/src/components/CardFace.tsx');
    const { WorldCardNode } = await load('/src/cards/CardFrame.tsx');
    const { ReactFlow } = await load(resource('/deps/@xyflow_react.js'));
    const { useWorldStore } = await load('/src/state/worldStore.ts');
    const { useNodeSurfaceStore } = await load('/src/state/nodeSurfaces.ts');
    const { TEST_CATALOG } = await load('/src/state/catalog.fixture.ts');
    const card = { id: 'material-world', type: 'agent', name: 'Holo world card', status: 'idle', finish: 'rainbow',
      config: { system_instruction: 'The description stays readable on matte stock.' },
      position: { x: 60, y: 20 }, size: { width: 224, height: 300 }, expanded: false };
    useWorldStore.setState({ cards: [card], edges: [], events: [], catalog: TEST_CATALOG });
    useNodeSurfaceStore.setState({ surfaceLevels: { [card.id]: 'preview' }, baseLevels: { [card.id]: 'preview' } });
    const element = document.createElement('div');
    element.id = 'material-surface-comparison';
    element.style.cssText = 'display:flex;align-items:center;gap:70px;height:350px;margin-top:28px';
    document.querySelector('.finish-preview')!.append(element);
    ReactDOM.createRoot(element).render(React.createElement(React.Fragment, null,
      React.createElement(CardStock, { finish: 'rainbow', quality: 'thumbnail', 'data-test-hand': true,
        style: { width: 106, height: 122, flex: 'none', transform: 'rotate(-8deg)' } },
      React.createElement(CardFace, { label: 'Holo hand', icon: '◇' })),
      React.createElement('div', { style: { width: 650, height: 350 }, 'data-test-world': true },
        React.createElement(ReactFlow, { defaultNodes: [{ id: card.id, type: 'worldCard', position: card.position,
          style: { width: 224, height: 300 }, data: { card, surfaceLevel: 'preview', renderLOD: 'full' } }],
          nodeTypes: { worldCard: WorldCardNode }, defaultViewport: { x: 0, y: 0, zoom: 1 }, minZoom: .3,
          onInit: (instance: unknown) => Object.assign(window, { materialTestFlow: instance }) })),
    ));
  });
  const hand = page.locator('[data-test-hand]');
  const world = page.locator('.world-card[data-card-id="material-world"]');
  await hand.scrollIntoViewIfNeeded();
  await expect(hand.locator('[data-material-ready]')).toHaveCount(1);
  await expect(world.locator('[data-material-ready]')).toHaveCount(1);
  const colors = (selector: string) => page.locator(`${selector} canvas`).evaluate(element => {
    const canvas = element as HTMLCanvasElement, ctx = canvas.getContext('2d')!;
    return [[.78,.08],[.85,.2]].flatMap(([x,y]) => {
      const pixels = ctx.getImageData(Math.floor(canvas.width*x),Math.floor(canvas.height*y),
        Math.max(1,Math.floor(canvas.width*.04)),Math.max(1,Math.floor(canvas.height*.04))).data;
      return [0,1,2].map(channel => {
        let total = 0; for (let i=channel; i<pixels.length; i+=4) total += pixels[i];
        return total/(pixels.length/4);
      });
    });
  });
  const expected = await colors('[data-preview-finish="rainbow"]');
  for (const selector of ['[data-test-hand]', '.world-card[data-card-id="material-world"]']) {
    const actual = await colors(selector);
    actual.forEach((value,index) => expect(Math.abs(value-expected[index])).toBeLessThan(18));
  }
  const rest = await colors('.world-card[data-card-id="material-world"]');
  const before = (await world.boundingBox())!;
  await page.mouse.move(before.x+30,before.y+30);
  await page.mouse.down();
  await page.mouse.move(before.x+180,before.y+40,{ steps: 8 });
  await page.mouse.up();
  await page.mouse.move(1,1);
  await expect.poll(async () => (await world.boundingBox())!.x-before.x).toBeGreaterThan(100);
  await expect(world.locator('[data-material-ready]')).toHaveCount(1);
  const after = await colors('.world-card[data-card-id="material-world"]');
  after.forEach((value,index) => expect(Math.abs(value-rest[index])).toBeLessThan(2));
  await expect(world.getByText('The description stays readable on matte stock.')).toBeVisible();
  expect(await world.locator('canvas').evaluate(element => {
    const canvas = element as HTMLCanvasElement;
    return canvas.getContext('2d')!.getImageData(Math.floor(canvas.width*.75),Math.floor(canvas.height*.7),1,1).data[3];
  })).toBeLessThan(25);
  await page.screenshot({ path: testInfo.outputPath('holo-consistent-surfaces.png'), fullPage: true, animations: 'disabled' });
  for (const lod of ['mid','far']) {
    await page.evaluate(({ lod }) => {
      const flow = (window as unknown as { materialTestFlow: {
        zoomTo: (zoom: number) => void;
        setNodes: (update: (nodes: { data: Record<string, unknown> }[]) => unknown[]) => void;
      } }).materialTestFlow;
      flow.zoomTo(.7);
      flow.setNodes(nodes => nodes.map(node => ({ ...node, data: { ...node.data, renderLOD: lod } })));
    }, { lod });
    await expect(world).toHaveAttribute('data-render-lod',lod);
    await expect(world).toHaveAttribute('data-finish','rainbow');
    await expect(world.locator('[data-material-ready]')).toHaveCount(1);
    await expect(world.getByText('Holo world card', { exact: true })).toBeVisible();
  }
});

test('workspace and inspector titlebars crop the foil without squeezing it into pixelated stripes', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.goto('/?card-finishes');
  await page.evaluate(async () => {
    const load = (path: string) => import(/* @vite-ignore */ path);
    const resource = (suffix: string) => performance.getEntriesByType('resource').find(entry => entry.name.includes(suffix))!.name;
    const { default: React } = await load(resource('/deps/react.js'));
    const { default: ReactDOM } = await load(resource('/deps/react-dom_client.js'));
    const { WorkspaceSurface } = await load('/src/cards/NodeWorkspace.tsx');
    const { WorldCardNode } = await load('/src/cards/CardFrame.tsx');
    const { ReactFlowProvider } = await load(resource('/deps/@xyflow_react.js'));
    const { useWorldStore } = await load('/src/state/worldStore.ts');
    const { useNodeSurfaceStore } = await load('/src/state/nodeSurfaces.ts');
    const { TEST_CATALOG } = await load('/src/state/catalog.fixture.ts');
    const definition = { ...TEST_CATALOG.node_types.find((entry: { id: string }) => entry.id === 'agent'),
      id: 'test.material', label: 'Research', traits: [] };
    const card = { id: 'chrome-workspace', type: definition.id, name: 'Holo research workspace', status: 'idle', finish: 'rainbow',
      config: {}, position: { x: 0, y: 0 }, size: { width: 1100, height: 250 }, expanded: true };
    const inspector = { ...card, id: 'chrome-inspector', name: 'Holo research inspector' };
    useWorldStore.setState({ cards: [card,inspector], edges: [], events: [], catalog: {
      ...TEST_CATALOG, node_types: [...TEST_CATALOG.node_types,definition] } });
    useNodeSurfaceStore.setState({ surfaceLevels: { [card.id]: 'workspace', [inspector.id]: 'inspector' },
      baseLevels: { [card.id]: 'preview', [inspector.id]: 'preview' } });
    document.documentElement.dataset.theme = 'dark';
    const element = document.createElement('section');
    element.id = 'chrome-material-comparison';
    element.style.cssText = 'display:flex;align-items:flex-start;gap:24px;padding:24px;background:var(--surface-solid)';
    document.body.append(element);
    ReactDOM.createRoot(element).render(React.createElement(React.Fragment,null,
      React.createElement('div', { 'data-test-workspace': true, style: { position: 'relative',width: 1100,height: 250,flex: 'none' } },
        React.createElement(WorkspaceSurface,{ card })),
      React.createElement('div', { style: { position: 'relative',width: 360,height: 280,flex: 'none' } },
        React.createElement(ReactFlowProvider,null,React.createElement(WorldCardNode,{ id: inspector.id,
          data: { card: inspector,surfaceLevel: 'inspector',renderLOD: 'full' },selected: false,dragging: false }))),
    ));
  });
  const comparison = page.locator('#chrome-material-comparison');
  await comparison.scrollIntoViewIfNeeded();
  const workspace = comparison.locator('.workspace-titlebar');
  const inspector = comparison.locator('.card-header');
  for (const header of [workspace,inspector]) {
    await expect(header.locator('[data-material-surface="chrome"][data-material-ready]')).toHaveCount(1);
    const raster = await header.locator('canvas').evaluate(element => {
      const canvas = element as HTMLCanvasElement;
      return { width: canvas.width,height: canvas.height,cssHeight: canvas.offsetHeight,opacity: Number(getComputedStyle(canvas).opacity) };
    });
    expect(raster.height).toBeGreaterThanOrEqual(raster.cssHeight-1);
    expect(raster.width).toBeLessThanOrEqual(1536);
    expect(raster.opacity).toBeLessThan(.5);
    await expect(header.getByText(/Holo research/)).toBeVisible();
  }
  // A squeezed portrait has overwhelmingly horizontal edges. An isotropic crop has
  // comparable horizontal and vertical pixel variation at the titlebar's native resolution.
  expect(await workspace.locator('canvas').evaluate(element => {
    const canvas = element as HTMLCanvasElement, w = canvas.width,h = canvas.height;
    const pixels = canvas.getContext('2d')!.getImageData(0,0,w,h).data;
    let horizontal = 0,vertical = 0;
    for (let y=3; y<h-4; y++) for (let x=Math.floor(w*.25); x<w-4; x++) {
      const index = (y*w+x)*4;
      for (let c=0; c<3; c++) {
        horizontal += Math.abs(pixels[index+c]-pixels[index+4+c]);
        vertical += Math.abs(pixels[index+c]-pixels[index+w*4+c]);
      }
    }
    return vertical/Math.max(1,horizontal);
  })).toBeLessThan(4);
  await expect(comparison.locator('.workspace-content .card-finish-layer')).toHaveCount(0);
  await comparison.screenshot({ path: testInfo.outputPath('holo-workspace-chrome-dark.png'), animations: 'disabled' });
  await page.evaluate(() => { document.documentElement.dataset.theme = 'light'; });
  await comparison.screenshot({ path: testInfo.outputPath('holo-workspace-chrome-light.png'), animations: 'disabled' });
  await page.locator('[data-test-workspace]').evaluate(element => { (element as HTMLElement).style.width = '420px'; });
  await expect.poll(() => workspace.locator('canvas').evaluate(element => (element as HTMLCanvasElement).width)).toBeLessThan(700);
  await expect(workspace.getByText('Holo research workspace', { exact: true })).toBeVisible();
});
