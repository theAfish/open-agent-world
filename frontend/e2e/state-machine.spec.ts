import { expect, test, type APIRequestContext, type Locator, type Page } from '@playwright/test';

test.setTimeout(60_000);
test.use({ actionTimeout: 10_000 });

async function createCard(request: APIRequestContext, type: string, name: string, x: number) {
  const response = await request.post('/api/nodes', { data: { type, name, position: { x, y: 340 },
    ...(type === 'agent' ? { config: { system_instruction: 'Preserve this instruction.' } } : {}) } });
  expect(response.status()).toBe(201);
  return response.json();
}

// Sample business states belong to the test fixture, never the editor defaults.
async function defineWorkflow(request: APIRequestContext, node: {id: string; name: string}) {
  const original = await (await request.get(`/api/state-machines/${node.id}`)).json();
  const response = await request.put(`/api/state-machines/${node.id}`, {data: {expected_revision: original.revision, presentation: {}, definition: {
    version: 2, status_entity_id: node.id, entities: [{id: node.id, card_id: node.id, label: node.name, kind: 'card', initial_state: 'planning',
      states: [{id: 'planning', label: 'Planning'}, {id: 'review', label: 'Awaiting review'}]}, ...original.definition.entities.filter((group: {ownership: string}) => group.ownership === 'system')], rules: [],
  }}});
  expect(response.ok()).toBe(true);
}

async function prepare(page: Page, request: APIRequestContext, surfaces: Record<string, string> = {}, locale = 'en') {
  await page.setViewportSize({ width: 1280, height: 800 });
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
      'oaw.locale': locale, 'oaw-theme': 'light', 'oaw-canvas-viewport-v1': null,
      'oaw-active-workspace-v1': null,
      'oaw-node-surfaces-v1': JSON.stringify({ version: 3, state: { surfaceLevels: surfaces } }),
    },
  } })).ok()).toBe(true);
  await page.goto('/');
  await page.locator('.react-flow__controls-fitview').first().click();
}

async function openEditor(page: Page, id: string, locale = 'en', hasStates = true) {
  const title = locale === 'en' ? 'State machine' : '\u72b6\u6001\u673a';
  await page.locator(`[data-card-id="${id}"]`).getByRole('button', { name: title, exact: true }).first().click();
  const editor = page.locator('dialog.sm-dialog');
  await expect(editor).toBeVisible();
  if (hasStates) await expect(editor.locator('.sm-state').first()).toBeInViewport();
  else await expect(editor.getByRole('button', {name: 'Add state', exact: true})).toBeEnabled();
  return editor;
}

// Real pointer selection is essential: selectOption bypasses the popup and would
// miss a menu portaled outside the modal's top layer (the original regression).
async function choose(editor: Locator, select: Locator, label: string) {
  await select.click();
  const menu = editor.getByRole('listbox');
  await expect(menu).toBeVisible();
  await menu.getByRole('option', { name: label, exact: true }).click();
  await expect(menu).toHaveCount(0);
  await expect(select.locator('option:checked')).toHaveText(label);
}

function stateNode(editor: Locator, entityId: string, stateId: string) {
  return editor.locator(`[data-id=${JSON.stringify(`state:${encodeURIComponent(entityId)}:${encodeURIComponent(stateId)}`)}]`).locator('.sm-state');
}

async function connect(editor: Locator, from: Locator, to: Locator) {
  await editor.getByRole('button', { name: 'Connect states', exact: true }).click();
  await from.click(); await to.click();
  await expect(editor.getByRole('complementary', { name: 'Transition editor' })).toBeVisible();
}

async function save(editor: Locator) {
  await editor.getByRole('button', { name: 'Save changes', exact: true }).click();
  await expect(editor.locator('.sm-save-status')).toHaveText('Saved');
  await expect(editor.getByRole('alert')).toHaveCount(0);
}

test.afterEach(async ({ page, request }, testInfo) => {
  if (testInfo.status !== testInfo.expectedStatus && !page.isClosed()) {
    await page.screenshot({ path: `../.outputs/state-machine-failure-${testInfo.title.split(' ')[0]}.png`, fullPage: true });
  }
  const nodes = await (await request.get('/api/nodes')).json();
  if (nodes.length) expect((await request.post('/api/nodes/batch-delete', { data: {
    node_ids: nodes.map((node: { id: string }) => node.id),
  } })).ok()).toBe(true);
});

for (const duplicated of [false, true]) test(`Legacy Agent lifecycle import has one set of SYSTEM states (previously duplicated: ${duplicated})`, async ({page, request}) => {
  const source = await createCard(request, 'agent', 'Lifecycle source', 350);
  const original = await (await request.get(`/api/state-machines/${source.id}`)).json();
  const {card_id: _owner, ...system} = original.definition.entities[0];
  const identity = duplicated ? 'legacy_status' : 'status';
  const legacy = {version: 2, status_entity_id: identity,
    entities: [...duplicated ? [system] : [], {id: identity, label: 'Agent', kind: 'card', initial_state: 'idle', states: system.states}],
    rules: system.projection.map((projection: {event: string; label: string; to_state: string}) => ({
      id: projection.event.replace('agent.', ''), name: projection.label, enabled: true,
      trigger: {entity_id: identity, event: projection.event}, effects: [{entity_id: identity, from_state: '*', to_state: projection.to_state}],
    }))};
  const response = await request.post('/api/nodes', {data: {type: 'agent', name: 'Imported Agent', position: {x: 650, y: 340}, config: {state_machine: legacy}}});
  expect(response.status()).toBe(201);
  const agent = await response.json();
  await prepare(page, request, {[agent.id]: 'inspector'});
  const editor = await openEditor(page, agent.id);
  await expect(editor.locator('.sm-state')).toHaveCount(4);
  await expect(editor.locator('.sm-state.is-system')).toHaveCount(4);
  await expect(editor.locator('.sm-state:not(.is-system)')).toHaveCount(0);
  await expect(editor.locator('.react-flow__node-machineEntity')).toHaveCount(1);
  const positions = await editor.locator('.react-flow__node-machineState').evaluateAll(nodes => nodes.map(node => (node as HTMLElement).style.transform));
  expect(new Set(positions).size).toBe(4);
  const saved = await (await request.get(`/api/state-machines/${agent.id}`)).json();
  expect(saved.definition.entities.map((group: {id: string}) => group.id)).toEqual(['status']);
  expect(saved.definition.rules).toEqual([]);
});

for (const kind of ['agent', 'legion']) test(`New ${kind} opens its real default graph and saves custom states while Sandbox has no editor`, async ({page, request}) => {
  const agent = await createCard(request, 'agent', 'Research agent', 350);
  const sandbox = await createCard(request, 'sandbox', 'Compute only', 1400);
  const owner = kind === 'agent' ? agent : (await (await request.post('/api/legion-groups', {data: {name: 'Research team', node_ids: [agent.id]}})).json())[0];
  await prepare(page, request, {[agent.id]: 'inspector'});
  const sandboxCard = page.locator(`[data-card-id="${sandbox.id}"]`);
  await expect(sandboxCard).toBeVisible();
  await expect(sandboxCard.getByRole('button', {name: 'State machine', exact: true})).toHaveCount(0);
  const editor = await openEditor(page, owner.id);
  const defaults = kind === 'agent' ? ['Idle', 'Running', 'Waiting', 'Error'] : ['Available'];
  await expect(editor.locator('.sm-state')).toHaveCount(defaults.length);
  if (kind === 'agent') {
    await page.screenshot({path: '../.outputs/state-machine-agent-default.png', fullPage: true});
    await expect(editor.locator('.sm-trigger-node,.react-flow__node-machineTrigger')).toHaveCount(0);
    await expect(editor).not.toContainText('Work is running');
    await expect(editor).not.toContainText('Any state');
    await editor.getByRole('group', {name: 'System transition: idle → running', exact: true}).press('Enter');
    await expect(editor.getByText('Canonical runtime transition', {exact: false})).toBeVisible();
    await expect(editor.getByRole('combobox', {name: 'Trigger phase', exact: true})).toHaveCount(0);
    await expect(editor.getByRole('button', {name: 'Delete connection'})).toHaveCount(0);
    await editor.getByText('Runtime fact', {exact: true}).click();
    await expect(editor).toContainText('agent.work_started');
    await expect(editor).not.toContainText('This operation is no longer available.');
    await editor.getByRole('button', {name: 'Close contextual editor'}).click();
  }
  await expect(editor.getByRole('button', {name: 'Save changes'})).toBeDisabled();
  expect((await (await request.get(`/api/state-machines/${owner.id}`)).json()).enabled).toBe(true);
  await editor.getByRole('button', {name: 'Close state machine editor'}).click();
  await expect(editor).toHaveCount(0);
  const reopened = await openEditor(page, owner.id);
  if (kind === 'agent') {
    await expect(reopened.getByRole('button', {name: 'Add state', exact: true})).toBeEnabled();
    await stateNode(reopened, 'status', 'running').click();
    await expect(reopened.getByLabel('State name', {exact: true})).toHaveCount(0);
  }
  await reopened.getByRole('button', {name: 'Add state', exact: true}).click();
  await reopened.getByLabel('State name', {exact: true}).fill('Collecting evidence');
  await reopened.getByRole('button', {name: 'Add state', exact: true}).click();
  await reopened.getByLabel('State name', {exact: true}).fill('Published');
  await reopened.getByLabel('Start here', {exact: true}).check();
  await reopened.getByRole('button', {name: 'Close contextual editor'}).click();
  await connect(reopened, reopened.locator('.sm-state').filter({hasText: 'Collecting evidence'}), reopened.locator('.sm-state').filter({hasText: 'Published'}));
  await save(reopened);
  const saved = (await (await request.get(`/api/state-machines/${owner.id}`)).json()).definition;
  const editedGroup = kind === 'agent' ? saved.entities[1] : saved.entities[0];
  expect(editedGroup.states.map((state: {label: string}) => state.label)).toEqual([...(kind === 'agent' ? [] : defaults), 'Collecting evidence', 'Published']);
  expect(editedGroup.initial_state).toBe(editedGroup.states.at(-1).id);
  expect(saved.rules.length).toBeGreaterThanOrEqual(1);
  await reopened.getByRole('button', {name: 'Apply saved version'}).click();
  await expect(reopened.getByRole('button', {name: 'Apply saved version'})).toHaveCount(0);
  expect((await (await request.get(`/api/state-machines/${owner.id}/runtime`)).json()).instances).toHaveLength(1);
  await reopened.getByRole('button', {name: 'Close state machine editor'}).click();
  await page.reload();
  const restored = await openEditor(page, owner.id);
  await expect(restored.locator('.sm-state')).toHaveCount(defaults.length + 2);
  await expect(restored).not.toContainText('Planning');
  await expect(restored).not.toContainText('Awaiting review');
  await expect(restored.locator('.sm-state.is-initial:not(.is-system)')).toContainText('Published');
});

test('Graph starts uncluttered, new edges stay unconfigured, and preview uses backend semantics', async ({ page, request }) => {
  const agent = await createCard(request, 'agent', 'Review agent', 650);
  await defineWorkflow(request, agent);
  await prepare(page, request, { [agent.id]: 'inspector' });
  const editor = await openEditor(page, agent.id);
  await expect(editor.locator('.sm-state')).toHaveCount(6);
  await expect(editor.locator('.react-flow__node-machineEntity')).toHaveCount(1);
  await expect(editor.locator('.sm-entity-heading:visible')).toHaveCount(0);
  await expect(editor.locator('.sm-inspector,.sm-progress-panel,.sm-navigation-picker')).toHaveCount(0);
  await connect(editor, stateNode(editor, agent.id, 'planning'), stateNode(editor, agent.id, 'review'));
  await expect(editor.locator('.sm-unconfigured')).toBeVisible();
  await expect(editor.getByLabel('Trigger behavior')).toHaveCount(0);
  await expect(editor.getByLabel('From state', { exact: true })).toHaveCount(0);
  await expect(editor.locator('.react-flow__edge.is-unconfigured')).toHaveCount(1);
  await save(editor);
  const draft = await (await request.get(`/api/state-machines/${agent.id}`)).json();
  expect(draft.enabled).toBe(false);
  expect(draft.definition.rules[0]).toMatchObject({ enabled: false, trigger: { event: 'unconfigured' } });
  expect(draft.definition.entities[0].states[0]).not.toHaveProperty('position');
  expect(draft.presentation.positions[agent.id].planning).toBeTruthy();
  expect((await (await request.get(`/api/nodes/${agent.id}`)).json()).config.system_instruction).toBe('Preserve this instruction.');
  await choose(editor, editor.getByRole('combobox', { name: 'Trigger / interface', exact: true }), 'Agent Run · Review agent');
  await choose(editor, editor.getByRole('combobox', { name: 'Trigger phase', exact: true }), 'When a Run starts');
  await expect(editor.locator('.sm-unconfigured')).toHaveCount(0);
  await editor.getByRole('button', { name: 'Condition', exact: true }).click();
  await choose(editor, editor.getByRole('combobox', { name: 'Trigger behavior', exact: true }), 'After N times');
  await editor.getByLabel('Times', { exact: true }).fill('2');
  await save(editor);
  await editor.getByRole('button', { name: 'Simulate', exact: true }).click();
  await editor.getByRole('button', { name: 'Send selected trigger' }).click();
  await expect(editor.getByRole('region', { name: 'Simulation' })).toContainText('expression_false');
  await editor.getByRole('button', { name: 'Send selected trigger' }).click();
  await expect(editor.getByRole('region', { name: 'Simulation' })).toContainText('triggered');
  await expect(stateNode(editor, agent.id, 'review')).toHaveClass(/is-active/);
  expect((await (await request.get(`/api/state-machines/${agent.id}/runtime`)).json()).instances).toHaveLength(1);
  await page.screenshot({ path: '../.outputs/state-machine-progressive.png', fullPage: true });
});

test('Legion navigation lazily opens the same authoritative member definition', async ({ page, request }) => {
  const worker = await createCard(request, 'agent', 'Worker', 350);
  const untouched = await createCard(request, 'agent', 'Unopened member', 1000);
  const outer = (await (await request.post('/api/legion-groups', { data: { name: 'Outer Legion', node_ids: [worker.id, untouched.id] } })).json())[0];
  await defineWorkflow(request, worker);
  await defineWorkflow(request, outer);
  const definitionReads: string[] = [];
  page.on('request', req => { if (req.method() === 'GET' && /\/state-machines\/[^/?]+$/.test(req.url())) definitionReads.push(req.url()); });
  await prepare(page, request);
  const editor = await openEditor(page, outer.id);
  await expect(editor.locator('.sm-state')).toHaveCount(2);
  expect(definitionReads.some(url => url.endsWith(`/${worker.id}`))).toBe(false);
  await editor.getByRole('button', { name: 'Member', exact: true }).click();
  await editor.getByLabel('Search objects').fill('Worker');
  await editor.locator('.sm-picker-results').getByRole('button', { name: 'Worker', exact: true }).click();
  await expect(editor.locator('.sm-state')).toHaveCount(6);
  await stateNode(editor, worker.id, 'planning').click();
  await editor.getByLabel('State name', { exact: true }).fill('Researching');
  await save(editor);
  const own = await (await request.get(`/api/state-machines/${worker.id}`)).json();
  expect(own.definition.entities[0].states[0].label).toBe('Researching');
  const parent = await (await request.get(`/api/state-machines/${outer.id}`)).json();
  expect(parent.definition.entities).toHaveLength(1);
  expect(parent.definition.entities[0].card_id).toBe(outer.id);
  expect(definitionReads.some(url => url.endsWith(`/${untouched.id}`))).toBe(false);
  await editor.getByRole('button', { name: 'Close state machine editor', exact: true }).click();
  await page.reload();
  const reopened = await openEditor(page, outer.id);
  await expect(reopened.locator('.sm-state')).toHaveCount(2);
});

test('Unknown catalog interfaces show only registered invocation phases', async ({ page, request }) => {
  const agent = await createCard(request, 'agent', 'Plugin user', 650);
  await defineWorkflow(request, agent);
  await page.route(`**/api/state-machines/events?card_id=${agent.id}`, async route => {
    const response = await route.fetch(), data = await response.json();
    data.sources.push({ id: 'opaque-vendor-source', kind: 'capability', operation_id: 'vendor:opaque', label: 'Publish lunar atlas', capability: 'vendor.lunar_atlas', target_card_id: agent.id, target_name: 'Plugin user', default_event: 'vendor.returned', events: [
      { key: 'vendor.returned', label: 'Invocation returned', category: 'vendor', outcome: 'returned', runtime_bound: true },
      { key: 'vendor.failed', label: 'Invocation failed', category: 'vendor', outcome: 'failed', runtime_bound: true },
    ] });
    await route.fulfill({ response, json: data });
  });
  await prepare(page, request, { [agent.id]: 'inspector' });
  const editor = await openEditor(page, agent.id);
  await connect(editor, stateNode(editor, agent.id, 'planning'), stateNode(editor, agent.id, 'review'));
  await choose(editor, editor.getByRole('combobox', { name: 'Trigger / interface', exact: true }), 'Publish lunar atlas · Plugin user');
  const phase = editor.getByRole('combobox', { name: 'Trigger phase', exact: true });
  await expect(phase.locator('option')).toHaveText(['Invocation returned', 'Invocation failed']);
  await save(editor);
  expect((await (await request.get(`/api/state-machines/${agent.id}`)).json()).definition.rules[0].trigger).toMatchObject({ event: 'vendor.returned', capability: 'vendor.lunar_atlas', operation_id: 'vendor:opaque' });
});

test('Expanded member graphs keep template-local IDs separate and edit their own definitions', async ({ page, request }) => {
  const left = await createCard(request, 'agent', 'Planner', 300), right = await createCard(request, 'agent', 'Reviewer', 900);
  for (const member of [left, right]) {
    const original = await (await request.get(`/api/state-machines/${member.id}`)).json();
    const saved = await request.put(`/api/state-machines/${member.id}`, { data: { expected_revision: original.revision, presentation: {}, definition: { version: 2, status_entity_id: 'self', entities: [{ id: 'self', card_id: member.id, label: member.name, kind: 'card', initial_state: 'planning', states: [{id:'planning',label:'Planning'}, {id:'review',label:'Awaiting review'}] }, ...original.definition.entities], rules: [] } } });
    expect(saved.ok()).toBe(true);
  }
  const legion = (await (await request.post('/api/legion-groups', { data: { name: 'Reference Legion', node_ids: [left.id, right.id] } })).json())[0];
  await defineWorkflow(request, legion);
  await prepare(page, request);
  const editor = await openEditor(page, legion.id);
  await editor.getByRole('button', { name: 'Member', exact: true }).click();
  for (const name of ['Planner', 'Reviewer']) await editor.locator('.sm-member-reference').filter({ hasText: name }).getByRole('button', { name: 'Show states' }).click();
  await editor.getByRole('button', { name: 'Close picker' }).click();
  await editor.locator('.react-flow__controls-fitview').click();
  await expect(editor.locator('.sm-state')).toHaveCount(14);
  await expect(editor.locator('.react-flow__node-machineEntity')).toHaveCount(3);
  const leftAlias = `ref:${left.id}:self`, rightAlias = `ref:${right.id}:self`;
  await connect(editor, stateNode(editor, leftAlias, 'planning'), stateNode(editor, leftAlias, 'review'));
  await save(editor);
  expect((await (await request.get(`/api/state-machines/${left.id}`)).json()).definition.rules).toHaveLength(1);
  expect((await (await request.get(`/api/state-machines/${legion.id}`)).json()).definition.rules).toHaveLength(0);
  await editor.getByRole('button', { name: 'Close contextual editor' }).click();
  await connect(editor, stateNode(editor, leftAlias, 'planning'), stateNode(editor, rightAlias, 'review'));
  await save(editor);
  const saved = (await (await request.get(`/api/state-machines/${legion.id}`)).json()).definition;
  expect(saved.entities).toHaveLength(1);
  expect(saved.references.map((ref: {card_id:string}) => ref.card_id).sort()).toEqual([left.id, right.id].sort());
  expect(saved.references.every((ref: {state_group_id:string}) => ref.state_group_id === 'self')).toBe(true);
  expect(new Set(saved.references.map((ref: {entity_id:string}) => ref.entity_id)).size).toBe(2);
});


test('Closing only warns for changed definitions, not viewing or layout changes', async ({page, request}) => {
  const agent = await createCard(request, 'agent', 'Close behavior', 650);
  await defineWorkflow(request, agent);
  await prepare(page, request, {[agent.id]: 'inspector'});
  let editor = await openEditor(page, agent.id);
  await editor.getByRole('button', {name: 'Close state machine editor'}).click();
  await expect(editor).toHaveCount(0);
  editor = await openEditor(page, agent.id);
  await editor.locator('.react-flow__controls-zoomin').click();
  const planning = stateNode(editor, agent.id, 'planning');
  const bounds = (await planning.boundingBox())!;
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
  await page.mouse.down();
  await page.mouse.move(bounds.x + bounds.width / 2 + 40, bounds.y + bounds.height / 2 + 35, {steps: 10});
  await page.mouse.up();
  await expect(editor.locator('.sm-save-status')).toHaveText('Unsaved changes');
  await editor.getByRole('button', {name: 'Close state machine editor'}).click();
  await expect(editor).toHaveCount(0);
  editor = await openEditor(page, agent.id);
  await stateNode(editor, agent.id, 'planning').click();
  await editor.getByLabel('State name', {exact: true}).fill('Renamed');
  await editor.getByRole('button', {name: 'Close state machine editor'}).click();
  await expect(editor.getByRole('button', {name: 'Keep editing', exact: true})).toBeVisible();
  await editor.getByRole('button', {name: 'Keep editing', exact: true}).click();
  await editor.getByLabel('State name', {exact: true}).fill('Planning');
  await editor.getByRole('button', {name: 'Close state machine editor'}).click();
  await expect(editor).toHaveCount(0);
});

test('Boundary gestures preview connections, connect states and support self-links', async ({page, request}) => {
  const agent = await createCard(request, 'agent', 'Boundary Agent', 650);
  await defineWorkflow(request, agent);
  await prepare(page, request, {[agent.id]: 'inspector'});
  const editor = await openEditor(page, agent.id);
  const planning = stateNode(editor, agent.id, 'planning'), review = stateNode(editor, agent.id, 'review');
  const bounds = (await planning.boundingBox())!;
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
  await expect(planning).not.toHaveAttribute('data-connection-hot', 'true');
  const start = {x: bounds.x + bounds.width - 3, y: bounds.y + bounds.height / 2};
  await page.mouse.move(start.x, start.y);
  await expect(planning).toHaveAttribute('data-connection-hot', 'true');
  await expect(planning.locator('.connection-hover-hint')).toHaveCSS('opacity', '0.65');
  await page.mouse.down();
  const target = (await review.boundingBox())!;
  await page.mouse.move(target.x + target.width / 2, target.y + target.height / 2, {steps: 15});
  // A body drop target must project the preview back onto the circle, not draw
  // through the label to the full-surface handle's center.
  const preview = editor.locator('.sm-connection-preview');
  await expect(preview).toBeAttached();
  const endpoint = await preview.evaluate(element => {
    const path = element as SVGPathElement;
    const point = path.getPointAtLength(path.getTotalLength());
    return new DOMPoint(point.x, point.y).matrixTransform(path.getScreenCTM()!).toJSON();
  });
  expect(Math.hypot(endpoint.x - target.x - target.width / 2, endpoint.y - target.y - target.height / 2)).toBeCloseTo(target.width / 2, 0);
  await page.mouse.up();
  await expect(editor.getByRole('complementary', {name: 'Transition editor'})).toBeVisible();
  await editor.getByRole('button', {name: 'Close contextual editor'}).click();
  await expect(editor.locator('.react-flow__edge.is-unconfigured')).toHaveCount(1);
  const self = (await planning.boundingBox())!;
  await page.mouse.move(self.x + self.width - 3, self.y + self.height / 2);
  await expect(planning).toHaveAttribute('data-connection-hot', 'true');
  await page.mouse.down();
  await page.mouse.move(self.x + self.width + 35, self.y + self.height / 2, {steps: 5});
  await expect(planning.locator('.connection-drop-surface')).toHaveAttribute('data-active', 'true');
  await page.mouse.move(self.x + self.width / 2, self.y + self.height / 2, {steps: 5});
  const loopPreview = await preview.evaluate(element => {
    const path = element as SVGPathElement, length = path.getTotalLength();
    return Array.from({length: 21}, (_, i) => {
      const point = path.getPointAtLength(length * i / 20);
      return new DOMPoint(point.x, point.y).matrixTransform(path.getScreenCTM()!).toJSON();
    });
  });
  for (const point of loopPreview) expect(Math.hypot(point.x - self.x - self.width / 2, point.y - self.y - self.height / 2)).toBeGreaterThan(self.width / 2 - 1);
  await page.mouse.up();
  await expect(editor.getByRole('complementary', {name: 'Transition editor'})).toBeVisible();
  await editor.getByRole('button', {name: 'Close contextual editor'}).click();
  await expect(editor.locator('.react-flow__edge.is-unconfigured')).toHaveCount(2);
});

test('Unified owner canvas keeps self-loops outside states and moves edge anchors with dragged nodes', async ({page, request}) => {
  const agent = await createCard(request, 'agent', 'Unified Agent', 650);
  await defineWorkflow(request, agent);
  await prepare(page, request, {[agent.id]: 'inspector'});
  const editor = await openEditor(page, agent.id);
  await expect(editor.locator('.react-flow__node-machineEntity')).toHaveCount(1);
  const planning = stateNode(editor, agent.id, 'planning'), review = stateNode(editor, agent.id, 'review');
  await connect(editor, planning, planning);
  await editor.getByRole('button', {name: 'Close contextual editor'}).click();
  const loop = editor.locator('.react-flow__edge.is-unconfigured').first();
  const start = loop.locator('[data-endpoint="source"]'), end = loop.locator('[data-endpoint="target"]');
  expect(Number(await start.getAttribute('cx'))).toBeGreaterThan(Number(await end.getAttribute('cx')));
  await connect(editor, planning, review);
  await editor.getByRole('button', {name: 'Close contextual editor'}).click();
  await connect(editor, review, planning);
  await editor.getByRole('button', {name: 'Close contextual editor'}).click();
  const edges = editor.locator('.react-flow__edge.is-unconfigured .react-flow__edge-path');
  await expect(edges).toHaveCount(3);
  await expect.poll(() => edges.evaluateAll(paths => new Set(paths.map(path => path.getAttribute('d'))).size)).toBe(3);
  const before = await edges.evaluateAll(paths => paths.map(path => path.getAttribute('d')));
  expect(new Set(before).size).toBe(3);
  const bounds = (await planning.boundingBox())!;
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
  await page.mouse.down();
  await page.mouse.move(bounds.x + bounds.width / 2 + 90, bounds.y + bounds.height / 2 + 80, {steps: 12});
  await page.mouse.up();
  await expect.poll(() => edges.evaluateAll(paths => paths.map(path => path.getAttribute('d')))).not.toEqual(before);
  await save(editor);
  const saved = await (await request.get(`/api/state-machines/${agent.id}`)).json();
  expect(saved.presentation.coordinate_space).toBe('owner');
  const positions = saved.presentation.positions;
  await editor.getByRole('button', {name: 'Close state machine editor'}).click();
  const reopened = await openEditor(page, agent.id);
  await expect(reopened.locator('.react-flow__node-machineEntity')).toHaveCount(1);
  await expect(reopened.locator('.react-flow__edge.is-unconfigured')).toHaveCount(3);
  expect((await (await request.get(`/api/state-machines/${agent.id}`)).json()).presentation.positions).toEqual(positions);
  await page.screenshot({path: '../.outputs/state-machine-unified.png', fullPage: true});
});

test('Applied editor changes control the live card after a real Agent Run', async ({page, request}) => {
  const agent = await createCard(request, 'agent', 'Custom lifecycle', 650);
  const initial = await (await request.get(`/api/state-machines/${agent.id}`)).json();
  initial.definition.entities.unshift({id: 'workflow', card_id: agent.id, label: 'Workflow', kind: 'group', ownership: 'user', initial_state: 'new', states: [{id: 'new', label: 'New'}, {id: 'signoff', label: 'Sign-off'}]});
  initial.definition.status_entity_id = 'workflow';
  initial.definition.rules = [{id: 'signoff', name: 'Sign-off', enabled: true, trigger: {entity_id: 'status', state_id: 'idle', event: 'state.entered'}, effects: [{entity_id: 'workflow', from_state: '*', to_state: 'signoff'}]}];
  expect((await request.put(`/api/state-machines/${agent.id}`, {data: {
    definition: initial.definition, expected_revision: initial.revision,
  }})).ok()).toBe(true);
  await prepare(page, request, {[agent.id]: 'inspector'});
  const editor = await openEditor(page, agent.id);
  await stateNode(editor, 'workflow', 'signoff').click();
  await editor.getByLabel('State name', {exact: true}).fill('Awaiting sign-off');
  await save(editor);
  await editor.getByRole('button', {name: 'Apply saved version'}).click();
  await expect(editor.getByRole('button', {name: 'Apply saved version'})).toHaveCount(0);
  await editor.getByRole('button', {name: 'Close state machine editor'}).click();
  expect((await request.post(`/api/agents/${agent.id}/run`, {data: {prompt: 'Finish this task'}})).status()).toBe(202);
  await expect.poll(async () => (await (await request.get(`/api/nodes/${agent.id}`)).json()).primary_state).toBe('signoff');
  expect((await (await request.get(`/api/nodes/${agent.id}`)).json()).operational_status).toBe('idle');
  const face = page.locator(`[data-card-id="${agent.id}"]`);
  await expect(face.locator('.card-status')).toContainText('Awaiting sign-off');
  await page.reload();
  await expect(face.locator('.card-status')).toContainText('Awaiting sign-off');
  const reopened = await openEditor(page, agent.id);
  await expect(stateNode(reopened, 'workflow', 'signoff')).toHaveClass(/is-active/);
  await page.screenshot({path: '../.outputs/state-machine-applied.png', fullPage: true});
});

test('System anchors create reactions and dashed command requests on the same canvas', async ({page, request}) => {
  const agent = await createCard(request, 'agent', 'Anchor user', 650);
  await defineWorkflow(request, agent);
  await prepare(page, request, {[agent.id]: 'inspector'});
  const editor = await openEditor(page, agent.id);
  await expect(editor.locator('.sm-state')).toHaveCount(6);
  await expect(editor.locator('.sm-trigger-node')).toHaveCount(0);
  await expect(editor.getByRole('button', {name: 'State references', exact: true})).toHaveCount(0);
  await stateNode(editor, 'status', 'running').click();
  await expect(editor.getByLabel('State name', {exact: true})).toHaveCount(0);
  await expect(editor.getByRole('button', {name: 'Delete state'})).toHaveCount(0);
  await editor.getByRole('button', {name: 'Close contextual editor'}).click();
  await connect(editor, stateNode(editor, 'status', 'running'), stateNode(editor, agent.id, 'review'));
  await expect(editor.getByLabel('State phase', {exact: true})).toHaveValue('state.entered');
  await expect(editor.getByRole('combobox', {name: 'Trigger / interface'})).toHaveCount(0);
  await save(editor);
  await editor.getByRole('button', {name: 'Close contextual editor'}).click();
  await connect(editor, stateNode(editor, agent.id, 'planning'), stateNode(editor, 'status', 'running'));
  await expect(editor.getByLabel('Via', {exact: true})).toHaveValue('start_work');
  await editor.getByLabel('Input', {exact: true}).fill('Research from approval');
  await save(editor);
  const definition = (await (await request.get(`/api/state-machines/${agent.id}`)).json()).definition;
  expect(definition.rules[0].trigger).toMatchObject({entity_id: 'status', state_id: 'running', event: 'state.entered'});
  expect(definition.rules[1].effects).toEqual([]);
  expect(definition.rules[1].command).toMatchObject({entity_id: 'status', state_id: 'running', command_id: 'start_work'});
  await page.screenshot({path: '../.outputs/state-machine-system-command.png', fullPage: true});
  await editor.getByRole('button', {name: 'Close contextual editor'}).click();
  await expect(editor.locator('.react-flow__edge.is-command .react-flow__edge-text')).toHaveText('Start work');
  await expect(editor.locator('.react-flow__edge.is-command .react-flow__edge-text')).toBeVisible();
  await page.screenshot({path: '../.outputs/state-machine-orchestration.png', fullPage: true});
  await expect(editor.locator('.sm-state.is-system')).toHaveCount(4);
  await editor.getByRole('button', {name: 'Close state machine editor'}).click();
  const reopened = await openEditor(page, agent.id);
  await expect(reopened.locator('.sm-state')).toHaveCount(6);
  await expect(reopened.locator('.sm-state.is-system')).toHaveCount(4);
});
