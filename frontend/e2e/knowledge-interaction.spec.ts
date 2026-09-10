import { expect, test, type APIRequestContext } from '@playwright/test';

async function createGraph(request: APIRequestContext) {
  const graph = await (await request.post('/api/nodes', { data: { type: 'matcreator.kdg', name: 'Interaction graph', position: { x: 200, y: 130 } } })).json();
  let revision = (await (await request.get(`/api/nodes/${graph.id}/document`)).json()).revision;
  const ids: string[] = [];
  for (const type of ['capability', 'procedure', 'heuristic', 'memory']) {
    const response = await request.post(`/api/nodes/${graph.id}/actions/edit`, { data: { expected_revision: revision, arguments: { title: `${type} example`, type, content: 'Interaction check', summary: 'A connected example' } } });
    expect(response.status()).toBe(200);
    revision = (await response.json()).revision;
    const document = (await (await request.get(`/api/nodes/${graph.id}/document`)).json()).value;
    ids.push(document.entries.find((entry: { title: string }) => entry.title === `${type} example`).id);
  }
  for (const [source, target, relation] of [[ids[0], ids[1], 'related_workflow'], [ids[2], ids[1], 'heuristic_for']]) {
    const response = await request.post(`/api/nodes/${graph.id}/actions/connect`, { data: { expected_revision: revision, arguments: { source, target, relation } } });
    expect(response.status()).toBe(200);
    revision = (await response.json()).revision;
  }
  return { ...graph, entryIds: ids };
}

for (const zoom of [0.65, 1.35]) test(`knowledge pan, node drag, zoom and selection follow the pointer at world zoom ${zoom}`, async ({ page, request }) => {
  await page.setViewportSize({ width: 2200, height: 1400 });
  await page.addInitScript(zoom => localStorage.setItem('oaw-canvas-viewport-v1', JSON.stringify({ state: { viewport: { x: 30, y: 30, zoom, width: 2200, height: 1400 } }, version: 0 })), zoom);
  const graph = await createGraph(request);
  try {
    await page.goto('/');
    const container = page.locator(`[data-card-id="${graph.id}"]`);
    await expect(container.getByRole('button', { name: 'Open workspace' })).toHaveCount(0);
    const canvas = container.locator('.kdg-canvas');
    const node = canvas.locator(`.kdg-node[data-id="${graph.entryIds[0]}"]`);
    await expect(node).toBeVisible();
    await page.waitForTimeout(250);
    const outer = page.locator('#oaw-world-map .react-flow__viewport').first();
    const worldTransform = await outer.getAttribute('style');
    const frame = (await container.boundingBox())!;
    const box = (await canvas.boundingBox())!;
    const before = (await node.boundingBox())!;
    await page.mouse.move(box.x + 20, box.y + 25);
    await page.mouse.down();
    await page.mouse.move(box.x + 90, box.y + 60, { steps: 10 });
    await page.mouse.up();
    await expect.poll(async () => (await node.boundingBox())!.x - before.x).toBeCloseTo(70, 0);
    expect((await node.boundingBox())!.y - before.y).toBeCloseTo(35, 0);
    const panned = (await node.boundingBox())!;
    await page.mouse.move(box.x + 20, box.y + 25);
    await page.mouse.down({ button: 'middle' });
    await page.mouse.move(box.x + 65, box.y + 45, { steps: 8 });
    await page.mouse.up({ button: 'middle' });
    await expect.poll(async () => (await node.boundingBox())!.x - panned.x).toBeCloseTo(45, 0);
    const start = (await node.boundingBox())!;
    await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2);
    await page.mouse.down();
    await page.mouse.move(start.x + start.width / 2 + 55, start.y + start.height / 2 + 30, { steps: 10 });
    await page.mouse.up();
    await expect.poll(async () => (await node.boundingBox())!.x - start.x).toBeCloseTo(55, 0);
    expect((await node.boundingBox())!.y - start.y).toBeCloseTo(30, 0);
    expect(await outer.getAttribute('style')).toBe(worldTransform);
    expect((await container.boundingBox())!.x).toBeCloseTo(frame.x, 0);
    await canvas.getByRole('button', { name: 'Fit View', exact: true }).click();
    await page.waitForTimeout(250);
    const anchor = (await node.boundingBox())!;
    const pointer = { x: anchor.x + anchor.width / 2, y: anchor.y + anchor.height / 2 };
    const fittedCanvas = (await canvas.boundingBox())!;
    expect(pointer.y).toBeGreaterThan(fittedCanvas.y);
    expect(pointer.y).toBeLessThan(fittedCanvas.y + fittedCanvas.height);
    await page.mouse.move(pointer.x, pointer.y);
    await page.mouse.wheel(0, -100);
    await expect.poll(async () => (await node.boundingBox())!.width).toBeGreaterThan(anchor.width);
    const zoomed = (await node.boundingBox())!;
    expect(zoomed.x + zoomed.width / 2).toBeCloseTo(pointer.x, 0);
    expect(zoomed.y + zoomed.height / 2).toBeCloseTo(pointer.y, 0);
    expect(await outer.getAttribute('style')).toBe(worldTransform);
    await page.keyboard.down('Shift');
    await page.mouse.move(zoomed.x - 8, zoomed.y - 8);
    await page.mouse.down();
    await page.mouse.move(zoomed.x + zoomed.width + 8, zoomed.y + zoomed.height + 8, { steps: 8 });
    await page.mouse.up();
    await page.keyboard.up('Shift');
    await expect(node).toHaveClass(/selected/);
    await expect(container.getByRole('button', { name: 'Focus selection' })).toBeVisible();
    for (const theme of ['light', 'dark']) {
      await page.evaluate(theme => document.documentElement.dataset.theme = theme, theme);
      const colors = await canvas.locator('.kdg-node').evaluateAll(nodes => nodes.map(node => getComputedStyle(node).backgroundColor));
      expect(new Set(colors).size).toBe(4);
      await page.screenshot({ path: `../.outputs/knowledge-interaction-${zoom}-${theme}.png` });
    }
    await node.click();
    await expect(container.locator('.kdg-inspector h3')).toHaveText('capability example');
  } finally { await request.delete(`/api/nodes/${graph.id}`); }
});

test('container members reflow during resize and persist through reload and undo', async ({ page, request }) => {
  await page.setViewportSize({ width: 2200, height: 1400 });
  const parent = await (await request.post('/api/nodes', { data: { type: 'matcreator.core', name: 'Resize tools', position: { x: 300, y: 130 }, size: { width: 1450, height: 740 } } })).json();
  const members = (await (await request.get('/api/world')).json()).nodes.filter((node: { parent_id: string }) => node.parent_id === parent.id) as { id: string }[];
  for (const [i, member] of members.entries()) {
    const response = await request.patch(`/api/nodes/${member.id}`, { data: { position: { x: 430 + i % 4 * 310, y: 290 + Math.floor(i / 4) * 190 } } });
    expect(response.status()).toBe(200);
  }
  try {
    await page.goto('/');
    const frame = page.locator(`[data-card-id="${parent.id}"]`);
    await frame.locator('.container-header strong').click();
    const grip = frame.locator('.container-resize-arc');
    await expect(grip).toBeVisible();
    const start = (await grip.boundingBox())!;
    await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2);
    await page.mouse.down();
    await page.mouse.move(start.x + start.width / 2 - 490, start.y + start.height / 2 - 130, { steps: 16 });
    const inside = async () => {
      const outer = (await frame.boundingBox())!;
      const boxes = await Promise.all(members.map(m => page.locator(`[data-card-id="${m.id}"]`).boundingBox()));
      for (const b of boxes) {
        expect(b).not.toBeNull();
        expect(b!.x).toBeGreaterThanOrEqual(outer.x);
        expect(b!.y).toBeGreaterThan(outer.y + 80);
        expect(b!.x + b!.width).toBeLessThanOrEqual(outer.x + outer.width + 1);
        expect(b!.y + b!.height).toBeLessThanOrEqual(outer.y + outer.height + 1);
      }
      for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i]!, b = boxes[j]!;
        expect(a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y).toBe(true);
      }
    };
    await inside();
    await page.mouse.up();
    await expect.poll(async () => (await (await request.get(`/api/nodes/${parent.id}`)).json()).size.width).toBeLessThan(1100);
    await inside();
    const saved = (await (await request.get(`/api/nodes/${parent.id}`)).json()).size;
    await page.reload();
    await expect(frame).toBeVisible();
    await inside();
    // History is session-local: resize again to verify a single undo restores frame and members.
    await frame.locator('.container-header strong').click();
    const next = (await grip.boundingBox())!;
    const previousPositions = await Promise.all(members.map(async m => (await (await request.get(`/api/nodes/${m.id}`)).json()).position));
    await page.mouse.move(next.x + 10, next.y + 10);
    await page.mouse.down();
    await page.mouse.move(next.x + 400, next.y + 10, { steps: 12 });
    await page.mouse.up();
    await expect.poll(async () => (await (await request.get(`/api/nodes/${parent.id}`)).json()).size.width).toBeGreaterThan(saved.width + 200);
    await page.keyboard.press('Control+z');
    await expect.poll(async () => (await (await request.get(`/api/nodes/${parent.id}`)).json()).size).toEqual(saved);
    await expect.poll(async () => (await frame.boundingBox())!.width).toBeCloseTo(saved.width, 0);
    for (let i = 0; i < members.length; i++) expect((await (await request.get(`/api/nodes/${members[i].id}`)).json()).position).toEqual(previousPositions[i]);
    await inside();
    await page.screenshot({ path: '../.outputs/container-resize-reflow.png' });
  } finally {
    const removed = await request.post('/api/nodes/batch-delete', { data: { node_ids: [parent.id, ...members.map(member => member.id)] } });
    expect(removed.status()).toBe(200);
  }
});

test('graph opens directly and edits, previews and deletes imported knowledge in its inspector', async ({ page, request }) => {
  await page.setViewportSize({ width: 1700, height: 1100 });
  const graph = await (await request.post('/api/nodes', { data: { type: 'matcreator.kdg', name: 'Inline knowledge', position: { x: 180, y: 100 } } })).json();
  const doc = await (await request.get(`/api/nodes/${graph.id}/document`)).json();
  expect((await request.post(`/api/nodes/${graph.id}/transformations/assimilate`, { data: { source_type: 'matcreator.core', expected_revision: doc.revision, confirm: true } })).status()).toBe(200);
  const original = (await (await request.get(`/api/nodes/${graph.id}/document`)).json()).value;
  const entry = original.entries.find((item: { title: string }) => item.title === 'Local structure demo');
  const world = (await (await request.get('/api/world')).json()).nodes;
  const members = world.filter((item: { parent_id: string }) => item.parent_id === graph.id);
  try {
    await page.goto('/');
    const container = page.locator(`[data-card-id="${graph.id}"]`);
    const workspace = container.getByRole('region', { name: 'Inline knowledge workspace', exact: true });
    await expect(workspace).toBeVisible();
    await expect(container.getByRole('button', { name: /Open workspace|Show member cards/ })).toHaveCount(0);
    for (const member of members) await expect(page.locator(`[data-card-id="${member.id}"]`)).toHaveCount(0);
    await workspace.getByLabel('Search knowledge').fill('Local structure demo');
    await workspace.getByRole('button', { name: 'Search', exact: true }).click();
    const node = workspace.locator(`.kdg-node[data-id="${entry.id}"]`);
    await node.click();
    const inspector = workspace.getByLabel('Knowledge details');
    await expect(inspector.getByRole('button', { name: 'Edit entry', exact: true })).toBeVisible();
    await inspector.getByText('SKILL.md', { exact: true }).click();
    await inspector.getByRole('button', { name: 'scripts/build_structure.py', exact: true }).click();
    await expect(inspector.getByRole('region', { name: 'Resource preview' })).toContainText('ase');
    await inspector.getByRole('button', { name: 'Edit entry', exact: true }).click();
    await inspector.getByLabel('Title', { exact: true }).fill('Cancelled draft');
    await inspector.getByRole('button', { name: 'Cancel editing' }).click();
    await expect(inspector.locator('h3')).toHaveText('Local structure demo');
    await inspector.getByRole('button', { name: 'Edit entry', exact: true }).click();
    await inspector.getByLabel('Title', { exact: true }).fill('Local structure demo — reviewed');
    await inspector.getByLabel('Content', { exact: true }).fill('Checked locally. Preserve the imported Skill resource.');
    await inspector.getByRole('button', { name: 'Save changes', exact: true }).click();
    await expect(inspector.locator('h3')).toHaveText('Local structure demo — reviewed');
    await expect(node).toContainText('reviewed');
    const changed = (await (await request.get(`/api/nodes/${graph.id}/document`)).json()).value;
    expect(changed.entries.find((item: { id: string }) => item.id === entry.id).owner).toBe('user');
    expect(changed.snapshots).toEqual(original.snapshots);
    await inspector.getByRole('button', { name: 'Delete entry', exact: true }).click();
    await inspector.getByRole('dialog', { name: 'Delete knowledge entry' }).getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(node).toBeVisible();
    await inspector.getByRole('button', { name: 'Delete entry', exact: true }).click();
    await inspector.getByRole('button', { name: 'Confirm delete', exact: true }).click();
    await expect(node).toHaveCount(0);
    const after = (await (await request.get(`/api/nodes/${graph.id}/document`)).json()).value;
    expect(after.entries.some((item: { id: string }) => item.id === entry.id)).toBe(false);
    expect(after.snapshots).toEqual(original.snapshots);
    expect(after.skills).toEqual(original.skills);
    await page.reload();
    await expect(workspace).toBeVisible();
    await expect(workspace.locator(`.kdg-node[data-id="${entry.id}"]`)).toHaveCount(0);
    await workspace.locator('.kdg-node').first().click();
    await page.screenshot({ path: '../.outputs/knowledge-inline-details.png' });
  } finally {
    await request.post('/api/nodes/batch-delete', { data: { node_ids: [graph.id, ...members.map((member: { id: string }) => member.id)] } });
  }
});

test('large graph keeps resource cards out of the canvas and pages knowledge on demand', async ({ page, request }) => {
  await page.setViewportSize({ width: 1700, height: 1100 });
  const graph = await (await request.post('/api/nodes', { data: { type: 'matcreator.kdg', name: 'Large knowledge', position: { x: 150, y: 100 } } })).json();
  const doc = await (await request.get(`/api/nodes/${graph.id}/document`)).json();
  const entries = Array.from({ length: 130 }, (_, i) => ({ id: `entry-${i}`, title: `Entry ${String(i).padStart(3, '0')}`, resources: [{ skill_id: `skill-${i}`, path: 'SKILL.md' }] }));
  const skills = entries.map((entry, i) => ({ id: `skill-${i}`, name: entry.title, description: 'Resource', instructions: 'Inspect through the graph', files: {} }));
  const saved = await request.post(`/api/nodes/${graph.id}/actions/replace`, { data: { expected_revision: doc.revision, arguments: { entries, skills } } });
  expect(saved.status()).toBe(200);
  const members = (await (await request.get('/api/world')).json()).nodes.filter((node: { parent_id: string }) => node.parent_id === graph.id) as { id: string }[];
  expect(members.length).toBe(130);
  try {
    await page.goto('/');
    const container = page.locator(`[data-card-id="${graph.id}"]`);
    await expect(container.locator('.kdg-count')).toHaveText('100 entries');
    await expect(page.locator('#oaw-world-map .world-card')).toHaveCount(0);
    expect((await container.boundingBox())!.width).toBeCloseTo(1000, 0);
    expect((await container.boundingBox())!.height).toBeCloseTo(650, 0);
    await container.getByRole('button', { name: 'Toggle navigator' }).click();
    await container.getByRole('button', { name: 'Load more' }).click();
    await expect(container.locator('.kdg-count')).toHaveText('130 entries');
    await container.getByLabel('Search knowledge').fill('Entry 129');
    await container.getByRole('button', { name: 'Search', exact: true }).click();
    const node = container.locator('.kdg-node[data-id="entry-129"]');
    await node.click();
    await expect(container.locator('.kdg-inspector h3')).toHaveText('Entry 129');
    await container.locator('.container-header strong').click();
    const grip = container.locator('.container-resize-arc');
    const bounds = (await grip.boundingBox())!;
    await page.mouse.move(bounds.x + 10, bounds.y + 10);
    await page.mouse.down();
    await page.mouse.move(bounds.x - 80, bounds.y - 50, { steps: 8 });
    await page.mouse.up();
    await expect.poll(async () => (await (await request.get(`/api/nodes/${graph.id}`)).json()).size.height).toBeLessThan(650);
    await expect(page.locator('#oaw-world-map .world-card')).toHaveCount(0);
  } finally {
    await page.close();
    const latest = (await (await request.get(`/api/nodes/${graph.id}/document`)).json()).revision;
    const cleared = await request.post(`/api/nodes/${graph.id}/actions/replace`, { data: { expected_revision: latest, arguments: { entries: [], skills: [] } } });
    expect(cleared.status()).toBe(200);
    // Removing document membership preserves the resources; clean the fixture up too.
    for (let offset = 0; offset < members.length; offset += 100) {
      const removed = await request.post('/api/nodes/batch-delete', { data: { node_ids: members.slice(offset, offset + 100).map(member => member.id) } });
      expect(removed.status()).toBe(200);
    }
    expect((await request.delete(`/api/nodes/${graph.id}`)).status()).toBe(200);
  }
});
