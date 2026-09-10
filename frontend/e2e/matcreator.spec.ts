import { expect, test } from "@playwright/test";

test('knowledge map has quiet curves and contextual neighborhood emphasis', async ({ page, request }) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.addInitScript(() => localStorage.setItem('oaw-theme', 'dark'));
  const graph = await (await request.post('/api/nodes', { data: { type: 'matcreator.kdg', name: 'Demo knowledge', position: { x: 150, y: 100 } } })).json();
  const doc = await (await request.get(`/api/nodes/${graph.id}/document`)).json();
  const imported = await request.post(`/api/nodes/${graph.id}/transformations/assimilate`, { data: { source_type: 'matcreator.core', expected_revision: doc.revision, confirm: true } });
  expect(imported.status()).toBe(200);
  await page.goto('/');
  const map = page.getByRole('region', { name: 'Demo knowledge workspace', exact: true });
  await expect(map.locator('.kdg-node').first()).toBeVisible();
  await expect(map.locator('.kdg-navigator')).toHaveCount(0);
  await expect(map.getByRole('button', { name: 'Focus selection' })).toHaveCount(0);
  await expect(map.locator('.react-flow__edge-text')).toHaveCount(0);
  const path = map.locator('.react-flow__edge-path').first();
  await expect(path).toHaveAttribute('d', / C .* C /);
  expect(await path.evaluate(el => Number(getComputedStyle(el).opacity))).toBeLessThan(.4);
  const edge = await (await request.post(`/api/nodes/${graph.id}/actions/search`, { data: { arguments: { limit: 100 } } })).json();
  const source = edge.value.edges[0].source;
  // The initial fit animates the parent viewport for 150ms after nodes mount.
  await page.waitForTimeout(250);
  await map.locator(`.kdg-node[data-id="${source}"]`).hover();
  await expect(map.locator('.kdg-node.is-muted').first()).toBeAttached();
  await expect(map.locator('.react-flow__edge.is-related').first()).toBeAttached();
  await map.getByRole('button', { name: 'Toggle navigator' }).hover();
  const midpoint = await path.evaluate(el => {
    const path = el as SVGPathElement;
    const point = path.getPointAtLength(path.getTotalLength() / 2);
    return new DOMPoint(point.x, point.y).matrixTransform(path.getScreenCTM()!).toJSON();
  });
  await page.mouse.move(midpoint.x, midpoint.y);
  await expect(map.locator('.react-flow__edge-text').first()).toBeVisible();
  await page.mouse.click(midpoint.x, midpoint.y);
  await map.getByRole('button', { name: 'Toggle navigator' }).hover();
  await expect(map.locator('.react-flow__edge-text').first()).toBeVisible();
  await page.screenshot({ path: '../.outputs/matcreator-native-map-dark.png' });
});

test.beforeEach(async ({ request }) => {
  const world = await (await request.get('/api/world')).json();
  const roots = new Set<string>(world.nodes.filter((node: { name: string }) => ['Demo knowledge', 'Demo Materials Core', 'Materials Core', 'Drop source', 'Drop destination'].includes(node.name)).map((node: { id: string }) => node.id));
  const owned = new Set(roots);
  let changed = true;
  while (changed) { changed = false; for (const node of world.nodes) if (owned.has(node.parent_id) && !owned.has(node.id)) { owned.add(node.id); changed = true; } }
  for (const node of [...world.nodes].reverse()) if (owned.has(node.id) && !roots.has(node.id)) await request.delete(`/api/nodes/${node.id}`);
  for (const id of roots) await request.delete(`/api/nodes/${id}`);
});

test("KDG workspace assimilates, selects, filters and expands real knowledge", async ({ page, request }) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  const graphResponse = await request.post("/api/nodes", { data: { type: "matcreator.kdg", name: "Demo knowledge", position: { x: 200, y: 100 } } });
  expect(graphResponse.status()).toBe(201);
  const graph = await graphResponse.json();
  const source = await (await request.post("/api/nodes", { data: { type: "matcreator.core", name: "Demo Materials Core", position: { x: 1400, y: 100 } } })).json();
  await page.goto("/");
  const workspace = page.getByRole("region", { name: "Demo knowledge workspace", exact: true });
  await workspace.getByRole('button', { name: 'Toggle navigator' }).click();
  await workspace.getByText("Assimilate a Toolset", { exact: true }).click();
  await workspace.getByRole("button", { name: "Choose Toolset", exact: true }).click();
  await workspace.getByLabel("Toolset to assimilate").selectOption(source.id);
  await workspace.getByRole("button", { name: "Preview assimilation" }).click();
  await expect(workspace.getByRole("dialog", { name: "Confirm assimilation" })).toBeVisible();
  expect((await request.get(`/api/nodes/${source.id}/document`)).status()).toBe(200);
  await workspace.getByRole("button", { name: "Confirm assimilation", exact: true }).click();
  await expect(workspace.getByRole("status")).toContainText("Toolset assimilated");
  expect((await request.get(`/api/nodes/${source.id}/document`)).status()).toBe(404);
  await workspace.getByLabel("Search knowledge").fill("copper");
  await workspace.getByRole("button", { name: "Search", exact: true }).click();
  const local = workspace.locator(".react-flow__node").filter({ hasText: "Local structure demo" });
  await expect(local).toBeVisible();
  await local.click();
  await expect(workspace.locator(".kdg-inspector h3")).toHaveText("Local structure demo");
  await expect(workspace.getByText("scripts/build_structure.py", { exact: true })).toBeAttached();
  await workspace.getByRole("button", { name: "Expand neighborhood" }).click();
  await workspace.getByRole("button", { name: "Focus selection" }).click();
  await workspace.getByLabel("Knowledge type", { exact: true }).selectOption("memory");
  await expect(workspace.locator(".react-flow__node")).toHaveCount(0);
  await workspace.getByLabel("Knowledge type", { exact: true }).selectOption("");
  await expect(local).toBeVisible();
  await workspace.getByRole("button", { name: "New entry", exact: true }).click();
  await workspace.getByLabel("Title", { exact: true }).fill("Copper validation note");
  await workspace.getByLabel("Content", { exact: true }).fill("Verify atom count and cell volume after conversion.");
  await workspace.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(workspace.locator('.react-flow__node').filter({ hasText: 'Copper validation note' })).toBeVisible();
  const saved = await request.post(`/api/nodes/${graph.id}/actions/search`, { data: { arguments: { query: 'Copper validation note' } } });
  expect((await saved.json()).value.nodes.some((entry: { title: string }) => entry.title === 'Copper validation note')).toBe(true);
  await workspace.getByRole("button", { name: "Toggle navigator" }).click();
  await expect(workspace.locator(".kdg-navigator")).toHaveCount(0);
  await page.screenshot({ path: "../.outputs/matcreator-workspace.png" });
});

test("dropping a Toolset into a declared body requires confirmation before consumption", async ({ page, request }) => {
  await page.setViewportSize({ width: 2000, height: 1000 });
  const graph = await (await request.post('/api/nodes', { data: { type: 'matcreator.kdg', name: 'Drop destination', position: { x: 100, y: 100 } } })).json();
  const source = await (await request.post('/api/nodes', { data: { type: 'matcreator.core', name: 'Drop source', position: { x: 1300, y: 100 } } })).json();
  await page.goto('/');
  const sourceCard = page.locator(`[data-card-id="${source.id}"]`);
  const destination = page.locator(`.react-flow__node[data-id="${graph.id}"]`);
  const resting = await sourceCard.boundingBox();
  async function drag() {
    const start = await sourceCard.locator('.container-header').boundingBox();
    const target = await destination.boundingBox();
    expect(start).not.toBeNull(); expect(target).not.toBeNull();
    await page.mouse.move(start!.x + 110, start!.y + 30);
    await page.mouse.down();
    await page.mouse.move(target!.x + 300, target!.y + 250, { steps: 12 });
    await expect(destination).toHaveAttribute('data-transformation-hint', /Assimilate/);
    await page.mouse.up();
  }
  page.once('dialog', dialog => dialog.dismiss());
  await drag();
  await expect(sourceCard).toBeVisible();
  expect((await request.get(`/api/nodes/${source.id}/document`)).status()).toBe(200);
  await expect(destination).not.toHaveAttribute('data-transformation-hint');
  await expect.poll(async () => Math.round((await sourceCard.boundingBox())!.x)).toBe(Math.round(resting!.x));
  page.once('dialog', dialog => dialog.accept());
  await drag();
  await expect(sourceCard).toHaveCount(0);
  expect((await request.get(`/api/nodes/${source.id}/document`)).status()).toBe(404);
});

test('palette Toolset drops directly into an inline graph workspace', async ({ page, request }) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  // Decks are user-owned; explicitly collect and place the Toolset in this fixture.
  let library = await (await request.get('/api/card-library')).json();
  const pack = Object.values(library.packs).find((item: any) => item.definition.cards.includes('matcreator.core')) as any;
  for (const action of [
    { action: 'open_pack', id: pack.definition.id },
    { action: 'create_deck', name: 'Tools', icon: 'boxes', entries: [{ kind: 'node', id: 'matcreator.core' }] },
  ]) {
    const response = await request.post('/api/card-library/actions', { data: { ...action, expected_revision: library.revision } });
    expect(response.status()).toBe(200);
    library = await response.json();
  }
  const graph = await (await request.post('/api/nodes', { data: { type: 'matcreator.kdg', name: 'Demo knowledge', position: { x: 100, y: 100 } } })).json();
  await page.goto('/');
  const container = page.locator(`[data-card-id="${graph.id}"]`);
  const workspace = container.getByRole('region', { name: 'Demo knowledge workspace', exact: true });
  await expect(workspace).toBeVisible();
  await page.getByRole('tab', { name: /Tools/ }).click();
  const palette = page.locator('.palette-item').filter({ hasText: 'Materials Core' });
  const destination = workspace.locator('.kdg-canvas');
  async function dragPalette() {
    await page.locator('.component-palette').hover({ position: { x: 20, y: 20 } });
    const dataTransfer = await page.evaluateHandle(() => new DataTransfer());
    await palette.dispatchEvent('dragstart', { dataTransfer });
    const target = await destination.boundingBox();
    const event = { dataTransfer, clientX: Math.max(50, target!.x + target!.width * .65), clientY: target!.y + 100 };
    await destination.dispatchEvent('dragover', event);
    await destination.dispatchEvent('drop', event);
    await palette.dispatchEvent('dragend', { dataTransfer });
  }
  const cancelled = new Promise<void>(resolve => page.once('dialog', async dialog => { expect(dialog.message()).toContain('Materials Core'); await dialog.dismiss(); resolve(); }));
  await dragPalette();
  await cancelled;
  expect((await (await request.get('/api/world')).json()).nodes.some((node: { type: string }) => node.type === 'matcreator.core')).toBe(false);
  const confirmed = new Promise<void>(resolve => page.once('dialog', async dialog => { expect(dialog.message()).toContain('Materials Core'); await dialog.accept(); resolve(); }));
  await dragPalette();
  await confirmed;
  await expect(workspace.locator('.kdg-node').first()).toBeVisible();
  const shape = await workspace.locator('.kdg-node').first().evaluate(el => ({ radius: getComputedStyle(el).borderRadius, width: el.clientWidth, height: el.clientHeight }));
  expect(shape.radius).toBe('50%'); expect(shape.width).toBe(shape.height);
  const outer = await container.boundingBox(); const inner = await workspace.boundingBox();
  expect(inner!.x).toBeGreaterThanOrEqual(outer!.x);
  expect(inner!.y + inner!.height).toBeLessThanOrEqual(outer!.y + outer!.height + 1);
  const world = await (await request.get('/api/world')).json();
  expect(world.nodes.some((node: { type: string }) => node.type === 'matcreator.core')).toBe(false);
  const header = await container.locator('.container-header').boundingBox();
  const previous = await workspace.boundingBox();
  await page.mouse.move(Math.max(150, header!.x + 120), header!.y + 25);
  await page.mouse.down();
  await page.mouse.move(Math.max(150, header!.x + 120) + 300, header!.y + 25, { steps: 12 });
  await page.mouse.up();
  await expect.poll(async () => (await workspace.boundingBox())!.x - previous!.x).toBeGreaterThan(200);
  const movedContainer = await container.boundingBox();
  const movedWorkspace = await workspace.boundingBox();
  expect(Math.round(movedWorkspace!.x - previous!.x)).toBe(Math.round(movedContainer!.x - outer!.x));
  await page.screenshot({ path: '../.outputs/matcreator-inline-graph.png' });
  await expect(container.getByRole('button', { name: 'Show member cards' })).toHaveCount(0);
  await expect(workspace).toBeVisible();
  const member = world.nodes.find((node: { parent_id: string }) => node.parent_id === graph.id);
  await expect(page.locator(`[data-card-id="${member.id}"]`)).toHaveCount(0);
});
