import { expect, test } from '@playwright/test';

test.use({ viewport: { width: 1920, height: 1080 } });

test('semantic zoom shows faithful mid views and restores small cards at ordinary distances', async ({ page, request }, testInfo) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  const profile = await (await request.get('/api/application')).json();
  const world = await (await request.get('/api/world')).json();
  await page.route('**/api/**', route => {
    const url = new URL(route.request().url());
    if (route.request().method() !== 'GET') return route.fulfill({ json: {} });
    if (url.pathname === '/api/application') return route.fulfill({ json: profile });
    if (url.pathname === '/api/world') return route.fulfill({ json: { ...world, nodes: [], edges: [] } });
    return route.continue();
  });
  await page.routeWebSocket('**/*', socket => socket.onMessage(() => {}));
  for (const zoom of [.2, .35]) {
    profile.values = { ...profile.values, 'oaw.locale': 'en', 'oaw-node-surfaces-v1': null,
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
      'oaw-canvas-viewport-v1': JSON.stringify({ version: 0, state: { viewport: { x: 850, y: 400, zoom, width: 1920, height: 1080 } } }),
    };
    await page.goto('/');
    await expect(page.locator('#oaw-world-map')).toBeVisible();
    await page.evaluate(async () => {
      const moduleUrl = (file: string) => performance.getEntriesByType('resource').find(e => e.name.includes(`/src/state/${file}.ts`))!.name;
      const { useWorldStore } = await import(/* @vite-ignore */ moduleUrl('worldStore'));
      const { useNodeSurfaceStore } = await import(/* @vite-ignore */ moduleUrl('nodeSurfaces'));
      useWorldStore.getState().generateStressWorld(4);
      const [agent, text, image, sandbox] = useWorldStore.getState().stressCards;
      useWorldStore.setState({ stressCards: [
        { ...agent, name: 'Research agent', position: { x: -900, y: -400 } },
        { ...text, name: 'Research notes', position: { x: -450, y: -400 }, config: { ...text.config, preview: 'Surface reconstruction\nCompare relaxed structures and retain the lowest energy candidates.', filename: 'research.md' } },
        { ...image, name: 'Result image', position: { x: 0, y: -400 } },
        { ...sandbox, name: 'Materials workspace', position: { x: -200, y: 700 }, config: { ...sandbox.config, workspace_path: '/workspace/materials', output: ['Relaxation complete.', '24 structures processed.'] } },
        { ...sandbox, id: 'stress-4', name: 'Large workspace', position: { x: 2200, y: 700 } },
      ] });
      useNodeSurfaceStore.setState((s: any) => ({ surfaceLevels: { ...s.surfaceLevels, 'stress-0': 'node' },
        surfaceSizes: { ...s.surfaceSizes, 'stress-4': { workspace: { width: 2000, height: 1000 } } } }));
    });
    const card = (id: number) => page.locator(`.world-card[data-card-id="stress-${id}"]`);
    for (const id of [0, 1, 2, 3]) await expect(card(id)).toHaveAttribute('data-render-lod', zoom === .2 ? 'mid' : 'full');
    await expect(card(4)).toHaveAttribute('data-render-lod', 'full');
    if (zoom === .2) {
      const image = card(3).locator('img[data-static-preview="sandbox"]');
      await expect(image).toBeVisible();
      await expect.poll(() => image.evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth > 0)).toBe(true);
      expect(decodeURIComponent((await image.getAttribute('src'))!)).toContain('24 structures processed.');
      await expect(card(3).locator('.sandbox-workspace, input, textarea, select')).toHaveCount(0);
      await expect(card(1)).toContainText('Surface reconstruction');
    }
    await page.screenshot({ path: testInfo.outputPath(`semantic-zoom-${zoom}.png`) });
    if (zoom === .2) {
      const header = (await card(3).locator('.node-drag-region').boundingBox())!;
      const before = (await card(3).boundingBox())!;
      await page.mouse.move(header.x + header.width / 2, header.y + header.height / 2);
      await page.mouse.down();
      await expect(card(3)).toHaveAttribute('data-render-lod', 'full');
      await page.mouse.move(header.x + header.width / 2 + 40, header.y + header.height / 2 + 20, { steps: 8 });
      await page.mouse.up();
      await expect.poll(async () => (await card(3).boundingBox())!.x - before.x).toBeGreaterThan(30);
      await expect(card(3)).toHaveAttribute('data-surface-level', 'workspace');
    }
  }
  expect(errors).toEqual([]);
});

test('1000-card overview hydrates interactions, preserves drafts and retains the safe region', async ({ page, request }) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  const profile = await (await request.get('/api/application')).json();
  const world = await (await request.get('/api/world')).json();
  profile.values = { ...profile.values, 'oaw.locale': 'en', 'oaw-node-surfaces-v1': null,
    'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
    'oaw-canvas-viewport-v1': JSON.stringify({ version: 0, state: { viewport: { x: 960, y: 540, zoom: .12, width: 1920, height: 1080 } } }),
  };
  await page.route('**/api/**', route => {
    const url = new URL(route.request().url());
    if (route.request().method() !== 'GET') return route.fulfill({ json: {} });
    if (url.pathname === '/api/application') return route.fulfill({ json: profile });
    if (url.pathname === '/api/world') return route.fulfill({ json: { ...world, nodes: [], edges: [] } });
    return route.continue();
  });
  await page.routeWebSocket('**/*', socket => socket.onMessage(() => {}));
  await page.goto('/');
  await expect(page.locator('#oaw-world-map')).toBeVisible();
  await page.evaluate(async () => {
    const moduleUrl = (file: string) => performance.getEntriesByType('resource').find(e => e.name.includes(`/src/state/${file}.ts`))!.name;
    const { useWorldStore } = await import(/* @vite-ignore */ moduleUrl('worldStore'));
    const { useNodeSurfaceStore } = await import(/* @vite-ignore */ moduleUrl('nodeSurfaces'));
    const state = window as any;
    state.lodWorld = useWorldStore; state.lodSurfaces = useNodeSurfaceStore;
    useWorldStore.getState().generateStressWorld(1000);
    useWorldStore.setState((s: any) => ({ stressCards: s.stressCards.map((c: any) => c.id === 'stress-3' ? { ...c, position: { x: 0, y: 0 } } : c) }));
  });
  await expect(page.locator('.card-lod-view').first()).toBeAttached();
  await expect(page.locator('.sandbox-workspace')).toHaveCount(0);
  const before = await page.evaluate(() => {
    const s = window as any;
    return { cards: s.lodWorld.getState().stressCards, levels: s.lodSurfaces.getState().surfaceLevels, sizes: s.lodSurfaces.getState().surfaceSizes };
  });
  expect(before.cards).toHaveLength(1000);
  expect(await page.locator('.card-lod-view').count()).toBeGreaterThan(100);
  const layers = await page.locator('.can-cache-card.card-view-mounted').evaluateAll(nodes => nodes
    .filter(node => Number(node.getAttribute('data-id')?.split('-')[1]) >= 512)
    .map(node => getComputedStyle(node).willChange));
  expect(layers.length).toBeGreaterThan(0);
  expect(layers.every(value => value.includes('transform'))).toBe(true);
  expect(await page.locator('.react-flow__node[data-id="stress-3"] > .card-render-boundary').evaluate(el => getComputedStyle(el).willChange)).toContain('transform');

  // Camera jitter remains inside the policy's safe region: no view churn.
  await page.waitForTimeout(300);
  await page.evaluate(() => {
    const s = window as any; s.lodMounts = 0;
    s.lodObserver = new MutationObserver(records => {
      for (const r of records) for (const n of [...r.addedNodes, ...r.removedNodes])
        if (n instanceof Element && (n.matches('.world-card') || n.querySelector('.world-card'))) s.lodMounts++;
    });
    s.lodObserver.observe(document.querySelector('.react-flow__nodes'), { childList: true, subtree: true });
  });
  await page.mouse.move(1400, 750); await page.mouse.down({ button: 'middle' });
  for (let i = 0; i < 8; i++) await page.mouse.move(1400 + (i % 2 ? 0 : 12), 750);
  await page.mouse.up({ button: 'middle' });
  expect(await page.evaluate(() => { const s = window as any; s.lodObserver.disconnect(); return s.lodMounts; })).toBe(0);

  await page.evaluate(() => (window as any).lodWorld.getState().selectCards(['stress-3'], { syncCanvas: true }));
  const card = page.locator('.world-card[data-card-id="stress-3"]');
  await expect(card).toHaveAttribute('data-render-lod', 'full');
  await expect(page.locator('.sandbox-workspace')).toHaveCount(1);
  expect(await card.locator('xpath=ancestor::*[contains(@class,"react-flow__node")][1]').evaluate(el => getComputedStyle(el).willChange)).not.toContain('transform');
  expect(await card.locator('..').evaluate(el => getComputedStyle(el).willChange)).not.toContain('transform');
  await expect(card.locator('.sandbox-settings-page')).toHaveCount(0);
  const command = card.getByRole('textbox', { name: 'Command' });
  await command.focus();
  // Synthetic commands are intentionally read-only; real draft editing/remount is covered by SandboxWorkspace.test.
  await page.evaluate(() => (window as any).lodSurfaces.getState().setDraft('sandbox:stress-3', 'echo hydration-draft'));
  await page.evaluate(() => (window as any).lodWorld.getState().selectCards([], { syncCanvas: true }));
  await expect(card).toHaveAttribute('data-render-lod', 'full');
  await expect(command).toBeFocused();
  await command.evaluate(el => (el as HTMLElement).blur());
  await expect(card).toHaveAttribute('data-render-lod', 'mid');
  await expect(page.locator('.sandbox-workspace')).toHaveCount(0);

  // Zoom through LOD thresholds without writing presentation/geometry.
  await page.locator('#oaw-world-map').dispatchEvent('wheel', { deltaY: -1500, clientX: 960, clientY: 540, bubbles: true, cancelable: true });
  await expect(card).toHaveAttribute('data-render-lod', 'full');
  await expect(command).toHaveValue('echo hydration-draft');
  const after = await page.evaluate(() => {
    const s = window as any;
    return { cards: s.lodWorld.getState().stressCards, levels: s.lodSurfaces.getState().surfaceLevels, sizes: s.lodSurfaces.getState().surfaceSizes };
  });
  expect(after).toEqual(before);

  // Hydrating on selection must not eat the original click or turn a drag into an open.
  await page.evaluate(() => {
    const s = window as any, text = s.lodWorld.getState().stressCards.find((c: any) => c.type === 'text');
    s.lodWorld.setState({ stressCards: [{ ...text, position: { x: 0, y: 0 } }] }); s.lodTextId = text.id;
  });
  await page.locator('#oaw-world-map').dispatchEvent('wheel', { deltaY: 1500, clientX: 960, clientY: 540, bubbles: true, cancelable: true });
  const textId = await page.evaluate(() => (window as any).lodTextId);
  const textCard = page.locator(`.world-card[data-card-id="${textId}"]`);
  await expect(textCard).toHaveAttribute('data-render-lod', /far|mid/);
  const clickBox = (await textCard.boundingBox())!;
  await page.mouse.move(clickBox.x + clickBox.width / 2, clickBox.y + clickBox.height / 2);
  await page.mouse.down();
  await expect(textCard).toHaveAttribute('data-render-lod', 'full');
  await page.mouse.up();
  await expect(textCard).toHaveAttribute('data-surface-level', 'inspector');
  await expect.poll(() => page.evaluate(id => (window as any).lodWorld.getState().selectedCardIds.includes(id), textId)).toBe(true);
  await page.evaluate(id => {
    const s = window as any; s.lodSurfaces.getState().closeInspector(id);
    s.lodWorld.getState().selectCards([], { syncCanvas: true });
    (document.activeElement as HTMLElement)?.blur();
  }, textId);
  await expect(textCard).toHaveAttribute('data-render-lod', /far|mid/);
  const box = (await textCard.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down(); await page.mouse.move(box.x + box.width / 2 + 35, box.y + box.height / 2 + 15, { steps: 8 }); await page.mouse.up();
  await expect(textCard).toHaveAttribute('data-surface-level', 'preview');
  await expect.poll(() => page.evaluate(() => (window as any).lodWorld.getState().stressCards[0].position.x)).toBeGreaterThan(100);
  await page.evaluate(() => {
    (window as any).lodWorld.getState().selectCards([], { syncCanvas: true });
    (document.activeElement as HTMLElement)?.blur();
  });
  await expect(textCard).toHaveAttribute('data-render-lod', /far|mid/);
  // A modified click keeps native selection but must not open the inspector.
  await textCard.click({ modifiers: ['Control'] });
  await expect(textCard).toHaveAttribute('data-surface-level', 'preview');
  await expect.poll(() => page.evaluate(id => (window as any).lodWorld.getState().selectedCardIds.includes(id), textId)).toBe(true);
  await page.evaluate(() => {
    (window as any).lodWorld.getState().selectCards([], { syncCanvas: true });
    (document.activeElement as HTMLElement)?.blur();
  });
  await expect(textCard).toHaveAttribute('data-render-lod', /far|mid/);
  const cancelBox = (await textCard.boundingBox())!;
  await page.mouse.move(cancelBox.x + cancelBox.width / 2, cancelBox.y + cancelBox.height / 2);
  await page.mouse.down();
  await expect(textCard).toHaveAttribute('data-render-lod', 'full');
  await page.locator('.card-render-boundary').dispatchEvent('pointercancel', { pointerId: 1, pointerType: 'mouse', isPrimary: true });
  await page.mouse.up();
  await expect(textCard).toHaveAttribute('data-surface-level', 'preview');
  expect(errors).toEqual([]);
});

test('lightweight cards keep connection targets and offscreen edge geometry', async ({ page, request }) => {
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation,
    changes: { 'oaw.locale': 'en', 'oaw-node-surfaces-v1': null,
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
      'oaw-canvas-viewport-v1': JSON.stringify({ version: 0, state: { viewport: { x: 600, y: 400, zoom: .12, width: 1920, height: 1080 } } }),
    } } });
  const create = async (name: string, x: number) => {
    const response = await request.post('/api/nodes', { data: { type: 'agent', name, position: { x, y: 0 }, size: { width: 96, height: 96 } } });
    expect(response.ok()).toBe(true); return response.json();
  };
  const a = await create('LOD source', 0), b = await create('LOD target', 3000);
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  try {
    await page.goto('/');
    const source = page.locator(`.world-card[data-card-id="${a.id}"]`), target = page.locator(`.world-card[data-card-id="${b.id}"]`);
    await expect(source).toHaveAttribute('data-render-lod', 'far');
    const start = (await source.locator('[data-connection-side="right"]').boundingBox())!;
    const end = (await target.boundingBox())!;
    await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2); await page.mouse.down();
    await page.mouse.move(end.x + end.width / 2, end.y + end.height / 2, { steps: 10 });
    await expect(target.locator('.connection-drop-surface')).toHaveClass(/valid/);
    await page.mouse.up();
    await page.getByRole('dialog', { name: 'Choose a capability' }).getByRole('button', { name: 'Grant capability' }).click();
    const edge = page.locator(`path.semantic-edge-path[data-source-id="${a.id}"][data-target-id="${b.id}"]`);
    await expect(edge).toHaveCount(1);
    // Move the target well beyond the virtualized view, retaining its model and edge.
    await page.evaluate(async id => {
      const url = performance.getEntriesByType('resource').find(e => /\/src\/state\/worldStore\.ts(?:\?|$)/.test(e.name))!.name;
      const { useWorldStore } = await import(/* @vite-ignore */ url);
      await useWorldStore.getState().updateCardPositions([{ id, position: { x: 30000, y: 0 } }]);
      useWorldStore.getState().selectCards([], { syncCanvas: true }); (document.activeElement as HTMLElement)?.blur();
    }, b.id);
    await expect(target).toHaveCount(0);
    await expect(edge).toHaveAttribute('d', /^M/);
    expect(await edge.getAttribute('d')).not.toMatch(/NaN|undefined/);
    expect(errors).toEqual([]);
  } finally {
    await page.close();
    await request.delete(`/api/nodes/${a.id}`); await request.delete(`/api/nodes/${b.id}`);
    const current = await (await request.get('/api/application')).json();
    await request.patch('/api/application/preferences', { data: { profile_id: current.profile_id, generation: current.generation,
      changes: Object.fromEntries(['oaw.locale', 'oaw-node-surfaces-v1', 'oaw-onboarding-v1', 'oaw-canvas-viewport-v1'].map(key => [key, profile.values[key] ?? null])) } });
  }
});
