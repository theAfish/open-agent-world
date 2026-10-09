import { expect, test, type Locator } from '@playwright/test';

async function materialPixels(card: Locator) {
  return card.locator('canvas').evaluate(async element => {
    await document.fonts.ready;
    // Initial ResizeObserver delivery and its queued mask rebuild precede the sample.
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const canvas = element as HTMLCanvasElement, ctx = canvas.getContext('2d')!;
    const pixels = ctx.getImageData(0,0,canvas.width,canvas.height).data;
    let hash = 2166136261, covered = 0, peak = 0, bright = 0, weak = 0, middle = 0, chromatic = 0;
    for (let i=0; i<pixels.length; i+=4) {
      const alpha = pixels[i+3];
      // Canvas GPU/CPU readback can round RGB by one level on the first read.
      // Alpha is exact; spectral travel is measured separately below.
      hash = Math.imul(hash ^ alpha,16777619);
      covered += alpha > 2 ? 1 : 0; bright += alpha > 128 ? 1 : 0;
      weak += alpha > 0 && alpha <= 16 ? 1 : 0; middle += alpha > 16 ? 1 : 0;
      peak = Math.max(peak,alpha);
      chromatic += alpha > 8 && Math.max(pixels[i],pixels[i+1],pixels[i+2])-Math.min(pixels[i],pixels[i+1],pixels[i+2]) > 65 ? 1 : 0;
    }
    // Sample the interior of every actual text rectangle, rather than one arbitrary copy pixel.
    const origin = (node: HTMLElement) => {
      let x = 0, y = 0;
      for (let current: HTMLElement | null = node; current; current = current.offsetParent as HTMLElement | null) {
        x += current.offsetLeft; y += current.offsetTop;
      }
      return { x,y };
    };
    const host = canvas.closest('.card-finish-surface')!, start = origin(canvas);
    let textPeak = 0, iconPeak = 0;
    for (const node of host.querySelectorAll<HTMLElement>('.card-face-copy strong, .card-face-copy small, .card-title-group, .card-face-symbol > svg')) {
      const pos = origin(node), scaleX = canvas.width/canvas.offsetWidth, scaleY = canvas.height/canvas.offsetHeight;
      // SVGs have no offset layout; test the badge interior separately below.
      if (node instanceof SVGElement) continue;
      for (let y=Math.max(0,Math.ceil((pos.y-start.y+2)*scaleY)); y<Math.min(canvas.height,(pos.y-start.y+node.offsetHeight-2)*scaleY); y++) {
        for (let x=Math.max(0,Math.ceil((pos.x-start.x+2)*scaleX)); x<Math.min(canvas.width,(pos.x-start.x+node.offsetWidth-2)*scaleX); x++) {
          textPeak = Math.max(textPeak,pixels[(y*canvas.width+x)*4+3]);
        }
      }
    }
    const badge = host.querySelector<HTMLElement>('.card-face-symbol');
    if (badge) {
      const pos = origin(badge);
      for (let y=.25; y<.75; y+=.1) for (let x=.25; x<.75; x+=.1) {
        const px = Math.floor((pos.x-start.x+badge.offsetWidth*x)*canvas.width/canvas.offsetWidth);
        const py = Math.floor((pos.y-start.y+badge.offsetHeight*y)*canvas.height/canvas.offsetHeight);
        iconPeak = Math.max(iconPeak,pixels[(py*canvas.width+px)*4+3]);
      }
    }
    return { hash, coverage: covered/(pixels.length/4), chromatic: chromatic/(pixels.length/4), bright: bright/(pixels.length/4), weak: weak/(pixels.length/4), middle: middle/(pixels.length/4), peak, textPeak, iconPeak,
      width: canvas.width, height: canvas.height, renderer: canvas.parentElement!.dataset.materialRenderer };
  });
}

test('engraved diffraction moves with angle, has no trail, and returns to its resting laminate', async ({ page }, info) => {
  await page.goto('/?card-finishes');
  const card = page.locator('[data-preview-finish="laser"]');
  await expect(card.locator('[data-material-ready]')).toHaveCount(1);
  const rest = await materialPixels(card);
  const box = (await card.boundingBox())!;
  await card.hover({ position: { x: box.width*.75,y: box.height*.3 } });
  await expect.poll(async () => (await materialPixels(card)).peak).toBeGreaterThan(16);
  await expect(card.locator('[data-material-settled]')).toHaveCount(1);
  const first = await materialPixels(card);
  await card.screenshot({ path: info.outputPath('laser-local-reflection.png') });
  await page.mouse.move(box.x+box.width*.9,box.y+box.height*.8);
  await expect.poll(async () => (await materialPixels(card)).hash).not.toBe(first.hash);
  await page.mouse.move(1,1);
  await expect.poll(async () => (await materialPixels(card)).hash).toBe(rest.hash);
  // The same angle samples the same microstructure: there is no time-dependent shimmer or trail.
  await card.hover({ position: { x: box.width*.75,y: box.height*.3 } });
  await expect.poll(async () => (await materialPixels(card)).hash).toBe(first.hash);
});

test('cards tilt beneath a fixed light and retain readable ink in both themes', async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/?card-finishes');
  const cards = page.locator('[data-preview-finish]');
  await expect(cards).toHaveCount(5);
  await page.getByLabel('Dark surroundings').uncheck();
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
  await page.getByLabel('Dark surroundings').check();
  await page.screenshot({ path: testInfo.outputPath('finishes-dark.png'), fullPage: true });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect(rainbow).not.toHaveAttribute('data-card-tilting');
  await expect(rainbow).toHaveCSS('transform', 'none');
  await page.getByRole('button', { name: 'Reset view' }).click();
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

test('materials share one GPU, give holo broad spectral coverage, and protect ink in both themes', async ({ page }, info) => {
  await page.setViewportSize({ width: 1800,height: 900 });
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    const metrics = { contexts: 0 };
    Object.assign(window,{ materialMetrics: metrics });
    Object.defineProperty(HTMLCanvasElement.prototype,'getContext',{ value: function(type: string,...args: unknown[]) {
      if (type === 'webgl') metrics.contexts++;
      return Reflect.apply(original,this,[type,...args]);
    } });
  });
  await page.goto('/?card-finishes');
  await expect(page.locator('[data-material-ready]')).toHaveCount(4);
  const contexts = () => page.evaluate(() => (window as unknown as { materialMetrics: { contexts: number } }).materialMetrics.contexts);
  expect(await contexts()).toBe(1);
  const samples = [];
  for (const dark of [true,false]) {
    await page.getByLabel('Dark surroundings').setChecked(dark);
    for (const finish of ['foil','rainbow','starlight','laser']) {
      const card = page.locator(`[data-preview-finish="${finish}"]`);
      const rest = await materialPixels(card);
      if (finish === 'rainbow') {
        expect(rest.chromatic).toBeGreaterThan(.25);
        expect(rest.middle).toBeGreaterThan(.28);
      }
      expect(rest.width).toBeLessThanOrEqual(640);
      expect(rest.height).toBeLessThanOrEqual(800);
      const box = (await card.boundingBox())!;
      let peak = 0;
      for (const [x,y] of [[.2,.15],[.75,.3],[.5,.5],[.9,.8]]) {
        await page.mouse.move(box.x+box.width*x,box.y+box.height*y);
        await expect(card).toHaveAttribute('data-finish-active','true');
        await expect.poll(async () => (await materialPixels(card)).renderer).toBe('webgl');
        // Wait for the coalesced input frame, not for an arbitrary animation duration.
        await expect.poll(() => card.evaluate(element => element.style.getPropertyValue('--pointer-x')))
          .toBe((2*x-1).toFixed(3));
        await expect(card.locator('[data-material-settled]')).toHaveCount(1);
        const sample = await materialPixels(card);
        if (finish === 'rainbow') {
          expect(sample.chromatic).toBeGreaterThan(.22);
          expect(sample.middle).toBeGreaterThan(.28);
        }
        if (finish === 'foil') expect(sample.coverage).toBeLessThan(.06);
        if (finish === 'starlight') expect(sample.bright).toBeLessThan(.01);
        expect(sample.coverage).toBeLessThan(.72);
        expect(sample.textPeak).toBe(0);
        expect(sample.iconPeak).toBe(0);
        peak = Math.max(peak,sample.peak);
        samples.push({ dark,finish,x,y,...sample });
      }
      expect(peak).toBeGreaterThan(finish === 'foil' ? 100 : finish === 'starlight' ? 25 : 16);
      if(finish !== 'foil') expect(peak).toBeLessThanOrEqual(82);
      await card.screenshot({ path: info.outputPath(`${finish}-${dark ? 'dark' : 'light'}-active.png`) });
      await page.mouse.move(1,1);
      await expect.poll(async () => (await materialPixels(card)).hash).toBe(rest.hash);
    }
  }
  expect(await contexts()).toBe(1);
  await info.attach('material-pixel-coverage',{ body: JSON.stringify(samples,null,2),contentType: 'application/json' });
});

test('unavailable WebGL retains the material shape and protected print', async ({ page }) => {
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    Object.defineProperty(HTMLCanvasElement.prototype,'getContext',{ value: function(type: string,...args: unknown[]) {
      return type === 'webgl' ? null : Reflect.apply(original,this,[type,...args]);
    } });
  });
  await page.goto('/?card-finishes');
  const card = page.locator('[data-preview-finish="rainbow"]');
  await expect(card.locator('[data-material-ready]')).toHaveCount(1);
  const rest = await materialPixels(card);
  expect(rest.chromatic).toBeGreaterThan(.2);
  const box = (await card.boundingBox())!;
  await card.hover({ position: { x: box.width*.75,y: box.height*.3 } });
  await expect.poll(async () => (await materialPixels(card)).renderer).toBe('fallback');
  const sample = await materialPixels(card);
  expect(sample.chromatic).toBeGreaterThan(.2);
  expect(sample.coverage).toBeLessThan(.72);
  expect(sample.middle).toBeGreaterThan(.28);
  expect(sample.textPeak).toBe(0);
  expect(sample.iconPeak).toBe(0);
  await expect(card.getByText('Research Agent')).toBeVisible();
  await page.mouse.move(1,1);
  await expect.poll(async () => (await materialPixels(card)).hash).toBe(rest.hash);
});

test('fallback representative poses retain separate material identities', async ({ page }) => {
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    Object.defineProperty(HTMLCanvasElement.prototype,'getContext',{ value: function(type: string,...args: unknown[]) {
      return type === 'webgl' ? null : Reflect.apply(original,this,[type,...args]);
    } });
  });
  await page.goto('/?card-finishes');
  await page.getByLabel('Study lighting').selectOption('representative');
  await expect(page.locator('[data-material-renderer="fallback"]')).toHaveCount(4);
  const signatures = new Set<number>();
  for (const finish of ['foil','rainbow','starlight','laser']) {
    const sample = await materialPixels(page.locator(`[data-preview-finish="${finish}"]`));
    expect(sample.coverage).toBeGreaterThan(0);
    if (finish === 'rainbow') expect(sample.chromatic).toBeGreaterThan(.25);
    if (finish === 'foil') expect(sample.coverage).toBeLessThan(.06);
    if (finish === 'starlight') expect(sample.bright).toBeLessThan(.01);
    expect(sample.textPeak).toBe(0);
    expect(sample.iconPeak).toBe(0);
    signatures.add(sample.hash);
  }
  expect(signatures.size).toBe(4);
  const laserCoverage = (await materialPixels(page.locator('[data-preview-finish="laser"]'))).coverage;
  await page.locator('[data-preview-finish="laser"]').evaluate(element =>
    element.dispatchEvent(new CustomEvent('card-material-light',{ detail: { x: -.7,y: -.7,active: true } })));
  await expect.poll(async () => (await materialPixels(page.locator('[data-preview-finish="laser"]'))).coverage).toBeLessThan(laserCoverage * .5);
  await page.getByLabel('Study lighting').selectOption('pointer');
  for (const finish of ['foil','rainbow','starlight','laser']) {
    await expect(page.locator(`[data-preview-finish="${finish}"] [data-material-settled]`)).toHaveCount(1);
    expect((await materialPixels(page.locator(`[data-preview-finish="${finish}"]`))).coverage).toBeGreaterThan(0);
  }
});

test('explicit uncoated regions remain protected when their role changes without resizing', async ({ page }) => {
  await page.goto('/?card-finishes');
  const card = page.locator('[data-preview-finish="rainbow"]');
  await expect(card.locator('[data-material-ready]')).toHaveCount(1);
  const box = (await card.boundingBox())!;
  await card.hover({ position: { x: box.width*.9,y: box.height*.8 } });
  await expect.poll(async () => (await materialPixels(card)).peak).toBeGreaterThan(20);
  const interiorPeak = () => card.locator('canvas').evaluate(element => {
    const canvas = element as HTMLCanvasElement;
    const data = canvas.getContext('2d')!.getImageData(Math.floor(canvas.width*.5),Math.floor(canvas.height*.25),
      Math.floor(canvas.width*.3),Math.floor(canvas.height*.28)).data;
    let peak = 0;
    for (let i=3; i<data.length; i+=4) peak = Math.max(peak,data[i]);
    return peak;
  });
  expect(await interiorPeak()).toBeGreaterThan(20);
  await card.locator('.card-face-art').evaluate(element => element.setAttribute('data-material-region','background'));
  await expect.poll(interiorPeak).toBe(0);
  await card.locator('.card-face-art').evaluate(element => element.removeAttribute('data-material-region'));
  await expect.poll(interiorPeak).toBeGreaterThan(20);
});

test('holo preserves printed stock across showcase, hand, dragging and semantic zoom', async ({ page }, testInfo) => {
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
  // Transparent world-card ink sits above a continuous coating; backed showcase copy uses a knockout.
  expect((await materialPixels(hand)).chromatic).toBeGreaterThan(.1);
  expect((await materialPixels(world)).textPeak).toBeGreaterThan(8);
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
  const bodyAlpha = await world.locator('canvas').evaluate(element => {
    const canvas = element as HTMLCanvasElement;
    return canvas.getContext('2d')!.getImageData(Math.floor(canvas.width*.75),Math.floor(canvas.height*.7),1,1).data[3];
  });
  expect(bodyAlpha).toBeGreaterThan(3);
  expect(bodyAlpha).toBeLessThanOrEqual(82); // Continuous, bounded film also covers the lower stock.
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

test('workspace and inspector chrome stay matte regardless of the saved card finish', async ({ page }, testInfo) => {
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
    await expect(header.locator('.card-finish-layer')).toHaveCount(0);
    await expect(header.locator('canvas')).toHaveCount(0);
    await expect(header.getByText(/Holo research/)).toBeVisible();
  }
  await expect(comparison.locator('.card-finish-layer')).toHaveCount(0);
  await comparison.screenshot({ path: testInfo.outputPath('matte-workspace-dark.png'),animations: 'disabled' });
  await page.evaluate(() => { document.documentElement.dataset.theme = 'light'; });
  await comparison.screenshot({ path: testInfo.outputPath('matte-workspace-light.png'),animations: 'disabled' });
  await page.locator('[data-test-workspace]').evaluate(element => { (element as HTMLElement).style.width = '420px'; });
  await expect(workspace.getByText('Holo research workspace',{ exact: true })).toBeVisible();
  await expect(comparison.locator('.card-finish-layer')).toHaveCount(0);
});


test('shared presets retain selective metal, chromatic film and sparse flake responses', async ({ page }, info) => {
  await page.setViewportSize({ width: 1680,height: 1050 });
  await page.goto('/?card-finishes');
  await page.getByLabel('Study lighting').selectOption('representative');
  await expect(page.locator('[data-material-renderer="webgl"]')).toHaveCount(4);
  await expect(page.locator('[data-preview-finish="normal"] canvas')).toHaveCount(0);
  const signatures = new Set<number>();
  for (const finish of ['foil','rainbow','starlight','laser']) {
    const card = page.locator('[data-preview-finish="'+finish+'"]');
    const sample = await materialPixels(card);
    expect(sample.textPeak).toBe(0); expect(sample.iconPeak).toBe(0);
    expect(sample.coverage).toBeGreaterThan(0); expect(sample.coverage).toBeLessThan(.72);
    if (finish === 'starlight') expect(sample.bright).toBeLessThan(.01);
    if (finish === 'foil') expect(sample.coverage).toBeLessThan(.06);
    if (finish === 'rainbow') expect(sample.chromatic).toBeGreaterThan(.25);
    signatures.add(sample.hash);
    await card.screenshot({ path: info.outputPath('shared-preset-'+finish+'.png') });
  }
  expect(signatures.size).toBe(4);
});

test('study sweep is opt-in, representative poses resist pointer input, and reduced motion stops the loop', async ({ page }) => {
  await page.addInitScript(() => {
    const request = window.requestAnimationFrame.bind(window);
    Object.assign(window,{ studyCallbacks: 0 });
    window.requestAnimationFrame = callback => request(time => {
      (window as unknown as { studyCallbacks: number }).studyCallbacks++; callback(time);
    });
  });
  await page.goto('/?card-finishes');
  const card = page.locator('[data-preview-finish="rainbow"]'), modes = page.getByLabel('Study lighting');
  await expect(card.locator('[data-material-ready]')).toHaveCount(1);
  const rest = await materialPixels(card);
  await modes.selectOption('representative');
  await expect(card).toHaveAttribute('data-study-angle','0.450,0.150');
  await expect(card.locator('[data-material-settled]')).toHaveCount(1);
  const representative = await materialPixels(card);
  await card.hover();
  await expect(card).not.toHaveAttribute('data-finish-active');
  expect((await materialPixels(card)).hash).toBe(representative.hash);
  await modes.selectOption('sweep');
  await expect.poll(async () => (await materialPixels(card)).hash).not.toBe(representative.hash);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect(page.getByRole('status')).toContainText('paused for reduced motion');
  await expect(card).toHaveAttribute('data-study-angle','0.450,0.150');
  const idle = await page.evaluate(async () => {
    await new Promise(resolve => setTimeout(resolve,100));
    const before = (window as unknown as { studyCallbacks: number }).studyCallbacks;
    await new Promise(resolve => setTimeout(resolve,180));
    return (window as unknown as { studyCallbacks: number }).studyCallbacks-before;
  });
  expect(idle).toBe(0);
  await modes.selectOption('pointer');
  await expect(card).not.toHaveAttribute('data-study-angle');
  await expect.poll(async () => (await materialPixels(card)).hash).toBe(rest.hash);
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await card.hover();
  await expect(card).toHaveAttribute('data-finish-active','true');
  await page.mouse.move(1,1);
  await expect.poll(async () => (await materialPixels(card)).hash).toBe(rest.hash);
});

test('directional diffraction fades outside its viewing window while shared clearcoat remains', async ({ page }) => {
  await page.goto('/?card-finishes');
  await page.getByLabel('Study lighting').selectOption('representative');
  const card = page.locator('[data-preview-finish="laser"]');
  await expect.poll(async () => (await materialPixels(card)).peak).toBeGreaterThan(16);
  const laserCoverage = (await materialPixels(card)).coverage;
  await card.evaluate(element => element.dispatchEvent(new CustomEvent('card-material-light',{ detail: { x: -.7,y: -.7,active: true } })));
  await expect.poll(async () => (await materialPixels(card)).coverage).toBeLessThan(laserCoverage * .5);
});

test('aurora colour travel is continuous, broad, reversible, and leaves top print pixel-stable', async ({ page }, info) => {
  await page.setViewportSize({ width: 1680, height: 1050 });
  await page.goto('/?card-finishes');
  const card = page.locator('[data-preview-finish="rainbow"]');
  await expect(card.locator('[data-material-ready]')).toHaveCount(1);
  await materialPixels(card);
  let printShot=0;
  const copyInk = async () => {
    const box = (await card.locator('.card-face-copy').boundingBox())!;
    return page.screenshot({path:info.outputPath("copy-"+(printShot++)+".png"),clip:{x:box.x+3,y:box.y+3,width:box.width-6,height:box.height-6}});
  };
  const copy = await copyInk();
  const badgeInk = async () => {
    const box = (await card.locator('.card-face-badge').boundingBox())!;
    // Rounded outer corners deliberately reveal the moving artwork around the label.
    return page.screenshot({ clip: { x: box.x+9, y: box.y+5, width: box.width-18, height: box.height-10 } });
  };
  const badge = await badgeInk();
  const icon = await card.locator('.card-face-symbol > svg').screenshot();
  const unchangedPrint = async (before: Buffer, after: Buffer) => {
    const delta = await page.evaluate(async urls => {
      const images = await Promise.all(urls.map(async url => {
        const image = new Image(); image.src = url; await image.decode();
        const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
        const context = canvas.getContext('2d')!; context.drawImage(image,0,0);
        return context.getImageData(0,0,canvas.width,canvas.height).data;
      }));
      if (images[0].length !== images[1].length) return Infinity;
      return images[0].reduce((maximum,value,index)=>Math.max(maximum,Math.abs(value-images[1][index])),0);
    }, [before,after].map(buffer => `data:image/png;base64,${buffer.toString('base64')}`));
    // Canvas readback plus page compositing can round 8-bit channels by two levels.
    expect(delta).toBeLessThanOrEqual(2);
  };
  const render = async (x: number, y: number) => {
    await card.evaluate((element, pose) => element.dispatchEvent(new CustomEvent('card-material-light',
      { detail: { ...pose, active: true, immediate: true } })), { x,y });
    await expect(card.locator('[data-material-settled]')).toHaveCount(1);
    const metrics = await materialPixels(card);
    expect(metrics.textPeak).toBe(0); expect(metrics.iconPeak).toBe(0);
    expect(metrics.chromatic).toBeGreaterThan(.25);
    return card.locator('canvas').evaluate(element => {
      const canvas = element as HTMLCanvasElement;
      return Array.from(canvas.getContext('2d')!.getImageData(0,0,canvas.width,canvas.height).data);
    });
  };
  const origin = await render(0,0), nearby = await render(.01,.01), turned = await render(.7,-.6);
  const distance = (a: number[], b: number[]) => {
    let sum = 0, count = 0, changed = 0;
    for (let i=0; i<a.length; i+=4) if (a[i+3] > 8 && b[i+3] > 8) {
      const diff = (Math.abs(a[i]-b[i])+Math.abs(a[i+1]-b[i+1])+Math.abs(a[i+2]-b[i+2]))/3;
      sum += diff; count++; if (diff > 25) changed++;
    }
    return { mean: sum/count, changed: changed/count };
  };
  const small = distance(origin,nearby), large = distance(origin,turned);
  expect(small.mean).toBeLessThan(6);
  expect(large.mean).toBeGreaterThan(20);
  expect(large.mean).toBeGreaterThan(small.mean * 8);
  expect(large.changed).toBeGreaterThan(.4);
  const copyAfter=await copyInk();
  await info.attach("top-print-before",{body:copy,contentType:"image/png"});
  await info.attach("top-print-after",{body:copyAfter,contentType:"image/png"});
  await unchangedPrint(copy,copyAfter);
  await unchangedPrint(badge,await badgeInk());
  await unchangedPrint(icon,await card.locator('.card-face-symbol > svg').screenshot());
  const restored = await render(0,0);
  expect(restored.reduce((maximum,value,index) => Math.max(maximum,Math.abs(value-origin[index])),0)).toBeLessThanOrEqual(1);
  await info.attach('angular-continuity', { body: JSON.stringify({ small, large }), contentType: 'application/json' });
});

test('layer comparison synchronizes the mask and composite, preserves masks after resize, and remains readable on mobile', async ({ page }, info) => {
  await page.setViewportSize({ width: 1500,height: 950 });
  await page.goto('/?card-finishes');
  await page.getByLabel('Layer comparison').check();
  await page.getByLabel('Study lighting').selectOption('representative');
  const laminate = page.locator('[data-study-layer="laminate"]');
  const composite = page.locator('[data-study-layer="composite"]');
  await expect(page.locator('[data-preview-finish]')).toHaveCount(3);
  expect((await materialPixels(laminate)).hash).toBe((await materialPixels(composite)).hash);
  await expect(laminate.locator('.card-face-copy')).toBeHidden();
  await expect(composite.locator('.card-face-copy')).toBeVisible();
  await page.screenshot({ path: info.outputPath('aurora-layer-comparison.png'), fullPage: true });
  await page.getByLabel('Study lighting').selectOption('pointer');
  await composite.hover({ position: { x: 250,y: 70 } });
  await expect(composite.locator('[data-material-settled]')).toHaveCount(1);
  await expect(laminate.locator('[data-material-settled]')).toHaveCount(1);
  expect((await materialPixels(laminate)).hash).toBe((await materialPixels(composite)).hash);
  await page.mouse.move(1,1);
  await page.getByLabel('Printed test pattern').uncheck();
  await page.setViewportSize({ width: 390,height: 844 });
  await composite.scrollIntoViewIfNeeded();
  await expect(composite.locator('[data-material-ready]')).toHaveCount(1);
  const sample = await materialPixels(composite);
  expect(sample.textPeak).toBe(0); expect(sample.iconPeak).toBe(0);
  expect(sample.chromatic).toBeGreaterThan(.2);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await composite.screenshot({ path: info.outputPath('aurora-mobile.png') });
});
