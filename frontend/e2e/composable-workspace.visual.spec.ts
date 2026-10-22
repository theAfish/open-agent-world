import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

const capturePhase = process.env.OAW_WORKSPACE_CAPTURE_PHASE ?? 'after';
const screenshotRoot = `../.outputs/composable-workspace/${capturePhase}`;

async function prepare(request: APIRequestContext) {
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
      'oaw.locale': 'en', 'oaw-theme': 'light', 'oaw-node-surfaces-v1': null, 'oaw-canvas-viewport-v1': null,
    },
  } })).ok()).toBe(true);
}

async function open(page: Page, group: string, name: string) {
  await page.goto('/');
  await page.getByRole('button', { name: 'Fit view', exact: true }).click();
  const card = page.locator(`[data-card-id="${group}"]`);
  // The group surrounds distant preset members; its title can be outside the
  // viewport after Fit view. The card's existing action still opens the real UI.
  await card.getByRole('button', { name: 'Workspace mode', exact: true }).evaluate((button: HTMLButtonElement) => button.click());
  const workspace = page.getByRole('dialog', { name: `${name} workspace mode`, exact: true });
  await expect(workspace).toBeVisible();
  return workspace;
}

test.afterEach(async ({ request }) => {
  const nodes = await (await request.get('/api/nodes')).json();
  if (nodes.length) expect((await request.post('/api/nodes/batch-delete', { data: { node_ids: nodes.map((node: { id: string }) => node.id) } })).ok()).toBe(true);
});

for (const preset of [
  { id: 'matcreator.native-research', name: 'MatCreator research', slug: 'matcreator' },
  { id: 'knowledge.base.research', name: 'Lab Manager', slug: 'knowledge' },
]) test(`${preset.slug} real workspace visual matrix`, async ({ page, request }) => {
  test.setTimeout(90_000);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await prepare(request);
  await page.setViewportSize({ width: 1600, height: 1000 });
  const response = await request.post(`/api/legions/presets/${preset.id}/instances`, { data: {} });
  expect(response.status(), await response.text()).toBe(201);
  const { node_ids: ids } = await response.json();
  const workspace = await open(page, ids.group, preset.name);
  await expect(workspace.locator('.legion-pane-content').first()).toBeVisible();
  await page.screenshot({ path: `${screenshotRoot}/${preset.slug}-empty-light-wide.png` });
  if (preset.slug === 'knowledge') {
    const app = workspace.locator('.knowledge-app');
    await expect(app.getByText('Add your first document')).toBeVisible();
    await app.getByLabel('Add documents', { exact: true }).setInputFiles({ name: 'Materials research notes.md', mimeType: 'text/markdown',
      buffer: Buffer.from('# Materials research notes\n\n## Transport study\n\nReview the measured conductivity before accepting the result.\n\n| Material | Temperature | Conductivity |\n| --- | --- | --- |\n| Li6PS5Cl | 300 K | 1.2 mS/cm |\n| Li6PS5Br | 300 K | 0.8 mS/cm |\n\n## Next steps\n\nCompare the source documents and record the assumptions.') });
    await app.getByRole('button', { name: 'Process all pending (1)', exact: true }).click();
    const document = app.getByRole('button', { name: /Materials research notes.*Ready/ });
    await expect(document).toBeVisible({ timeout: 30_000 });
    await document.click();
    await expect(app.locator('.knowledge-prose table')).toBeVisible();
  } else {
    await expect(workspace.getByRole('region', { name: 'Execution graph', exact: true })).toBeVisible();
  }
  for (const theme of ['light', 'dark']) {
    await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
    if (preset.slug === 'knowledge') {
      const accent = theme === 'light' ? 'rgb(107, 79, 176)' : 'rgb(194, 174, 242)';
      await expect(workspace.locator('.knowledge-upload')).toHaveCSS('background-color', accent);
      await expect(workspace.locator('.knowledge-tabstrip [aria-selected=true]')).toHaveCSS('color', accent);
    }
    for (const size of [{ width: 1600, height: 1000, label: 'wide' }, { width: 960, height: 760, label: 'narrow' }]) {
      await page.setViewportSize(size);
      await page.screenshot({ path: `${screenshotRoot}/${preset.slug}-${theme}-${size.label}.png` });
      const fit = await workspace.locator('.legion-layout-stage').evaluate(element => ({ width: element.clientWidth, scrollWidth: element.scrollWidth }));
      expect(fit.scrollWidth).toBeLessThanOrEqual(fit.width + 1);
    }
  }
  await workspace.getByRole('button', { name: 'Edit layout', exact: true }).click();
  await page.screenshot({ path: `${screenshotRoot}/${preset.slug}-dark-narrow-edit.png` });
  await workspace.getByRole('button', { name: 'Done editing', exact: true }).click();
  if (preset.slug === 'knowledge') {
    await workspace.getByRole('button', { name: 'Edit layout', exact: true }).click();
    const owner = workspace.locator(`[data-workspace-pane="${ids.knowledge}"]`);
    const sourceSection = owner.locator('[data-workspace-section="sources"]');
    await sourceSection.hover();
    const bounds = (await owner.boundingBox())!;
    await sourceSection.getByRole('button', { name: 'Arrange Sources', exact: true }).dragTo(owner,
      { targetPosition: { x: bounds.width - 8, y: bounds.height / 2 } });
    const detached = workspace.locator('[data-workspace-section-pane="sources"]');
    await expect(detached.getByLabel('Add documents', { exact: true })).toBeAttached();
    await workspace.getByRole('button', { name: 'Done editing', exact: true }).click();
    await expect(detached.getByRole('button', { name: 'Search', exact: true })).toBeVisible();
    const detachedContent = detached.locator('.knowledge-section:not(.workspace-section)');
    await expect(detachedContent).toHaveCSS('container-name', 'knowledge-section');
    await expect(detachedContent).toHaveCSS('overflow-y', 'auto');
    await expect(detached.locator('.knowledge-upload')).toHaveCSS('background-color', 'rgb(194, 174, 242)');
    await page.screenshot({ path: `${screenshotRoot}/knowledge-detached-sources-dark-narrow.png` });
    // Scrolling must work through the section itself, which has moved outside
    // the card's normal scroll container. The host pane deliberately clips.
    const contentSize = await detachedContent.evaluate(element => ({ height: element.clientHeight, scroll: element.scrollHeight }));
    expect(contentSize.scroll).toBeGreaterThan(contentSize.height);
    const contentBounds = (await detachedContent.boundingBox())!;
    await page.mouse.move(contentBounds.x + contentBounds.width / 2, contentBounds.y + contentBounds.height / 2);
    await page.mouse.wheel(0, 1200);
    await expect.poll(() => detachedContent.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
    await expect(detached.getByRole('heading', { name: 'Next steps', exact: true })).toBeInViewport();
    const scrollPosition = await detachedContent.evaluate(element => element.scrollTop);
    await owner.getByRole('tab', { name: 'Schemas', exact: true }).click();
    await expect(detached.getByRole('heading', { name: 'Next steps', exact: true })).toBeInViewport();
    expect(await detachedContent.evaluate(element => element.scrollTop)).toBe(scrollPosition);
    await page.screenshot({ path: `${screenshotRoot}/knowledge-detached-sources-scrolled-dark-narrow.png` });
  } else {
    await workspace.getByRole('tab', { name: 'Skills & knowledge', exact: true }).click();
    await expect(workspace.getByRole('region', { name: 'Know-Do Graph workspace', exact: true })).toBeVisible();
    await page.screenshot({ path: `${screenshotRoot}/matcreator-knowledge-narrow.png` });
  }
  expect(errors).toEqual([]);
});

test('non-chat graph and compute workspace preserves a manually resized layout on reopen', async ({ page, request }) => {
  test.setTimeout(60_000);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await prepare(request);
  await page.setViewportSize({ width: 1600, height: 1000 });
  const cards = [];
  for (const [type, name] of [['matcreator.kdg', 'Research graph'], ['sandbox', 'Files and terminal'], ['text', 'Research notes']]) {
    const response = await request.post('/api/nodes', { data: { type, name, position: { x: 300 + cards.length * 400, y: 300 } } });
    expect(response.status()).toBe(201);
    cards.push(await response.json());
  }
  const graph = cards[0];
  let revision = (await (await request.get(`/api/nodes/${graph.id}/document`)).json()).revision;
  for (const [title, type, summary] of [['Transport calculation', 'procedure', 'Compute and review ionic transport.'], ['Convergence check', 'heuristic', 'Inspect convergence before acceptance.'], ['Conductivity study', 'memory', 'Source observations awaiting review.']]) {
    const response = await request.post(`/api/nodes/${graph.id}/actions/edit`, { data: { expected_revision: revision, arguments: { title, type, summary, content: summary } } });
    expect(response.ok()).toBe(true);
    revision = (await response.json()).revision;
  }
  const response = await request.post('/api/legion-groups', { data: { name: 'Graph and compute', node_ids: cards.map(card => card.id) } });
  expect(response.ok()).toBe(true);
  const group = (await response.json())[0];
  const pane = (index: number) => ({ kind: 'pane', view: { card_id: cards[index].id } });
  expect((await request.patch(`/api/nodes/${group.id}`, { data: { config: { workspace_layout: { version: 2, hidden_sections: [], root: {
    kind: 'split', axis: 'horizontal', ratio: .62, first: pane(0), second: { kind: 'split', axis: 'vertical', ratio: .7, first: pane(1), second: pane(2) },
  } } } } })).ok()).toBe(true);
  const workspace = await open(page, group.id, 'Graph and compute');
  await expect(workspace.getByRole('textbox', { name: 'Conversation message', exact: true })).toHaveCount(0);
  await expect(workspace.getByRole('region', { name: 'Know-Do Graph workspace', exact: true })).toBeVisible();
  await expect(workspace.locator('.network-map')).toHaveAttribute('data-node-count', '3');
  for (const theme of ['light', 'dark']) {
    await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
    for (const size of [{ width: 1600, height: 1000, label: 'wide' }, { width: 960, height: 760, label: 'narrow' }]) {
      await page.setViewportSize(size);
      await page.screenshot({ path: `${screenshotRoot}/non-chat-${theme}-${size.label}.png` });
    }
  }
  const terminal = workspace.getByRole('region', { name: 'Sandbox terminal', exact: true });
  await workspace.getByRole('navigation', { name: 'Workspace views', exact: true }).getByRole('button', { name: 'Terminal', exact: true }).click();
  await terminal.scrollIntoViewIfNeeded();
  await expect(terminal.getByRole('textbox', { name: 'Command', exact: true })).toBeInViewport();
  await page.screenshot({ path: `${screenshotRoot}/non-chat-dark-narrow-terminal-scroll.png` });
  await workspace.getByRole('button', { name: 'Edit layout', exact: true }).click();
  await page.screenshot({ path: `${screenshotRoot}/non-chat-dark-narrow-edit.png` });
  const divider = workspace.locator('.legion-layout-divider').first();
  await divider.press('ArrowLeft');
  await workspace.getByRole('button', { name: 'Done editing', exact: true }).click();
  await expect(workspace.locator('.legion-window-status')).not.toHaveText(/Saving|Unsaved/);
  const saved = (await (await request.get(`/api/nodes/${group.id}`)).json()).config.workspace_layout;
  expect(saved.root.ratio).not.toBe(.62);
  await workspace.getByRole('button', { name: 'Back to canvas', exact: true }).click();
  await expect(workspace).toBeHidden();
  await open(page, group.id, 'Graph and compute');
  expect((await (await request.get(`/api/nodes/${group.id}`)).json()).config.workspace_layout).toEqual(saved);
  await expect(workspace.locator('.legion-workspace-pane')).toHaveCount(3);
  expect(errors).toEqual([]);
});

test('compose a non-chat workspace once and preserve its layout through content and viewport changes', async ({ page, request }) => {
  test.setTimeout(60_000);
  await prepare(request);
  await page.setViewportSize({ width: 1500, height: 960 });
  const cards = [];
  for (const [type, name] of [['sandbox', 'Compute workspace'], ['text', 'Lab notes'], ['matcreator.kdg', 'Evidence graph']]) {
    const response = await request.post('/api/nodes', { data: { type, name, position: { x: 300 + cards.length * 360, y: 300 } } });
    expect(response.status()).toBe(201);
    cards.push(await response.json());
  }
  const grouped = await request.post('/api/legion-groups', { data: { name: 'Non-chat workbench', node_ids: cards.map(card => card.id) } });
  expect(grouped.ok()).toBe(true);
  const group = (await grouped.json())[0];
  const workspace = await open(page, group.id, 'Non-chat workbench');
  await expect(workspace.locator('[data-workspace-pane]')).toHaveCount(0);
  await workspace.getByRole('button', { name: 'Compose all cards', exact: true }).click();
  await expect(workspace.locator('[data-workspace-pane]')).toHaveCount(3);
  for (const card of cards) await expect(workspace.locator(`[data-workspace-pane="${card.id}"]`)).toBeVisible();
  await expect(workspace.getByRole('textbox', { name: 'Conversation message', exact: true })).toHaveCount(0);
  await workspace.getByRole('button', { name: 'Done editing', exact: true }).click();
  await expect(workspace.locator('.legion-window-status')).not.toHaveText(/Saving|Unsaved/);
  const saved = (await (await request.get(`/api/nodes/${group.id}`)).json()).config.workspace_layout;
  expect(saved.root.kind).toBe('split');
  await page.screenshot({ path: `${screenshotRoot}/non-chat-composed-light-wide.png` });
  await workspace.getByRole('button', { name: 'Edit layout', exact: true }).click();
  await expect(workspace.getByRole('button', { name: 'Compose all cards', exact: true })).toHaveCount(0);
  await workspace.getByRole('button', { name: 'Done editing', exact: true }).click();
  await workspace.getByRole('button', { name: 'Back to canvas', exact: true }).click();
  await expect(workspace).toBeHidden();
  await open(page, group.id, 'Non-chat workbench');
  await page.setViewportSize({ width: 960, height: 760 });
  expect((await request.patch(`/api/nodes/${cards[1].id}`, { data: { name: 'Reviewed lab notes' } })).ok()).toBe(true);
  await expect(workspace.getByRole('tab', { name: 'Reviewed lab notes', exact: true })).toBeVisible();
  expect((await (await request.get(`/api/nodes/${group.id}`)).json()).config.workspace_layout).toEqual(saved);
  await expect(workspace.locator('[data-workspace-pane]')).toHaveCount(3);
  await page.screenshot({ path: `${screenshotRoot}/non-chat-composed-light-narrow.png` });
});

test('real graph controls and entry editor remain usable in 280px and 360px content containers', async ({ page, request }) => {
  test.setTimeout(60_000);
  await prepare(request);
  await page.setViewportSize({ width: 1000, height: 850 });
  const created = await request.post('/api/nodes', { data: { type: 'matcreator.kdg', name: 'Compact graph', position: { x: 300, y: 300 } } });
  expect(created.status()).toBe(201);
  const graph = await created.json();
  const grouped = await request.post('/api/legion-groups', { data: { name: 'Graph controls', node_ids: [graph.id] } });
  expect(grouped.ok()).toBe(true);
  const group = (await grouped.json())[0];
  expect((await request.patch(`/api/nodes/${group.id}`, { data: { config: { workspace_layout: { version: 2,
    hidden_sections: [], root: { kind: 'pane', view: { card_id: graph.id } },
  } } } })).ok()).toBe(true);
  const workspace = await open(page, group.id, 'Graph controls');
  const app = workspace.getByRole('region', { name: 'Know-Do Graph workspace', exact: true });
  for (const width of [280, 360]) {
    // Constrain the production component independently of the surrounding window.
    await app.evaluate((element, value) => { (element as HTMLElement).style.width = `${value}px`; }, width);
    await expect.poll(async () => (await app.boundingBox())!.width).toBe(width);
    const bounds = (await app.boundingBox())!;
    for (const control of await app.locator('.kdg-toolbar button, .kdg-toolbar input').all()) {
      const box = (await control.boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(bounds.x - 1);
      expect(box.x + box.width).toBeLessThanOrEqual(bounds.x + bounds.width + 1);
    }
    await app.getByRole('button', { name: 'Toggle navigator', exact: true }).click();
    await expect(app.getByLabel('Workspace mode', { exact: true })).toBeVisible();
    await app.getByRole('button', { name: 'Toggle navigator', exact: true }).click();
    await app.getByRole('button', { name: 'New entry', exact: true }).click();
    await app.getByLabel('Title', { exact: true }).fill(`Compact ${width}px evidence`);
    await app.getByLabel('Content', { exact: true }).fill('Recorded through the real graph editor.');
    await app.getByRole('button', { name: 'Save changes', exact: true }).click();
    await expect(app.locator('.kdg-inspector h3')).toHaveText(`Compact ${width}px evidence`);
    await app.screenshot({ path: `${screenshotRoot}/graph-content-${width}px.png` });
    await app.getByRole('button', { name: 'Close inspector', exact: true }).click();
  }
});
