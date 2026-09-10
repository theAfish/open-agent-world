import { expect, test } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

test.use({ deviceScaleFactor: 1.5 });

test('MatterViz renders Conversation structures, pins selection, rotates locally and clears on disconnect', async ({ page, request }) => {
  test.setTimeout(110_000);
  await page.setViewportSize({ width: 2400, height: 1200 });
  const created: string[] = [];
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const create = async (type: string, name: string, x: number) => {
    const response = await request.post('/api/nodes', { data: { type, name, position: { x, y: 350 } } });
    expect(response.status()).toBe(201);
    const card = await response.json(); created.push(card.id); return card;
  };
  try {
    const room = await create('conversation', 'Structure files', 600);
    const viewer = await create('science.structure-viewer', 'Crystal viewer', 1700);
    const summary = await (await request.get(`/api/conversations/${room.id}`)).json();
    const base = `/api/conversations/${room.id}/sessions/${summary.sessions[0].id}`;
    const files = [];
    for (const [filename, content] of [
      ['helium.xyz', '1\nHelium\nHe 0 0 0\n'],
      ['water.xyz', '3\nWater\nO 0 0 0\nH 0.96 0 0\nH -0.24 0.93 0\n'],
      ['POSCAR', 'Silicon\n1\n5.43 0 0\n0 5.43 0\n0 0 5.43\nSi\n2\nDirect\n0 0 0\n0.25 0.25 0.25\n'],
      ['salt.cif', 'data_salt\n_cell_length_a 5.64\n_cell_length_b 5.64\n_cell_length_c 5.64\n_cell_angle_alpha 90\n_cell_angle_beta 90\n_cell_angle_gamma 90\nloop_\n_atom_site_label\n_atom_site_type_symbol\n_atom_site_fract_x\n_atom_site_fract_y\n_atom_site_fract_z\nNa1 Na 0 0 0\nCl1 Cl 0.5 0.5 0.5\n'],
    ]) {
      const response = await request.post(`${base}/attachments?filename=${filename}`, { data: Buffer.from(content), headers: { 'Content-Type': 'application/octet-stream' } });
      expect(response.status()).toBe(201); files.push(await response.json());
    }
    expect((await request.post(`${base}/messages`, { data: { attachments: files.map(({ version_id, path }) => ({ version_id, path })) } })).status()).toBe(202);
    const edgeResponse = await request.post('/api/edges', { data: { source: viewer.id, target: room.id, relationship: 'core.file-preview' } });
    expect(edgeResponse.status()).toBe(201); const edge = await edgeResponse.json();
    await page.goto('/');
    for (const id of [room.id, viewer.id]) {
      const card = page.locator(`[data-card-id="${id}"]`);
      await card.locator('.card-kind-icon').click();
      await card.getByRole('button', { name: 'Open workspace' }).click();
    }
    const source = page.locator(`[data-workspace-node-id="${room.id}"]`);
    const display = page.locator(`[data-workspace-node-id="${viewer.id}"]`);
    await page.locator('.react-flow__controls-zoomout').click();
    await page.locator('.react-flow__controls-zoomout').click();
    await source.getByRole('button', { name: 'Open helium.xyz', exact: true }).click();
    await expect(display.locator('.structure-canvas')).toHaveAttribute('data-atom-count', '1', { timeout: 75_000 });
    const assertAlignedPicking = async () => {
      const canvas = display.locator('.viewport-cell canvas').first();
      await expect(canvas).toBeVisible();
      await expect.poll(async () => canvas.evaluate(element => {
        const actual = element.getBoundingClientRect();
        const expected = element.closest('.viewport-cell')!.getBoundingClientRect();
        return Math.max(...(['x', 'y', 'width', 'height'] as const).map(key => Math.abs(actual[key] - expected[key])));
      })).toBeLessThan(1);
      const box = (await canvas.boundingBox())!;
      await expect(async () => {
        await page.mouse.move(box.x + box.width / 2 - 3, box.y + box.height / 2);
        await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
        await expect(display.locator('.elem-name')).toHaveText('Helium', { timeout: 500 });
      }).toPass({ timeout: 10_000 });
      const tooltip = (await display.locator('.viewport-cell [role="tooltip"]').boundingBox())!;
      expect(Math.abs(tooltip.x - box.x - box.width / 2)).toBeLessThan(2);
      expect(Math.abs(tooltip.y - box.y - box.height / 2)).toBeLessThan(2);
      await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2, { delay: 100 });
      await expect(display.locator('.selection-label')).toHaveText('1');
      await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2, { delay: 100 });
      await expect(display.locator('.selection-label')).toHaveCount(0);
      await page.mouse.move(box.x + box.width * .08, box.y + box.height * .85);
      await expect(display.locator('.elem-name')).toHaveCount(0);
    };
    await assertAlignedPicking();
    await page.locator('.react-flow__controls-zoomout').click();
    await page.locator('.react-flow__controls-zoomout').click();
    await assertAlignedPicking();
    await page.locator('.react-flow__controls-zoomin').click();
    await page.locator('.react-flow__controls-zoomin').click();
    await assertAlignedPicking();
    // Moving and resizing a mounted workspace must preserve the same local space.
    const heading = (await display.locator('.workspace-titlebar').boundingBox())!;
    const oldWindow = (await display.boundingBox())!;
    await page.mouse.move(heading.x + 150, heading.y + 15);
    await page.mouse.down();
    await page.mouse.move(heading.x + 210, heading.y + 65, { steps: 8 });
    await page.mouse.up();
    expect((await display.boundingBox())!.y).toBeGreaterThan(oldWindow.y + 20);
    const grip = page.locator(`[data-card-id="${viewer.id}"] .container-resize-arc`);
    await expect(grip).toBeVisible();
    const handle = (await grip.boundingBox())!;
    const oldWidth = await display.evaluate(el => el.clientWidth);
    await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2);
    await page.mouse.down();
    await page.mouse.move(handle.x + handle.width / 2 + 70, handle.y + handle.height / 2 + 40, { steps: 8 });
    await page.mouse.up();
    await expect.poll(() => display.evaluate(el => el.clientWidth)).toBeGreaterThan(oldWidth + 10);
    await assertAlignedPicking();
    await page.screenshot({ path: '../.outputs/structure-viewer-picking.png' });
    for (let i = 0; i < 3; i++) await page.locator('.react-flow__controls-zoomin').click();
    await expect.poll(() => display.locator('.viewport-cell canvas').first().evaluate(el => el.getBoundingClientRect().width / el.clientWidth)).toBeGreaterThan(1);
    await assertAlignedPicking();
    // Restore the overview so both source and viewer controls stay on screen.
    for (let i = 0; i < 3; i++) await page.locator('.react-flow__controls-zoomout').click();
    await source.getByRole('button', { name: 'Open water.xyz', exact: true }).click();
    await expect(display.locator('.structure-canvas')).toHaveAttribute('data-atom-count', '3', { timeout: 75_000 });
    await expect(display.locator('canvas').first()).toBeVisible();
    await expect(display.locator('.structure-empty[role=alert]')).toHaveCount(0);
    expect(errors).toEqual([]);
    await display.getByRole('button', { name: 'Following', exact: true }).click();
    await source.getByRole('button', { name: 'Open POSCAR', exact: true }).click();
    await expect(display.locator('.structure-toolbar strong')).toHaveText('water.xyz');
    await display.getByRole('button', { name: 'Pinned', exact: true }).click();
    await expect(display.locator('.structure-toolbar strong')).toHaveText('POSCAR');
    await expect(display.locator('.structure-canvas')).toHaveAttribute('data-atom-count', '2');
    await source.getByRole('button', { name: 'Open salt.cif', exact: true }).click();
    await expect(display.locator('.structure-toolbar strong')).toHaveText('salt.cif');
    await expect(display.locator('.structure-canvas')).toHaveAttribute('data-atom-count', '2');
    // A renderer gesture must not move the world or the viewer window.
    const canvas = display.locator('canvas').first();
    const box = (await canvas.boundingBox())!;
    const before = await display.boundingBox();
    const beforeRotation = await canvas.screenshot();
    await page.mouse.move(box.x + box.width * .45, box.y + box.height * .5);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * .6, box.y + box.height * .6, { steps: 12 });
    await page.mouse.up();
    expect(await display.boundingBox()).toEqual(before);
    expect((await canvas.screenshot()).equals(beforeRotation)).toBe(false);
    await page.screenshot({ path: '../.outputs/structure-viewer.png' });
    expect(errors).toEqual([]);
    expect((await request.delete(`/api/edges/${edge.id}`)).status()).toBe(200);
    await expect(display.locator('canvas')).toHaveCount(0);
    await expect(display.getByText('Connect this card to a Sandbox or Conversation')).toBeVisible();
  } catch (error) {
    await page.screenshot({ path: '../.outputs/structure-viewer-error.png' });
    await test.info().attach('browser-errors', { body: JSON.stringify(errors), contentType: 'application/json' });
    throw error;
  } finally {
    for (const id of created.reverse()) await request.delete(`/api/nodes/${id}`);
  }
});

test('a connected viewer reads a real Sandbox workspace file without starting a process', async ({ page, request }) => {
  test.setTimeout(75_000);
  await page.setViewportSize({ width: 2400, height: 1200 });
  const directory = fileURLToPath(new URL('../../.tmp/structure-e2e-files/', import.meta.url));
  await mkdir(directory, { recursive: true });
  await writeFile(`${directory}/molecule.xyz`, '2\nHydrogen\nH 0 0 0\nH 0.74 0 0\n');
  const sandboxResponse = await request.post('/api/nodes', { data: { type: 'sandbox', name: 'Structure workspace',
    position: { x: 600, y: 350 }, config: { workspace_path: directory, workspace_access: 'read_only' } } });
  expect(sandboxResponse.status(), await sandboxResponse.text()).toBe(201);
  const sandbox = await sandboxResponse.json();
  const viewerResponse = await request.post('/api/nodes', { data: { type: 'science.structure-viewer', name: 'Sandbox viewer', position: { x: 1700, y: 350 } } });
  expect(viewerResponse.status()).toBe(201);
  const viewer = await viewerResponse.json();
  try {
    expect((await request.post('/api/edges', { data: { source: viewer.id, target: sandbox.id, relationship: 'core.file-preview' } })).status()).toBe(201);
    await page.goto('/');
    for (const id of [sandbox.id, viewer.id]) {
      const card = page.locator(`[data-card-id="${id}"]`);
      await card.locator('.card-kind-icon').click();
      await card.getByRole('button', { name: 'Open workspace', exact: true }).click();
    }
    const source = page.locator(`[data-workspace-node-id="${sandbox.id}"]`);
    const display = page.locator(`[data-workspace-node-id="${viewer.id}"]`);
    await source.getByRole('button', { name: 'molecule.xyz', exact: true }).click();
    await expect(display.locator('.structure-canvas')).toHaveAttribute('data-atom-count', '2', { timeout: 45_000 });
    await expect(display.locator('canvas').first()).toBeVisible();
    await expect(display.locator('.structure-empty[role=alert]')).toHaveCount(0);
    await expect(source.locator('.sandbox-preview-content')).toContainText('Hydrogen');
    const history = await (await request.get(`/api/sandboxes/${sandbox.id}/history`)).json();
    expect(history).toEqual([]);
    await page.screenshot({ path: '../.outputs/structure-viewer-sandbox.png' });
  } finally {
    await request.delete(`/api/nodes/${viewer.id}`);
    await request.delete(`/api/nodes/${sandbox.id}`);
  }
});
