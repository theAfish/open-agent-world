import { expect, test, type Response } from '@playwright/test';

test.beforeEach(async ({ page, request }) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw.locale': 'en', 'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
      'oaw-canvas-viewport-v1': null, 'oaw-node-surfaces-v1': null,
    },
  } })).ok()).toBe(true);
  let library = await (await request.get('/api/card-library')).json();
  for (const id of library.available_pack_ids) {
    const response = await request.post('/api/card-library/actions', { data: { action: 'open_pack', id, expected_revision: library.revision } });
    expect(response.ok()).toBe(true);
    library = await response.json();
  }
});

test('saved MatCreator drop shows feedback before the response and preserves all Skill documents', async ({ page, request }) => {
  test.setTimeout(60_000);
  const ids: string[] = [];
  let savedId: string | undefined;
  let deployed: Promise<Response> | undefined;
  let release!: () => void;
  const responseGate = new Promise<void>(resolve => { release = resolve; });
  try {
    const seedResponse = await request.post('/api/legions/presets/matcreator.research/instances', { data: { position: { x: 100, y: 100 } } });
    expect(seedResponse.ok()).toBe(true);
    // Preset responses contain blueprint nodes; collection-generated Skills are
    // present in the world too and must be included in the round-trip check.
    const seedNodes = await (await request.get('/api/nodes')).json();
    ids.push(...seedNodes.map((n: { id: string }) => n.id));
    const savedResponse = await request.post('/api/legions', { data: { name: 'Saved MatCreator', node_ids: ids } });
    expect(savedResponse.ok()).toBe(true);
    const saved = await savedResponse.json(); savedId = saved.id;
    const library = await (await request.get('/api/card-library')).json();
    expect((await request.post('/api/card-library/actions', { data: {
      action: 'update_deck', id: library.active_deck_id, entries: [{ kind: 'legion', id: saved.id }], expected_revision: library.revision,
    } })).ok()).toBe(true);
    await page.goto('/');
    await page.getByRole('complementary', { name: 'Active card deck' }).hover();
    const source = page.locator(`[data-palette-card="${saved.id}"]`);
    await source.hover();
    await page.waitForTimeout(600); // Let the deck hover animation settle before pointer capture.
    await page.route(`**/api/legions/${saved.id}/instances`, async route => {
      await responseGate;
      await route.continue();
    });
    const box = (await source.boundingBox())!;
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(1100, 480, { steps: 12 });
    await expect(page.locator('.palette-drag-preview')).toBeVisible();
    deployed = page.waitForResponse(r => r.url().endsWith(`/legions/${saved.id}/instances`) && r.request().method() === 'POST');
    await page.mouse.up();
    const marker = page.locator('.legion-deployment');
    await expect(marker).toHaveText('Deploying Saved MatCreator…');
    const markerBox = (await marker.boundingBox())!;
    expect(markerBox.x + markerBox.width / 2).toBeCloseTo(1100, 0);
    expect(markerBox.y + markerBox.height / 2).toBeCloseTo(480, 0);
    expect((await (await request.get('/api/nodes')).json()).length).toBe(seedNodes.length);
    await page.screenshot({ path: 'test-results/legion-deploying.png' });
    release();
    const instance = await (await deployed).json();
    ids.push(...instance.nodes.map((n: { id: string }) => n.id));
    expect(instance.nodes).toHaveLength(seedNodes.length);
    await expect(marker).toHaveCount(0);
    const root = instance.nodes.find((n: { type: string }) => n.type === 'legion');
    await expect(page.locator(`[data-card-id="${root.id}"]`).first()).toBeVisible();
    const document = async (id: string) => (await (await request.get(`/api/nodes/${id}/document`)).json()).value;
    const beforeSkills = seedNodes.filter((n: { type: string }) => n.type.endsWith('.skill'));
    expect(beforeSkills.length).toBeGreaterThan(0);
    for (const original of beforeSkills) {
      const copied = instance.nodes.find((n: { type: string; name: string }) => n.type === original.type && n.name === original.name);
      expect(copied).toBeTruthy();
      expect(await document(copied.id)).toEqual(await document(original.id));
    }
    await page.keyboard.press('Control+z');
    await expect(page.locator(`[data-card-id="${root.id}"]`)).toHaveCount(0);
    await expect.poll(async () => (await (await request.get('/api/nodes')).json()).length).toBe(seedNodes.length);
  } finally {
    release();
    await deployed?.catch(() => undefined);
    // The isolated E2E profile may also contain a redone/deferred instance on failure.
    const nodes = await (await request.get('/api/nodes')).json();
    await request.post('/api/nodes/batch-delete', { data: { node_ids: nodes.map((n: { id: string }) => n.id) } });
    if (savedId) await request.delete(`/api/legions/${savedId}`);
  }
});

test('overview caches bounded cards while panning and releases layers when zoomed in', async ({ page, request }) => {
  const nodes = [];
  try {
    for (let i = 0; i < 25; i++) {
      const response = await request.post('/api/nodes', { data: {
        type: 'text', name: `Cache ${i}`, position: { x: 100 + i % 5 * 300, y: 100 + Math.floor(i / 5) * 350 },
      } });
      expect(response.ok()).toBe(true); nodes.push(await response.json());
    }
    await page.goto('/');
    await page.getByRole('button', { name: 'Fit view', exact: true }).click();
    const flow = page.locator('#oaw-world-map');
    await expect(flow).toHaveAttribute('data-card-layers', 'true');
    const cached = flow.locator('.react-flow__node-worldCard.can-cache-card').first();
    await expect(cached).toHaveCSS('will-change', 'transform');
    const viewport = page.locator('#oaw-world-map > .react-flow__renderer > .react-flow__pane > .react-flow__viewport');
    const before = await viewport.getAttribute('style');
    await page.mouse.move(1450, 600);
    await page.mouse.down({ button: 'middle' });
    await page.mouse.move(1400, 550, { steps: 12 });
    await page.mouse.up({ button: 'middle' });
    await expect(viewport).not.toHaveAttribute('style', before!);
    await expect(cached).toHaveCSS('will-change', 'transform');
    for (let i = 0; i < 5; i++) await page.getByRole('button', { name: 'Zoom in', exact: true }).click();
    await expect(flow).not.toHaveAttribute('data-card-layers', 'true');
    await expect(flow.locator('.react-flow__node-worldCard.can-cache-card').first()).toHaveCSS('will-change', 'auto');
    const current = await (await request.get('/api/nodes')).json();
    expect(current.map((n: { id: string; position: unknown }) => [n.id, n.position])).toEqual(nodes.map(n => [n.id, n.position]));
  } finally {
    await request.post('/api/nodes/batch-delete', { data: { node_ids: nodes.map(n => n.id) } });
  }
});
