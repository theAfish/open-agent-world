import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

test('switches canvas workspaces and publishes their selected menu order', async ({ page, request }) => {
  const { cards, group } = await setup(request);
  const layout = (cardId: string) => ({ version: 2, root: { kind: 'pane', view: { card_id: cardId } } });
  expect((await request.patch(`/api/nodes/${group.id}`, { data: { config: { workspace_layout: layout(cards[0].id) } } })).ok()).toBe(true);
  const note = await (await request.post('/api/nodes', { data: { type: 'text', name: 'Operations guide', content: 'Operations workspace content' } })).json();
  const grouped = await (await request.post('/api/legion-groups', { data: { name: 'Operations', node_ids: [note.id] } })).json();
  const operations = grouped.find((card: { type: string }) => card.type === 'legion');
  expect((await request.patch(`/api/nodes/${operations.id}`, { data: { config: { workspace_layout: layout(note.id) } } })).ok()).toBe(true);
  await page.goto('/');
  let workspace = await openWorkspace(page, group.id);
  await workspace.getByRole('button', { name: 'Switch workspace' }).click();
  await expect(page.getByRole('menuitemradio')).toHaveText(['Project studio', 'Operations']);
  await page.screenshot({ path: '../.tmp/builder-workspace-switcher.png', fullPage: true });
  await page.getByRole('menuitemradio', { name: 'Operations' }).click();
  workspace = page.getByRole('dialog', { name: 'Operations workspace mode' });
  await expect(workspace.locator('.text-editor:visible')).toHaveValue('Operations workspace content');
  await workspace.getByRole('button', { name: 'Publish application' }).click();
  const publish = page.getByRole('dialog', { name: 'Publish application', exact: true });
  await publish.getByRole('checkbox', { name: 'Project studio' }).check();
  await publish.getByRole('button', { name: 'Move Project studio up' }).click();
  await expect(publish.getByRole('button', { name: 'Create release' })).toBeEnabled();
  await page.screenshot({ path: '../.tmp/publish-workspace-order.png', fullPage: true });
  const published = page.waitForResponse(response => response.url().endsWith('/api/deployments') && response.request().method() === 'POST');
  await publish.getByRole('button', { name: 'Create release' }).click();
  const release = await (await published).json();
  expect(release.workspaces.map((item: { legion_id: string }) => item.legion_id)).toEqual([group.id, operations.id]);
  await publish.getByRole('button', { name: 'Close', exact: true }).click();
  await workspace.getByRole('button', { name: 'Switch workspace' }).click();
  await page.getByRole('menuitemradio', { name: 'Project studio' }).click();
  await page.reload();
  await expect(page.getByRole('dialog', { name: 'Project studio workspace mode' })).toBeVisible();
  const trigger = page.getByRole('button', { name: 'Switch workspace' });
  await trigger.press('ArrowDown');
  await page.getByRole('menuitemradio', { name: 'Project studio' }).press('End');
  await expect(page.getByRole('menuitemradio', { name: 'Operations' })).toBeFocused();
  await page.getByRole('menuitemradio', { name: 'Operations' }).press('Enter');
  await expect(page.getByRole('dialog', { name: 'Operations workspace mode' })).toBeVisible();
  await page.getByRole('dialog', { name: 'Operations workspace mode' }).getByRole('button', { name: 'Use dark theme' }).click();
  await trigger.click();
  await page.screenshot({ path: '../.tmp/builder-workspace-switcher-dark.png', fullPage: true, animations: 'disabled' });
  await page.getByRole('menuitemradio', { name: 'Operations' }).press('Escape');
  await expect(page.getByRole('menu')).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await trigger.click();
  await page.locator('.legion-layout-stage').click({ position: { x: 500, y: 450 } });
  await expect(page.getByRole('menu')).toHaveCount(0);
});

for (const nestedAxis of ['horizontal', 'vertical']) test(`resizing nested ${nestedAxis} workspace regions fills the stage without expanding it`, async ({ page, request }) => {
  await page.setViewportSize({ width: 1400, height: 900 });
  const { cards, group } = await setup(request);
  const pane = (index: number) => ({ kind: 'pane', view: { card_id: cards[index].id } });
  await request.patch(`/api/nodes/${group.id}`, { data: { config: { workspace_layout: {
    version: 2, hidden_sections: [], root: { kind: 'split', axis: 'horizontal', ratio: .5,
      first: pane(0), second: { kind: 'split', axis: nestedAxis, ratio: .5, first: pane(1), second: pane(2) } },
  } } } });
  await page.goto('/');
  const window = await openWorkspace(page, group.id);
  const stage = window.locator('.legion-layout-stage');
  const assertFits = async () => {
    const size = await stage.evaluate(element => ({ w: element.clientWidth, sw: element.scrollWidth, h: element.clientHeight, sh: element.scrollHeight }));
    expect(size.sw).toBeLessThanOrEqual(size.w + 1);
    expect(size.sh).toBeLessThanOrEqual(size.h + 1);
    for (const split of await window.locator('.legion-layout-split').all()) {
      const edges = await split.evaluate(element => {
        const bounds = element.getBoundingClientRect();
        const first = element.firstElementChild!.getBoundingClientRect();
        const last = element.lastElementChild!.getBoundingClientRect();
        return [first.left - bounds.left, first.top - bounds.top, bounds.right - last.right, bounds.bottom - last.bottom];
      });
      for (const gap of edges) expect(Math.abs(gap)).toBeLessThanOrEqual(1);
    }
    for (const pane of await window.locator('.legion-workspace-pane').all()) {
      const bounds = await pane.boundingBox();
      expect(bounds!.width).toBeGreaterThanOrEqual(239);
      expect(bounds!.height).toBeGreaterThanOrEqual(199);
    }
  };
  await assertFits();
  for (const editing of [false, true]) {
    if (editing) await window.getByRole('button', { name: 'Edit layout', exact: true }).click();
    for (const split of await window.locator('.legion-layout-split').all()) {
      const axis = await split.getAttribute('data-split-axis');
      const divider = split.locator(':scope > .legion-layout-divider');
      for (const direction of [-1, 1]) {
        await expect(divider).toHaveAttribute('aria-disabled', 'false');
        const bounds = (await split.boundingBox())!;
        const handle = (await divider.boundingBox())!;
        await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2);
        await page.mouse.down();
        await page.mouse.move(axis === 'horizontal' ? bounds.x + (direction < 0 ? 10 : bounds.width - 10) : handle.x + handle.width / 2,
          axis === 'vertical' ? bounds.y + (direction < 0 ? 10 : bounds.height - 10) : handle.y + handle.height / 2, { steps: 12 });
        await assertFits();
        await page.mouse.up();
        if (!editing) await expect(window.locator('.legion-window-status')).not.toHaveText(/Saving|Unsaved/);
        await divider.press(direction < 0 ? 'End' : 'Home');
        if (!editing) await expect(window.locator('.legion-window-status')).not.toHaveText(/Saving|Unsaved/);
        await assertFits();
      }
    }
  }
  await window.getByRole('button', { name: 'Done editing', exact: true }).click();
  await page.reload();
  await openWorkspace(page, group.id);
  await page.setViewportSize({ width: 900, height: 700 });
  await assertFits();
});

async function setup(request: APIRequestContext) {
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation, changes: {
    'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en',
    'oaw-canvas-viewport-v1': null, 'oaw-node-surfaces-v1': null, 'oaw-theme': 'light',
  } } })).ok()).toBe(true);
  const cards = [];
  for (const [type, name] of [['text', 'Project notes'], ['conversation', 'Conversation'], ['sandbox', 'Sandbox'], ['agent', 'Assistant']]) {
    const response = await request.post('/api/nodes', { data: { type, name, position: { x: 300 + cards.length * 350, y: 300 } } });
    expect(response.status()).toBe(201);
    cards.push(await response.json());
  }
  const response = await request.post('/api/legion-groups', { data: { name: 'Project studio', node_ids: cards.map(card => card.id) } });
  expect(response.ok()).toBe(true);
  return { cards, group: (await response.json())[0] };
}

async function openWorkspace(page: Page, id: string) {
  const workspace = page.getByRole('dialog', { name: 'Project studio workspace mode' });
  await expect(page.locator(`[data-card-id="${id}"]`)).toBeAttached();
  // Reload resumes the saved active workspace. Its modal correctly blocks
  // pointer input to the canvas, so reuse that restored window.
  if (await workspace.isVisible()) return workspace;
  await page.getByRole('button', { name: 'Fit view', exact: true }).click();
  await page.locator(`[data-card-id="${id}"]`).getByRole('button', { name: 'Workspace mode', exact: true }).click();
  return workspace;
}

test.afterEach(async ({ request }) => {
  const nodes = await (await request.get('/api/nodes')).json();
  if (nodes.length) expect((await request.post('/api/nodes/batch-delete', { data: { node_ids: nodes.map((node: { id: string }) => node.id) } })).ok()).toBe(true);
  for (const template of await (await request.get('/api/legions')).json()) await request.delete(`/api/legions/${template.id}`);
});

test('live card sections detach, hide, restore and persist with their original owner', async ({ page, request }) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1500, height: 1000 });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const { cards, group } = await setup(request);
  const conversation = cards[1], sandbox = cards[2];
  expect((await request.post(`/api/conversations/${conversation.id}/sessions`, { data: { group_title: 'Workspace section QA' } })).ok()).toBe(true);
  // Old layouts are read by the same live editor and upgraded on save.
  expect((await request.patch(`/api/nodes/${group.id}`, { data: { config: { workspace_layout: {
    version: 1, root: { kind: 'pane', card_id: conversation.id },
  } } } })).ok()).toBe(true);
  await page.goto('/');
  const window = await openWorkspace(page, group.id);
  const owner = window.locator(`[data-workspace-pane="${conversation.id}"]`);
  const composer = window.getByRole('textbox', { name: 'Conversation message', exact: true });
  await composer.fill('The same conversation draft follows its section.');
  const originalInput = await composer.elementHandle();
  await window.getByRole('button', { name: 'Edit layout', exact: true }).click();
  await expect(composer).toBeVisible();
  const inline = owner.locator('[data-workspace-section="conversation"]');
  await inline.hover();
  const handle = inline.getByRole('button', { name: 'Arrange Conversation', exact: true });
  await expect(handle).toBeVisible();
  const bounds = await owner.boundingBox();
  await handle.dragTo(owner, { targetPosition: { x: bounds!.width - 10, y: bounds!.height / 2 } });
  const detached = window.locator('[data-workspace-section-pane="conversation"]');
  await expect(detached).toBeVisible();
  await expect(owner.locator('[data-workspace-section="conversation"]')).toHaveCount(0);
  await expect(detached.getByRole('textbox', { name: 'Conversation message', exact: true })).toHaveValue('The same conversation draft follows its section.');
  expect(await originalInput!.evaluate(element => element.isConnected)).toBe(true);
  // Moving back and cancelling the layout also keeps the same DOM/input state.
  await detached.getByRole('button', { name: 'Restore Conversation · Conversation to card', exact: true }).click();
  await expect(inline).toBeVisible();
  await inline.hover();
  await inline.getByRole('button', { name: 'Arrange Conversation', exact: true }).click();
  await window.getByRole('button', { name: 'Dock right: Conversation', exact: true }).click();
  const participants = owner.locator('[data-workspace-section="participants"]');
  await participants.hover();
  await participants.getByRole('button', { name: 'Hide Participants', exact: true }).click();
  await expect(window.getByRole('region', { name: 'Hidden sections', exact: true }).getByRole('button', { name: 'Restore Participants to card', exact: true })).toBeVisible();
  await expect(participants).toHaveCount(0);
  // A second card exposes the same section API and retains its own tab state.
  await window.locator(`[data-workspace-source="${sandbox.id}"]`).click();
  await owner.getByRole('button', { name: 'Add selected card as tab', exact: true }).click();
  const sandboxOwner = window.locator(`[data-workspace-pane="${sandbox.id}"]`);
  await sandboxOwner.getByRole('tab', { name: 'History', exact: true }).click();
  const terminal = sandboxOwner.locator('[data-workspace-section="terminal"]');
  await terminal.hover();
  await terminal.getByRole('button', { name: 'Arrange Terminal', exact: true }).click();
  await window.getByRole('button', { name: 'Dock below: Sandbox', exact: true }).click();
  const terminalPane = window.locator('[data-workspace-section-pane="terminal"]');
  await expect(terminalPane.getByRole('tab', { name: 'History', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect(sandboxOwner.getByRole('separator', { name: 'Resize terminal', exact: true })).toHaveCount(0);
  await page.screenshot({ path: 'test-results/legion-sections-editor.png' });
  await window.getByRole('button', { name: 'Done editing', exact: true }).click();
  await expect(detached.getByRole('textbox', { name: 'Conversation message', exact: true })).toHaveValue('The same conversation draft follows its section.');
  expect(await originalInput!.evaluate(element => element.isConnected)).toBe(true);
  await expect(window.locator('.workspace-section-controls')).toHaveCount(0);
  const saved = (await (await request.get(`/api/nodes/${group.id}`)).json()).config.workspace_layout;
  expect(saved.version).toBe(2);
  expect(saved.hidden_sections).toEqual([{ card_id: conversation.id, section_id: 'participants' }]);
  expect(JSON.stringify(saved.root)).toContain('"section_id":"conversation"');
  await page.screenshot({ path: 'test-results/legion-sections-live.png' });
  await window.getByRole('button', { name: 'Edit layout', exact: true }).click();
  await detached.getByRole('button', { name: 'Restore Conversation · Conversation to card', exact: true }).click();
  await window.getByRole('button', { name: 'Cancel layout changes', exact: true }).click();
  await expect(detached.getByRole('textbox', { name: 'Conversation message', exact: true })).toHaveValue('The same conversation draft follows its section.');
  await window.getByRole('button', { name: 'Done editing', exact: true }).click();
  await page.reload();
  await openWorkspace(page, group.id);
  await expect(detached.getByRole('textbox', { name: 'Conversation message', exact: true })).toBeVisible();
  await expect(terminalPane.getByRole('tab', { name: 'Terminal', exact: true })).toBeVisible();
  await page.setViewportSize({ width: 900, height: 700 });
  await expect(detached.getByRole('textbox', { name: 'Conversation message', exact: true })).toBeInViewport();
  await page.screenshot({ path: 'test-results/legion-sections-small.png' });
  await page.setViewportSize({ width: 1500, height: 1000 });
  expect((await (await request.get(`/api/nodes/${group.id}`)).json()).config.workspace_layout).toEqual(saved);
  await window.getByRole('button', { name: 'Edit layout', exact: true }).click();
  const palette = window.getByRole('complementary', { name: 'Workspace cards' });
  const hiddenSections = palette.getByRole('region', { name: 'Hidden sections', exact: true });
  await expect(hiddenSections.getByText('Conversation', { exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/legion-hidden-sections.png' });
  await hiddenSections.getByRole('button', { name: 'Restore Participants to card', exact: true }).click();
  await expect(hiddenSections).toHaveCount(0);
  await window.locator('.legion-pane-tabs').getByRole('tab', { name: 'Conversation', exact: true }).click();
  await expect(owner.locator('[data-workspace-section="participants"]')).toBeVisible();
  await window.getByRole('button', { name: 'Done editing', exact: true }).click();
  expect((await (await request.get(`/api/nodes/${group.id}`)).json()).config.workspace_layout.hidden_sections).toEqual([]);
  // Owner removal removes every section reference from the visible workspace.
  expect((await request.patch(`/api/nodes/${conversation.id}`, { data: { parent_id: null } })).ok()).toBe(true);
  await expect(detached).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('an unplaced card can expose a section without placing or duplicating the whole card', async ({ page, request }) => {
  const { cards, group } = await setup(request);
  const notes = cards[0], sandbox = cards[2];
  expect((await request.patch(`/api/nodes/${group.id}`, { data: { config: { workspace_layout: {
    version: 2, root: { kind: 'pane', view: { card_id: notes.id } }, hidden_sections: [],
  } } } })).ok()).toBe(true);
  await page.goto('/');
  const window = await openWorkspace(page, group.id);
  await window.getByRole('button', { name: 'Edit layout', exact: true }).click();
  await window.getByRole('navigation', { name: 'Unplaced workspace cards' }).getByRole('button', { name: 'Sandbox', exact: true }).click();
  const drawer = window.getByRole('region', { name: 'Unplaced card details', exact: true });
  await drawer.getByRole('tab', { name: 'History', exact: true }).click();
  const terminal = drawer.locator('[data-workspace-section="terminal"]');
  await terminal.hover();
  const target = window.locator(`[data-workspace-pane="${notes.id}"]`);
  const bounds = await target.boundingBox();
  await terminal.getByRole('button', { name: 'Arrange Terminal', exact: true }).dragTo(target,
    { targetPosition: { x: bounds!.width - 10, y: bounds!.height / 2 } });
  await drawer.getByRole('button', { name: 'Collapse card details', exact: true }).click();
  const section = window.locator('[data-workspace-section-pane="terminal"]');
  await expect(section.getByRole('tab', { name: 'History', exact: true })).toHaveAttribute('aria-selected', 'true');
  await window.getByRole('button', { name: 'Remove Project notes from layout', exact: true }).click();
  await window.getByRole('button', { name: 'Done editing', exact: true }).click();
  await page.reload();
  await openWorkspace(page, group.id);
  await expect(window.locator('[data-workspace-pane]')).toHaveCount(1);
  await expect(section.getByRole('textbox', { name: 'Command', exact: true })).toBeVisible();
  await expect(window.getByRole('region', { name: 'Sandbox terminal', exact: true })).toHaveCount(1);
});

test('bottom bar opens unplaced cards and retains drafts through collapse and layout placement', async ({ page, request }) => {
  await page.setViewportSize({ width: 1400, height: 900 });
  const { cards, group } = await setup(request);
  const [notes, conversation] = cards;
  const layout = { version: 2, root: { kind: 'pane', view: { card_id: conversation.id } }, hidden_sections: [] };
  expect((await request.patch(`/api/nodes/${group.id}`, { data: { config: { workspace_layout: layout } } })).ok()).toBe(true);
  await page.goto('/');
  const window = await openWorkspace(page, group.id);
  const bar = window.getByRole('navigation', { name: 'Unplaced workspace cards' });
  const icon = bar.getByRole('button', { name: 'Project notes', exact: true });
  const drawer = window.getByRole('region', { name: 'Unplaced card details', exact: true });
  await expect(bar.getByRole('button')).toHaveCount(3);
  await expect(bar.getByRole('button', { name: 'Conversation', exact: true })).toHaveCount(0);
  await icon.click();
  await drawer.getByRole('textbox', { name: 'Contents', exact: true }).fill('Bottom bar draft');
  await icon.click();
  await expect(drawer).toHaveCount(0);
  await icon.click();
  await expect(drawer.getByRole('textbox', { name: 'Contents', exact: true })).toHaveValue('Bottom bar draft');
  await bar.getByRole('button', { name: 'Assistant', exact: true }).click();
  await expect(drawer.getByRole('region', { name: 'Assistant', exact: true })).toBeVisible();
  await icon.click();
  await expect(drawer.getByRole('textbox', { name: 'Contents', exact: true })).toHaveValue('Bottom bar draft');
  await page.screenshot({ path: 'test-results/legion-bottom-bar-light.png' });
  await page.keyboard.press('Escape');
  await expect(drawer).toHaveCount(0);
  await expect(window).toBeVisible();
  expect((await (await request.get(`/api/nodes/${group.id}`)).json()).config.workspace_layout).toEqual(layout);
  await window.getByRole('button', { name: 'Edit layout', exact: true }).click();
  await icon.dragTo(window.locator('.legion-pane-titlebar').first());
  await expect(icon).toHaveCount(0);
  await window.getByRole('button', { name: 'Done editing', exact: true }).click();
  await expect(window.getByRole('tabpanel', { name: 'Project notes', exact: true }).getByRole('textbox', { name: 'Contents', exact: true })).toHaveValue('Bottom bar draft');
  await window.getByRole('button', { name: 'Edit layout', exact: true }).click();
  await window.getByRole('button', { name: 'Remove Project notes from layout', exact: true }).click();
  await window.getByRole('button', { name: 'Done editing', exact: true }).click();
  await icon.click();
  await expect(drawer.getByRole('textbox', { name: 'Contents', exact: true })).toHaveValue('Bottom bar draft');
  await drawer.getByRole('button', { name: 'Save text', exact: true }).click();
  await expect(drawer.getByRole('button', { name: 'Save text', exact: true })).toBeDisabled();
  await window.getByRole('button', { name: 'Back to canvas', exact: true }).click();
  await page.getByRole('button', { name: 'Use dark theme' }).click();
  await openWorkspace(page, group.id);
  await icon.click();
  await page.screenshot({ path: 'test-results/legion-bottom-bar-dark.png' });
  expect((await request.patch(`/api/nodes/${notes.id}`, { data: { parent_id: null } })).ok()).toBe(true);
  await expect(icon).toHaveCount(0);
  await expect(drawer).toHaveCount(0);
});

test('drag, tile, resize, preview live cards and preserve a reusable window layout', async ({ page, request }) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const { cards, group } = await setup(request);
  const [notes, conversation, sandbox, agent] = cards;
  const positions = cards.map(card => card.position);
  await page.goto('/');
  const window = await openWorkspace(page, group.id);
  const source = (id: string) => window.locator(`[data-workspace-source="${id}"]`);
  const pane = (id: string) => window.locator(`[data-workspace-pane="${id}"]`);
  await source(notes.id).dragTo(window.locator('.legion-layout-empty'));
  await expect(pane(notes.id)).toBeVisible();
  const bounds = await pane(notes.id).boundingBox();
  await source(conversation.id).dragTo(pane(notes.id), { targetPosition: { x: bounds!.width - 8, y: bounds!.height / 2 } });
  await expect(window.locator('[data-workspace-pane]')).toHaveCount(2);
  await source(sandbox.id).click();
  await window.getByRole('button', { name: 'Dock below: Conversation', exact: true }).hover();
  await expect(window.locator('[data-dock-preview="bottom"]')).toBeVisible();
  await page.screenshot({ path: 'test-results/legion-workspace-dock-preview.png' });
  await window.getByRole('button', { name: 'Dock below: Conversation', exact: true }).click();
  await expect(window.locator('[data-workspace-pane]')).toHaveCount(3);
  await expect(source(agent.id)).toBeVisible();
  await expect(pane(agent.id)).toHaveCount(0);
  const divider = window.getByRole('separator', { name: 'Resize workspace regions' }).first();
  const dividerBounds = await divider.boundingBox();
  await page.mouse.move(dividerBounds!.x + 2, dividerBounds!.y + 80);
  await page.mouse.down(); await page.mouse.move(dividerBounds!.x - 100, dividerBounds!.y + 80, { steps: 10 }); await page.mouse.up();
  expect(Number(await divider.getAttribute('aria-valuenow'))).toBeLessThan(50);
  await divider.focus(); await page.keyboard.press('ArrowRight');
  await page.screenshot({ path: 'test-results/legion-workspace-edit-light.png' });
  // Every rectangle shares its boundaries; no card border radii or freeform gaps.
  const [left, upper, lower] = await Promise.all([pane(notes.id).boundingBox(), pane(conversation.id).boundingBox(), pane(sandbox.id).boundingBox()]);
  expect(upper!.x - left!.x - left!.width).toBeCloseTo(5, 0);
  expect(lower!.y - upper!.y - upper!.height).toBeCloseTo(5, 0);
  expect(upper!.width).toBeCloseTo(lower!.width, 0);
  expect(await pane(notes.id).evaluate(element => getComputedStyle(element).borderRadius)).toBe('0px');
  await window.getByRole('button', { name: 'Done editing', exact: true }).click();
  await expect(window.getByRole('button', { name: 'Save layout', exact: true })).toHaveCount(0);
  await pane(notes.id).getByRole('textbox', { name: 'Contents', exact: true }).fill('A real document draft, retained while editing the layout.');
  await window.getByRole('button', { name: 'Edit layout', exact: true }).click();
  // Move a live pane to another branch without resetting its document draft.
  await source(notes.id).click();
  await window.getByRole('button', { name: 'Dock left: Sandbox', exact: true }).click();
  await window.getByRole('button', { name: 'Save layout', exact: true }).click();
  await expect(pane(notes.id).getByRole('textbox', { name: 'Contents', exact: true })).toHaveValue('A real document draft, retained while editing the layout.');
  await pane(notes.id).getByRole('button', { name: 'Save text', exact: true }).click();
  await expect(pane(notes.id).getByRole('button', { name: 'Save text', exact: true })).toBeDisabled();
  await expect(pane(conversation.id).getByRole('button', { name: 'Send message', exact: true })).toBeVisible();
  await expect(pane(sandbox.id).getByRole('button', { name: 'Settings', exact: true })).toBeVisible();
  await expect(window.getByText('Layout saved', { exact: true })).toBeVisible();
  const saved = (await (await request.get(`/api/nodes/${group.id}`)).json()).config.workspace_layout;
  await page.screenshot({ path: 'test-results/legion-workspace-preview-light.png' });
  await window.getByRole('button', { name: 'Back to canvas', exact: true }).click();
  await expect(window).toHaveCount(0);
  const actualPositions = await Promise.all(cards.map(async card => (await (await request.get(`/api/nodes/${card.id}`)).json()).position));
  expect(actualPositions).toEqual(positions);
  await page.getByRole('button', { name: 'Use dark theme' }).click();
  await openWorkspace(page, group.id);
  await page.screenshot({ path: 'test-results/legion-workspace-preview-dark.png' });
  await page.setViewportSize({ width: 900, height: 700 });
  await expect(window.getByRole('button', { name: 'Back to canvas', exact: true })).toBeInViewport();
  await page.screenshot({ path: 'test-results/legion-workspace-small.png' });
  await page.reload();
  await openWorkspace(page, group.id);
  await expect(window.locator('[data-workspace-pane]')).toHaveCount(3);
  expect((await (await request.get(`/api/nodes/${group.id}`)).json()).config.workspace_layout).toEqual(saved);
  await window.getByRole('button', { name: 'Back to canvas', exact: true }).click();
  const captured = page.waitForResponse(response => response.url().endsWith('/api/legions') && response.request().method() === 'POST');
  await page.locator(`[data-card-id="${group.id}"]`).getByRole('button', { name: 'Save to library', exact: true }).click();
  const template = await (await captured).json();
  // Collection and active decks are independent: explicitly equip the captured
  // blueprint before looking for its placement action in the Legions deck.
  const library = await (await request.get('/api/card-library')).json();
  expect((await request.post('/api/card-library/actions', { data: {
    expected_revision: library.revision, action: 'update_deck', id: 'saved-legions',
    entries: [{ kind: 'legion', id: template.id }],
  } })).ok()).toBe(true);
  // Deploy independently after deleting the source; only the saved blueprint
  // should supply the layout and document, with no original IDs remaining.
  expect((await request.post('/api/nodes/batch-delete', { data: { node_ids: [group.id, ...cards.map(card => card.id)] } })).ok()).toBe(true);
  await expect(page.locator(`[data-card-id="${group.id}"]`)).toHaveCount(0);
  // Refresh the client snapshot after the fixture's direct Library mutation;
  // subsequent deck actions must carry its current optimistic revision.
  await page.reload();
  const deployed = page.waitForResponse(response => response.url().includes(`/api/legions/${template.id}/instances`) && response.request().method() === 'POST');
  await page.getByRole('tab', { name: /Legions/ }).click();
  page.once('dialog', async dialog => {
    expect(dialog.type()).toBe('confirm');
    expect(dialog.message()).toContain('Open these owned packs and continue?');
    await dialog.accept();
  });
  await page.getByRole('button', { name: 'Place Project studio', exact: true }).click();
  const instanceResponse = await deployed;
  expect(instanceResponse.status()).toBe(201);
  const instance = await instanceResponse.json();
  const copy = instance.nodes.find((node: { type: string }) => node.type === 'legion');
  expect(JSON.stringify(copy.config.workspace_layout)).not.toContain(notes.id);
  await openWorkspace(page, copy.id);
  for (const original of [notes, conversation, sandbox]) {
    const node = instance.nodes.find((node: { name: string }) => node.name === original.name);
    await expect(window.locator(`[data-workspace-pane="${node.id}"]`)).toBeVisible();
  }
  expect(errors).toEqual([]);
});

test('compact live workspace resizes and saves ratios without allowing rearrangement', async ({ page, request }) => {
  await page.setViewportSize({ width: 1400, height: 900 });
  const { cards, group } = await setup(request);
  const originalRoot = { kind: 'split', axis: 'horizontal', ratio: .5,
    first: { kind: 'pane', view: { card_id: cards[0].id } }, second: { kind: 'pane', view: { card_id: cards[1].id } } };
  expect((await request.patch(`/api/nodes/${group.id}`, { data: { config: { workspace_layout: { version: 2, root: originalRoot, hidden_sections: [] } } } })).ok()).toBe(true);
  await page.goto('/');
  const window = await openWorkspace(page, group.id);
  const box = await window.boundingBox();
  expect(box!.width).toBeGreaterThanOrEqual(1390);
  expect(box!.height).toBeGreaterThanOrEqual(890);
  // Readable controls keep the application chrome compact without squeezing
  // the shared typography back into the old 26px / 28px strips.
  expect((await window.locator('.legion-window-titlebar').boundingBox())!.height).toBeLessThanOrEqual(46);
  expect((await window.locator('.legion-pane-titlebar').first().boundingBox())!.height).toBeLessThanOrEqual(34);
  await expect(window.getByText('LEGION WORKSPACE', { exact: true })).toHaveCount(0);
  await expect(window.getByRole('button', { name: 'Save layout', exact: true })).toHaveCount(0);
  await expect(window.locator('.legion-pane-titlebar[draggable="true"]')).toHaveCount(0);
  await expect(window.locator('.legion-workspace-tab[draggable="true"]')).toHaveCount(0);
  await expect(window.locator('[data-dock-side]')).toHaveCount(0);
  let saves = 0;
  page.on('request', request => { if (request.method() === 'PATCH' && request.url().endsWith(`/api/nodes/${group.id}`)) saves++; });
  const divider = window.getByRole('separator', { name: 'Resize workspace regions' });
  const bounds = await divider.boundingBox();
  await page.mouse.move(bounds!.x + 2, bounds!.y + 60);
  await page.mouse.down();
  await page.mouse.move(bounds!.x + 100, bounds!.y + 60, { steps: 10 });
  expect(saves).toBe(0);
  await page.mouse.up();
  await expect(window.getByText('Layout saved', { exact: true })).toBeVisible();
  expect(saves).toBe(1);
  const saved = (await (await request.get(`/api/nodes/${group.id}`)).json()).config.workspace_layout;
  expect(saved.root.ratio).toBeGreaterThan(.55);
  expect({ ...saved.root, ratio: .5 }).toEqual(originalRoot);
  // Native pointer cancellation restores the previous size without a write.
  const prior = await divider.getAttribute('aria-valuenow');
  const currentBounds = await divider.boundingBox();
  await page.mouse.move(currentBounds!.x + 2, currentBounds!.y + 60);
  await page.mouse.down();
  await page.mouse.move(currentBounds!.x + 50, currentBounds!.y + 60, { steps: 5 });
  await divider.dispatchEvent('pointercancel', { pointerId: 1 });
  await page.mouse.up();
  expect(saves).toBe(1);
  await page.reload();
  await openWorkspace(page, group.id);
  await expect(divider).toHaveAttribute('aria-valuenow', prior!);
  // Failed automatic saves remain recoverable without exposing an ordinary Save button.
  await page.route(`**/api/nodes/${group.id}`, route => route.request().method() === 'PATCH'
    ? route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"Unavailable"}' }) : route.continue());
  await divider.focus();
  await page.keyboard.press('ArrowLeft');
  await expect(window.getByRole('alert')).toContainText('Your draft is still here');
  await expect(window.getByRole('button', { name: 'Save layout', exact: true })).toHaveCount(0);
  await page.unroute(`**/api/nodes/${group.id}`);
  await window.getByRole('button', { name: 'Retry', exact: true }).click();
  await expect(window.getByRole('alert')).toHaveCount(0);
  await expect(window.getByText('Layout saved', { exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/legion-workspace-compact-live.png' });
});

test('failed saves keep the draft; close can discard; detached panes collapse', async ({ page, request }) => {
  const { cards, group } = await setup(request);
  await page.goto('/');
  const window = await openWorkspace(page, group.id);
  await window.locator(`[data-workspace-source="${cards[0].id}"]`).click();
  await window.getByRole('button', { name: 'Place selected card', exact: true }).click();
  await page.route(`**/api/nodes/${group.id}`, route => route.request().method() === 'PATCH'
    ? route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'Layout save unavailable' }) }) : route.continue());
  await window.getByRole('button', { name: 'Save layout', exact: true }).click();
  await expect(window.getByRole('alert')).toContainText('Your draft is still here');
  await expect(window.locator('[data-workspace-pane]')).toHaveCount(1);
  await page.unroute(`**/api/nodes/${group.id}`);
  await window.getByRole('button', { name: 'Save layout', exact: true }).click();
  await expect(window.getByText('Layout saved', { exact: true })).toBeVisible();
  await window.getByRole('button', { name: 'Edit layout', exact: true }).click();
  await window.getByRole('button', { name: 'Remove Project notes from layout', exact: true }).click();
  await page.keyboard.press('Escape');
  await expect(window.getByRole('button', { name: 'Discard layout and close', exact: true })).toBeVisible();
  await window.getByRole('button', { name: 'Discard layout and close', exact: true }).click();
  await openWorkspace(page, group.id);
  await expect(window.locator('[data-workspace-pane]')).toHaveCount(1);
  expect((await request.patch(`/api/nodes/${cards[0].id}`, { data: { parent_id: null } })).ok()).toBe(true);
  await expect(window.locator('[data-workspace-pane]')).toHaveCount(0);
  await expect(window.getByRole('heading', { name: 'Build your workspace' })).toBeVisible();
});

test('title strips group, reorder and split tabs while preserving live drafts and saved selection', async ({ page, request }) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1400, height: 900 });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const { cards, group } = await setup(request);
  const notes = cards[0];
  const response = await request.post('/api/nodes', { data: { type: 'text', name: 'Scratchpad', parent_id: group.id, position: { x: 600, y: 650 } } });
  expect(response.status()).toBe(201);
  const scratch = await response.json();
  await page.goto('/');
  const window = await openWorkspace(page, group.id);
  const source = (id: string) => window.locator(`[data-workspace-source="${id}"]`);
  const tabs = window.locator('.legion-pane-tabs');
  const panel = (name: string) => window.getByRole('tabpanel', { name, exact: true });
  await source(notes.id).click();
  await window.getByRole('button', { name: 'Place selected card' }).click();
  const header = window.locator('.legion-pane-titlebar');
  const bounds = await header.boundingBox();
  await source(scratch.id).dragTo(header, { targetPosition: { x: bounds!.width - 5, y: 12 } });
  await expect(window.locator('[data-workspace-pane]')).toHaveCount(1);
  await expect(tabs.getByRole('tab')).toHaveText(['Project notes', 'Scratchpad']);
  await window.getByRole('button', { name: 'Done editing' }).click();
  await expect(panel('Scratchpad')).toBeVisible();
  await panel('Scratchpad').getByRole('textbox', { name: 'Contents', exact: true }).fill('Scratch draft survives switching and moving.');
  await tabs.getByRole('tab', { name: 'Project notes', exact: true }).click();
  await expect(window.getByText('Layout saved', { exact: true })).toBeVisible();
  await expect(panel('Scratchpad')).toHaveCount(0);
  await panel('Project notes').getByRole('textbox', { name: 'Contents', exact: true }).fill('Project draft survives switching and moving.');
  await tabs.getByRole('tab', { name: 'Project notes', exact: true }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(panel('Scratchpad').getByRole('textbox', { name: 'Contents', exact: true })).toHaveValue('Scratch draft survives switching and moving.');
  await expect(window.getByText('Layout saved', { exact: true })).toBeVisible();
  await window.getByRole('button', { name: 'Edit layout', exact: true }).click();
  // Reorder by dropping before a tab, then add a third card via the accessible button.
  await tabs.getByRole('tab', { name: 'Scratchpad', exact: true }).dragTo(tabs.getByRole('tab', { name: 'Project notes', exact: true }));
  await expect(tabs.getByRole('tab')).toHaveText(['Scratchpad', 'Project notes']);
  await source(cards[1].id).click();
  await window.getByRole('button', { name: 'Add selected card as tab', exact: true }).click();
  await expect(tabs.getByRole('tab')).toHaveText(['Scratchpad', 'Project notes', 'Conversation']);
  await page.screenshot({ path: 'test-results/legion-tabs-editor.png' });
  // An active tab can be split out of its own group by dropping on a content edge.
  const region = window.locator('[data-workspace-pane]');
  const regionBounds = await region.boundingBox();
  await tabs.getByRole('tab', { name: 'Conversation', exact: true }).dragTo(region,
    { targetPosition: { x: regionBounds!.width - 5, y: regionBounds!.height / 2 } });
  await expect(window.locator('[data-workspace-pane]')).toHaveCount(2);
  const rightHeader = window.locator(`[data-workspace-pane="${cards[1].id}"] .legion-pane-titlebar`);
  const rightBounds = await rightHeader.boundingBox();
  await tabs.getByRole('tab', { name: 'Project notes', exact: true }).dragTo(rightHeader,
    { targetPosition: { x: rightBounds!.width - 5, y: 12 } });
  await window.getByRole('button', { name: 'Save layout', exact: true }).click();
  await expect(panel('Project notes').getByRole('textbox', { name: 'Contents', exact: true })).toHaveValue('Project draft survives switching and moving.');
  await expect(panel('Scratchpad').getByRole('textbox', { name: 'Contents', exact: true })).toHaveValue('Scratch draft survives switching and moving.');
  for (const name of ['Scratchpad', 'Project notes']) {
    await panel(name).getByRole('button', { name: 'Save text', exact: true }).click();
    await expect(panel(name).getByRole('button', { name: 'Save text', exact: true })).toBeDisabled();
  }
  const saved = (await (await request.get(`/api/nodes/${group.id}`)).json()).config.workspace_layout;
  expect(saved.root.second).toEqual({ kind: 'tabs', views: [{ card_id: cards[1].id }, { card_id: notes.id }], active_view: { card_id: notes.id } });
  await page.screenshot({ path: 'test-results/legion-tabs-live-light.png' });
  await window.getByRole('button', { name: 'Back to canvas', exact: true }).click();
  await page.getByRole('button', { name: 'Use dark theme' }).click();
  await page.reload();
  await openWorkspace(page, group.id);
  await expect(tabs.last().getByRole('tab', { name: 'Project notes', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect(panel('Project notes').getByRole('textbox', { name: 'Contents', exact: true })).toHaveValue('Project draft survives switching and moving.');
  await page.screenshot({ path: 'test-results/legion-tabs-live-dark.png' });
  // Removing the active card collapses its tab group to the remaining page.
  expect((await request.patch(`/api/nodes/${notes.id}`, { data: { parent_id: null } })).ok()).toBe(true);
  await expect(tabs.getByRole('tab', { name: 'Project notes', exact: true })).toHaveCount(0);
  await expect(panel('Conversation')).toBeVisible();
  expect(errors).toEqual([]);
});


test('workspace footer preferences stay usable above the modal workspace', async ({ page, request }) => {
  const { cards, group } = await setup(request);
  await request.patch(`/api/nodes/${group.id}`, { data: { config: { workspace_layout: {
    version: 1, root: { kind: 'pane', card_id: cards[0].id },
  } } } });
  await page.goto('/');
  const window = await openWorkspace(page, group.id);
  const footer = page.locator('.legion-workspace .legion-window-footer');
  await expect(footer.getByText('Live workspace', { exact: true })).toBeVisible();
  const preferences = footer.getByRole('group', { name: 'Application preferences' });
  await expect(preferences.getByRole('button')).toHaveCount(3);
  const label = await footer.getByText('Live workspace', { exact: true }).boundingBox();
  const controls = await preferences.boundingBox();
  const tray = await footer.getByRole('navigation').boundingBox();
  expect(controls!.x).toBeGreaterThan(label!.x + label!.width);
  expect(tray!.x).toBeGreaterThan(controls!.x + controls!.width + 10);
  await preferences.getByRole('button', { name: 'Use dark theme' }).click();
  await expect(preferences.getByRole('button', { name: 'Use light theme' })).toBeVisible();
  await preferences.getByRole('button', { name: '切换到中文' }).click();
  await expect(footer.getByText('实时工作区', { exact: true })).toBeVisible();
  await footer.getByRole('button', { name: 'Switch to English' }).click();
  await preferences.getByRole('button', { name: 'Open settings' }).click();
  const settings = page.getByRole('dialog', { name: 'Settings', exact: true });
  await settings.getByRole('button', { name: 'Sandbox', exact: true }).click();
  await expect(settings.getByRole('button', { name: 'Sandbox', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await settings.getByRole('button', { name: 'Close settings' }).press('Escape');
  await expect(settings).toHaveCount(0);
  await expect(window).toBeVisible();
  await page.screenshot({ path: 'test-results/legion-footer-preferences-dark.png' });
  await preferences.getByRole('button', { name: 'Use light theme' }).click();
  await page.screenshot({ path: 'test-results/legion-footer-preferences-light.png' });
});
