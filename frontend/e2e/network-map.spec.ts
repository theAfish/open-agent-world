import { expect, test } from '@playwright/test';
import { mapReady, clickMapNode } from './network-map-helpers';

test('one camera supports search, type filters, islands, focus history and repeated unmount', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await page.goto('/?network-map');
  const scope = page.locator('main');
  await mapReady(scope);
  const state = () => scope.locator('.network-map-stage').evaluate(el => (el as any).networkDiagnostics);
  const before = await state();
  // All semantic changes reuse exact coordinates; they never restart layout.
  for (let i = 0; i < 6; i++) await scope.locator('.network-map-scale button').nth(1).click();
  expect((await state()).worker).toBe(false);
  expect((await state()).positions.map(({ id, x, y }: any) => [id, x, y])).toEqual(before.positions.map(({ id, x, y }: any) => [id, x, y]));
  await scope.locator('.network-map-tools button').last().click();
  await scope.locator('.network-topics button').first().click();
  await page.waitForTimeout(350);
  expect((await state()).camera.ratio).toBeLessThan(before.camera.ratio);
  await scope.locator('.network-map-tools input').fill('Atomic structure 1');
  await scope.locator('.network-results button').first().click();
  await expect(scope.locator('footer')).toHaveText('node-0');
  await expect(scope.locator('.network-results')).toHaveCount(0);
  await expect(scope.locator('.network-map-tools button').first()).toBeEnabled();
  await scope.locator('.network-map-tools button').first().click();
  expect((await state()).worker).toBe(false);
  await scope.locator('.network-map-tools select').selectOption('memory');
  await scope.locator('.network-map-scale button').last().click();
  await page.setViewportSize({ width: 800, height: 700 });
  await expect(scope.locator('.network-map')).toBeVisible();
  for (let i = 0; i < 3; i++) {
    await page.getByRole('button', { name: 'Unmount map', exact: true }).click();
    await expect(scope.locator('canvas')).toHaveCount(0);
    await page.getByRole('button', { name: 'Mount map', exact: true }).click();
    await mapReady(scope);
  }
  expect(errors).toEqual([]);
});

test('Knowledge Base publishes real entities and opens the same shared renderer', async ({ page, request }) => {
  test.setTimeout(60000);
  const result = await request.post('/api/legions/presets/knowledge.base.research/instances', { data: {} });
  expect(result.ok(), await result.text()).toBe(true);
  const { node_ids: ids, nodes } = await result.json();
  const action = async (operation: string, args: Record<string, unknown> = {}, confirm = false) => {
    const response = await request.post(`/api/nodes/${ids.knowledge}/resource/${operation}`, { data: { arguments: args, confirm } });
    expect(response.ok(), await response.text()).toBe(true); return response.json();
  };
  try {
    const uploaded = await action('ingest', { filename: 'graph.md', media_type: 'text/markdown', content_base64: Buffer.from('# Si3N4\nSintering at 1750 C.').toString('base64') });
    await action('process', { source_ids: [uploaded.source.id] });
    await expect.poll(async () => (await action('sources')).sources[0]?.record_id, { timeout: 25000 }).toBeTruthy();
    const record = (await action('sources')).sources[0].record_id;
    const schema = (await action('schemas', { operation: 'create', name: 'Materials map', system_prompt: 'Extract entities and relations.', definition: { type: 'object', required: ['entities'], properties: { entities: { type: 'array', items: { type: 'object' } }, relations: { type: 'array', items: { type: 'object' } } } } })).schema;
    const projection = (await action('save_projection', { schema_id: schema.id, record_id: record, data: {
      entities: [{ type: 'material', name: 'Si3N4', form: 'powder' }, { type: 'process', name: 'Sintering', temperature_c: 1750 }],
      relations: [{ source: 'Si3N4', target: 'Sintering', type: 'processed_by' }],
    }, model: 'local-test' })).projection;
    const draft = (await action('draft', { operation: 'create', projection_ids: [projection.id] })).draft;
    await action('review', { operation: 'submit', draft_id: draft.id, expected_revision: 1 });
    await action('review', { operation: 'approve', draft_id: draft.id, expected_revision: 1 }, true);
    const original = await action('graph');
    expect(original.entities).toHaveLength(2); expect(original.relations).toHaveLength(1);
    await page.setViewportSize({ width: 1900, height: 1100 });
    const profile = await (await request.get('/api/application')).json();
    await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation, changes: { 'oaw.locale': 'en', 'oaw-canvas-viewport-v1': null } } });
    await page.goto('/');
    await page.getByRole('button', { name: 'Fit view', exact: true }).click();
    await page.locator(`[data-card-id="${ids.group}"]`).getByRole('button', { name: 'Workspace mode', exact: true }).click();
    const workspace = page.getByRole('dialog', { name: 'Knowledge research workspace mode' });
    await workspace.getByRole('tab', { name: 'Graph', exact: true }).click();
    await mapReady(workspace);
    await expect(workspace.locator('.network-map')).toHaveAttribute('data-node-count', '2');
    await clickMapNode(page, workspace, original.entities.find((e: any) => e.name === 'Si3N4').id);
    await expect(workspace.locator('.knowledge-graphside')).toContainText('powder');
    await workspace.locator('.network-map-caption button').click();
    await mapReady(workspace);
    expect((await action('graph')).relations).toEqual(original.relations);
    await page.waitForTimeout(350); // Capture after the camera transition, when Sigma restores labels/edges.
    // A two-node neighborhood can already be fitted. Even a camera animation
    // with identical endpoints must restore the labels after hide-on-move.
    await expect.poll(() => workspace.locator('canvas.sigma-labels').evaluate(canvas => {
      const el = canvas as HTMLCanvasElement;
      return el.getContext('2d')!.getImageData(0, 0, el.width, el.height).data.some((v, i) => i % 4 === 3 && v > 0);
    })).toBe(true);
    await workspace.screenshot({ path: '../.outputs/graph-audit/knowledge-base-published.png' });
  } finally { await request.post('/api/nodes/batch-delete', { data: { node_ids: nodes.map((n: any) => n.id) } }).catch(() => {}); }
});
