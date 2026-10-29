import { expect, test, type Page } from "@playwright/test";

test('Minister streaming updates stay in one bubble', async ({ page, request }) => {
  const created = await request.post('/api/nodes', { data: {
    type: 'agent', minister: {}, name: 'Stream check', position: { x: 350, y: 350 }, size: { width: 96, height: 96 },
  } });
  expect(created.ok()).toBeTruthy();
  await page.goto('/');
  await openChat(page, 'Stream check');
  await page.getByRole('textbox', { name: 'Message Stream check', exact: true }).fill('stream');
  await page.getByRole('button', { name: 'Send to Stream check', exact: true }).click();
  const bubbles = page.locator('.minister-bubble-slot .minister-message.is-agent');
  await expect(bubbles).toHaveCount(1);
  const id = await bubbles.first().getAttribute('data-message-id');
  await expect(bubbles).toHaveText('Stream checkInspecting local resources.');
  await expect(page.getByText('Minister is working…', { exact: true })).toHaveCount(0);
  await expect(bubbles).toHaveCount(1);
  await expect(bubbles).toHaveAttribute('data-message-id', id!);
  await page.screenshot({ path: 'test-results/minister-stream-single-bubble.png' });
});

test('Minister visual observation returns real masked pixels without moving the viewport', async ({ page, request }) => {
  const minister = await (await request.post('/api/nodes', { data: {
    type: 'agent', minister: { control_radius: 500 }, name: 'Observer', position: { x: 350, y: 300 }, size: { width: 96, height: 96 },
  } })).json();
  const note = await (await request.post('/api/nodes', { data: {
    type: 'text', name: 'Visible note', content: 'PRIVATE BODY MUST NOT BE CAPTURED', position: { x: 500, y: 300 }, size: { width: 220, height: 140 },
  } })).json();
  let capture: { data_base64: string; captured_ids: string[] } | undefined;
  page.on('websocket', socket => {
    if (!socket.url().endsWith('/ws/visual')) return;
    socket.on('framesent', frame => {
      const data = JSON.parse(String(frame.payload));
      if (data.data_base64) capture = data;
    });
  });
  await page.goto('/');
  const node = page.locator(`.world-card[data-card-id="${note.id}"]`);
  await expect(node).toBeVisible();
  await node.evaluate(element => {
    const secret = document.createElement('div');
    secret.dataset.observationPrivate = 'true';
    secret.style.cssText = 'position:absolute;inset:0;background:rgb(255,0,0)';
    secret.textContent = 'PRIVATE';
    element.appendChild(secret);
  });
  const before = await page.locator('.react-flow__viewport').first().getAttribute('style');
  await openChat(page, 'Observer');
  await page.getByRole('textbox', { name: 'Message Observer', exact: true }).fill('observe');
  await page.getByRole('button', { name: 'Send to Observer', exact: true }).click();
  await expect.poll(() => capture?.captured_ids, { timeout: 20000 }).toContain(note.id);
  await expect(page.getByText(/Observed canvas image:/)).toBeVisible();
  expect(await page.locator('.react-flow__viewport').first().getAttribute('style')).toBe(before);
  const pixels = await page.evaluate(async encoded => {
    const image = new Image(); image.src = 'data:image/png;base64,' + encoded; await image.decode();
    const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
    const context = canvas.getContext('2d')!; context.drawImage(image, 0, 0);
    const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
    const colors = new Set<string>(); let red = 0;
    for (let i = 0; i < data.length; i += 4) {
      colors.add(`${data[i]},${data[i+1]},${data[i+2]}`);
      if (data[i] === 255 && data[i+1] === 0 && data[i+2] === 0) red++;
    }
    return { width: image.width, colors: colors.size, red };
  }, capture!.data_base64);
  expect(pixels.width).toBe(1600); expect(pixels.colors).toBeGreaterThan(10); expect(pixels.red).toBe(0);
  const { writeFile } = await import('node:fs/promises');
  await writeFile('test-results/minister-observation.png', Buffer.from(capture!.data_base64, 'base64'));
  await page.screenshot({ path: 'test-results/minister-observation-source.png' });
  await request.delete(`/api/nodes/${minister.id}`);
});

async function openChat(page: Page, name: string) {
  const close = page.getByRole('button', { name: `Close ${name} inspector`, exact: true });
  if (await close.isVisible()) {
    await close.press('Enter');
  }
  await page.getByRole('button', { name: `Open Minister ${name}`, exact: true }).hover();
  await expect(page.getByRole('textbox', { name: `Message ${name}`, exact: true })).toBeVisible();
}
async function openPermissions(page: Page, name: string) {
  await openChat(page, name);
  await page.getByRole('button', { name: 'Minister settings', exact: true }).click();
  await expect(page.getByRole('tab', { name: 'Minister', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('.minister-panel, .minister-manage')).toHaveCount(0);
}
async function closeChat(page: Page) {
  const input = page.locator('.minister-presence:not([hidden]) textarea');
  if (await input.isVisible()) await input.press('Escape');
  await page.mouse.move(20, 20);
  await expect(page.locator('.minister-presence:not([hidden])')).toHaveCount(0);
}

// Run against backend.tests.minister_app for deterministic, real tool invocations.
test.skip(process.env.OAW_MINISTER_E2E !== "1", "Use npm run test:e2e:minister for the isolated tool-using provider");

test.beforeEach(async ({ request }) => {
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation,
    changes: { 'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
      'oaw.locale': 'en', 'oaw-node-surfaces-v1': null, 'oaw-canvas-viewport-v1': null, 'oaw-theme': null } } });
});

test("an Agent is placed from the deck, promoted by dragging, restored and revoked", async ({ page, request }) => {
  const originalIds = new Set((await (await request.get('/api/nodes')).json()).map((node: { id: string }) => node.id));
  await page.goto('/');
  await page.getByRole('button', { name: 'Open Pack and Card Library' }).click();
  const library = page.getByRole('dialog', { name: 'Pack & Card Library' });
  const pack = library.getByRole('article', { name: 'Core essentials', exact: true });
  const collection = await (await request.get('/api/card-library')).json();
  const opened = Object.values(collection.packs).some((value: any) => value.definition.name === 'Core essentials' && value.opened);
  if (!opened) { await pack.locator('.pack-touch-area').focus(); await page.keyboard.press('Enter'); }
  await pack.getByRole('button', { name: 'View cards in Core essentials' }).click();
  const decks = library.getByRole('complementary', { name: 'Deck destinations' });
  await decks.getByRole('textbox', { name: 'New deck name' }).fill('Minister practice');
  await decks.getByRole('button', { name: 'Create deck', exact: true }).click();
  await library.getByLabel('Search cards', { exact: true }).fill('Agent');
  await library.getByRole('button', { name: 'Add Agent to deck', exact: true }).click();
  await expect(page.locator('.component-palette [data-palette-card]')).toHaveCount(1);
  await library.getByLabel('Search cards', { exact: true }).fill('Minister role');
  await library.getByRole('button', { name: 'Add Minister role to deck', exact: true }).click();
  await expect(page.locator('.component-palette [data-palette-card]')).toHaveCount(2);
  await library.getByRole('button', { name: 'Close Library' }).click();
  const tray = page.getByRole('complementary', { name: 'Active card deck' });
  await tray.hover();
  await tray.getByRole('button', { name: 'Place Agent', exact: true }).dragTo(page.locator('.react-flow__pane').first(), { targetPosition: { x: 350, y: 230 } });
  const card = (await (await request.get('/api/nodes')).json()).find((node: { id: string; type: string }) => node.type === 'agent' && !originalIds.has(node.id));
  let sourceId: string | undefined;
  try {
    expect(card.minister).toBeNull();
    const node = page.locator(`[data-card-id="${card.id}"]`);
    await node.locator('.card-kind-icon').click();
    await expect(node.getByRole('button', { name: 'Appoint as Minister', exact: true })).toHaveCount(0);
    await node.getByRole('button', { name: /^Close .* inspector$/ }).click();
    await tray.hover();
    await tray.getByRole('button', { name: 'Place Minister role', exact: true }).dragTo(page.locator('.react-flow__pane').first(), { targetPosition: { x: 850, y: 280 } });
    const role = (await (await request.get('/api/nodes')).json()).find((item: { type: string }) => item.type === 'core.minister-role');
    sourceId = role.id;
    const source = page.locator(`[data-card-id="${sourceId}"]`);
    await expect(source.getByText('Drag onto an Agent', { exact: true })).toBeVisible();
    await expect(page.getByText('Drag this role card onto an Agent to appoint it as Minister.', { exact: true }).first()).toBeVisible();
    await page.mouse.move(700, 150);
    await expect.poll(() => page.locator('.react-flow__viewport').evaluate(element => element.getAnimations().some(animation => animation.playState === 'running'))).toBe(false);
    await page.screenshot({ path: 'test-results/minister-role-card-light.png' });
    await page.getByRole('button', { name: 'Use dark theme' }).click();
    await page.screenshot({ path: 'test-results/minister-role-card-dark.png' });
    await page.getByRole('button', { name: 'Use light theme' }).click();
    const from = (await source.locator('.card-kind-icon').boundingBox())!, to = (await node.boundingBox())!;
    await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
    await page.mouse.down();
    await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 15 });
    await page.mouse.up();
    await expect(node).toHaveAttribute('data-minister', 'true');
    await expect(node).toHaveAttribute('data-promotion', 'appointing');
    await page.screenshot({ path: 'test-results/minister-promotion-event.png' });
    await expect(source).toHaveCount(0);
    expect((await request.get(`/api/nodes/${sourceId}`)).status()).toBe(404);
    const promoted = await (await request.get(`/api/nodes/${card.id}`)).json();
    expect(promoted.type).toBe(card.type); expect(promoted.config).toEqual(card.config);
    expect(promoted.minister).toEqual({ control_radius: 1200, allow_canvas_edits: true });
    await expect.poll(async () => (await (await request.get('/api/application')).json()).values['oaw-minister-role-learned']).toBe('true');
    await expect(node).not.toHaveAttribute('data-promotion');
    await expect(node).toHaveAttribute('data-surface-level', 'node');
    const crown = page.locator(`[data-minister-for="${card.id}"] .minister-badge`);
    await expect(crown.locator('span')).toHaveCount(0);
    await expect.poll(async () => {
      const a = (await node.boundingBox())!, b = (await crown.boundingBox())!;
      return Math.abs(a.x + a.width / 2 - b.x - b.width / 2) < 1 && b.width < a.width / 3;
    }).toBe(true);
    await page.screenshot({ path: 'test-results/minister-promotion-light.png' });
    await page.reload();
    await expect(node).toHaveAttribute('data-minister', 'true');
    await expect(node).not.toHaveAttribute('data-promotion');
    await page.getByRole('button', { name: 'Use dark theme' }).click();
    await page.screenshot({ path: 'test-results/minister-promotion-dark.png' });
    if (!await node.getByRole('button', { name: 'Remove Minister role', exact: true }).isVisible()) await node.locator('.card-kind-icon').click();
    await node.getByRole('tab', { name: 'Minister', exact: true }).click();
    await expect(node.getByText('Keep this Agent, its model, tools and history. Only Minister permissions are removed.')).toBeVisible();
    const remove = node.getByRole('button', { name: 'Remove Minister role', exact: true });
    await remove.scrollIntoViewIfNeeded();
    expect((await remove.boundingBox())!.height).toBeGreaterThanOrEqual(40);
    await page.screenshot({ path: 'test-results/minister-permissions-dark.png' });
    await page.getByRole('button', { name: 'Use light theme' }).click();
    await page.screenshot({ path: 'test-results/minister-permissions-light.png' });
    await node.getByRole('button', { name: 'Remove Minister role', exact: true }).click();
    await expect(node).not.toHaveAttribute('data-minister');
    await expect(node.getByRole('tab', { name: 'Minister', exact: true })).toHaveCount(0);
    expect((await (await request.get(`/api/nodes/${card.id}`)).json()).config).toEqual(card.config);
  } finally {
    await request.delete(`/api/nodes/${card.id}`);
    if (sourceId) await request.delete(`/api/nodes/${sourceId}`);
  }
});

test('Codex keeps its own identity and configuration when a role card is dropped from the deck', async ({ page, request }) => {
  const response = await request.post('/api/nodes', { data: { type: 'openai.codex.agent', name: 'My Codex', position: { x: 360, y: 280 } } });
  expect(response.ok()).toBeTruthy();
  const card = await response.json();
  try {
    await page.goto('/');
    const node = page.locator(`[data-card-id="${card.id}"]`);
    await page.getByRole('button', { name: 'Open Pack and Card Library' }).click();
    const library = page.getByRole('dialog', { name: 'Pack & Card Library' });
    const pack = library.getByRole('article', { name: 'Core essentials', exact: true });
    const collection = await (await request.get('/api/card-library')).json();
    if (!Object.values(collection.packs).some((value: any) => value.definition.name === 'Core essentials' && value.opened)) {
      await pack.locator('.pack-touch-area').focus(); await page.keyboard.press('Enter');
    }
    await pack.getByRole('button', { name: 'View cards in Core essentials' }).click();
    const decks = page.getByRole('complementary', { name: 'Active card deck' });
    await decks.getByRole('button', { name: 'Create a new card deck' }).click();
    await decks.getByLabel('Deck name', { exact: true }).fill('Codex roles');
    await decks.getByRole('button', { name: 'Create deck', exact: true }).click();
    await library.getByLabel('Search cards', { exact: true }).fill('Minister role');
    await library.getByRole('button', { name: 'Add Minister role to deck', exact: true }).click();
    await library.getByRole('button', { name: 'Close Library' }).click();
    const tray = page.getByRole('complementary', { name: 'Active card deck' });
    await tray.hover();
    await tray.getByRole('button', { name: 'Place Minister role', exact: true }).dragTo(node);
    await expect(node).toHaveAttribute('data-minister', 'true');
    await expect(node).toHaveAttribute('data-card-type', 'openai.codex.agent');
    expect((await (await request.get(`/api/nodes/${card.id}`)).json()).config).toEqual(card.config);
    await expect(node).not.toHaveAttribute('data-promotion');
    await expect(page.getByRole('button', { name: 'Open Minister My Codex' })).toBeVisible();
    await page.screenshot({ path: 'test-results/minister-codex.png' });
    await page.reload();
    await expect(node).toHaveAttribute('data-minister', 'true');
    await expect(node).not.toHaveAttribute('data-promotion');
  } finally { await request.delete(`/api/nodes/${card.id}`); }
});

test("Minister permissions live in the Agent card and history uses its workspace", async ({ page, context, request }) => {
  const stamp = Date.now();
  const id = `minister-ui-${stamp}`, noteId = `minister-note-${stamp}`, farId = `minister-far-${stamp}`;
  for (const data of [
    { id, type: 'agent', name: 'Garden Minister', position: { x: 320, y: 350 }, minister: { control_radius: 400, allow_canvas_edits: false } },
    { id: noteId, type: 'text', name: 'Near note', position: { x: 100, y: 350 } },
    { id: farId, type: 'text', name: 'Far note', position: { x: 1800, y: 800 } },
  ]) expect((await request.post('/api/nodes', { data: { ...data, size: { width: 96, height: 96 } } })).ok()).toBeTruthy();
  const observer = await context.newPage();
  try {
    await page.goto('/'); await observer.goto('/');
    await openPermissions(page, 'Garden Minister');
    const node = page.locator(`[data-card-id="${id}"]`);
    const permissions = node.getByRole('region', { name: 'Minister permissions' });
    const radius = permissions.getByRole('spinbutton', { name: 'Control radius' });
    await radius.fill('300'); await radius.press('Enter');
    await expect.poll(async () => (await (await request.get(`/api/nodes/${id}`)).json()).minister.control_radius).toBe(300);
    await permissions.locator('summary').filter({ hasText: 'Nearby cards' }).click();
    await permissions.getByRole('textbox', { name: 'Search nearby cards' }).fill('note');
    await expect(permissions.getByRole('button', { name: /Near note/ })).toBeVisible();
    await expect(permissions.getByRole('button', { name: /Far note/ })).toHaveCount(0);
    await permissions.getByRole('checkbox', { name: 'Allow canvas edits' }).check();
    await expect.poll(async () => (await (await request.get(`/api/nodes/${id}`)).json()).minister.allow_canvas_edits).toBe(true);
    await openChat(page, 'Garden Minister');
    await page.getByRole('textbox', { name: 'Message Garden Minister' }).fill(`rename ${noteId} Minister renamed this`);
    await page.getByRole('button', { name: 'Send to Garden Minister' }).click();
    await expect(page.getByRole('log', { name: 'Garden Minister conversation' })).toContainText('Renamed the card.');
    await expect(observer.locator(`[data-card-id="${noteId}"]`)).toContainText('Minister renamed this');
    await openPermissions(page, 'Garden Minister');
    await permissions.getByRole('button', { name: 'Open Agent history' }).click();
    const workspace = page.getByRole('dialog', { name: 'Garden Minister workspace' });
    await workspace.locator('.workspace-titlebar').click({ position: { x: 650, y: 18 }, modifiers: ['Shift'] });
    await page.keyboard.press('f');
    await expect(workspace.getByRole('tab', { name: 'Activity', exact: true })).toHaveAttribute('aria-selected', 'true');
    await expect(workspace.locator('.agent-history-session').first()).toBeVisible();
    await workspace.getByRole('tab', { name: 'Minister', exact: true }).click();
    await expect(workspace.getByRole('spinbutton', { name: 'Control radius' })).toHaveValue('300');
    await page.screenshot({ path: 'test-results/minister-workspace-tab-light.png' });
    await page.getByRole('button', { name: 'Use dark theme' }).click();
    await page.screenshot({ path: 'test-results/minister-workspace-tab-dark.png' });
    await workspace.getByRole('tab', { name: 'Settings', exact: true }).click();
    await expect(workspace.getByRole('textbox', { name: 'System instruction', exact: true })).toBeVisible();
    await expect(workspace.getByRole('spinbutton', { name: 'Control radius' })).toHaveCount(0);
    await page.reload();
    expect((await (await request.get(`/api/nodes/${id}`)).json()).minister.control_radius).toBe(300);
    await expect(page.locator('.minister-panel, .minister-manage')).toHaveCount(0);
  } finally {
    await observer.close();
    for (const key of [id, noteId, farId]) await request.delete(`/api/nodes/${key}`);
  }
});

test("two Minister conversations report an overlapping edit conflict", async ({ page, context, request }) => {
  const stamp = Date.now();
  const ids = [`minister-north-${stamp}`, `minister-south-${stamp}`], noteId = `minister-shared-${stamp}`;
  for (let index = 0; index < 2; index++) expect((await request.post("/api/nodes", { data: {
    id: ids[index], type: "agent", size: { width: 96, height: 96 }, name: index ? "South" : "North", position: { x: 320 + index * 110, y: 200 },
    minister: { allow_canvas_edits: true, control_radius: 500 },
  } })).ok()).toBeTruthy();
  expect((await request.post("/api/nodes", { data: { id: noteId, type: "text", name: "Shared note", position: { x: 150, y: 260 }, size: { width: 96, height: 96 } } })).ok()).toBeTruthy();
  const second = await context.newPage();
  try {
    await page.goto("/"); await second.goto("/");
    await openChat(page, 'North');
    await openChat(second, 'South');
    await page.getByRole("textbox", { name: "Message North" }).fill(`race:${noteId}`);
    await second.getByRole("textbox", { name: "Message South" }).fill(`race:${noteId}`);
    await Promise.all([page.getByRole("button", { name: "Send to North" }).click(), second.getByRole("button", { name: "Send to South" }).click()]);
    const north = page.getByRole("log", { name: "North conversation" });
    const south = second.getByRole("log", { name: "South conversation" });
    await expect.poll(async () => `${await north.innerText()}\n${await south.innerText()}`).toContain("revision_conflict");
    await expect.poll(async () => `${await north.innerText()}\n${await south.innerText()}`).toContain("Renamed the card.");
    const name = (await (await request.get(`/api/nodes/${noteId}`)).json()).name;
    await expect(page.locator(`[data-card-id="${noteId}"]`)).toContainText(name);
    await expect(second.locator(`[data-card-id="${noteId}"]`)).toContainText(name);
  } finally {
    await second.close();
    for (const key of [...ids, noteId]) await request.delete(`/api/nodes/${key}`);
  }
});

test("the reported Chinese chat-setup scenario creates a usable separate Conversation", async ({ page, request }) => {
  await page.setViewportSize({ width: 1800, height: 1100 });
  const minister = await (await request.post("/api/nodes", { data: {
    type: "agent", size: { width: 96, height: 96 }, name: "Minister", position: { x: 900, y: 400 }, minister: { allow_canvas_edits: false },
  } })).json();
  let areaId: string | undefined, participantId: string | undefined;
  try {
    await page.goto("/");
    await openChat(page, 'Minister');
    const input = page.getByRole("textbox", { name: "Message Minister", exact: true });
    const send = page.getByRole("button", { name: "Send to Minister", exact: true });
    const log = page.getByRole("log", { name: "Minister conversation", exact: true });
    await input.fill("帮我配置个聊天环境？");
    await send.click();
    await expect(log).toContainText("只能查看");
    await openPermissions(page, 'Minister');
    await page.getByRole('checkbox', { name: 'Allow canvas edits' }).click();
    await openChat(page, 'Minister');
    await expect.poll(async () => (await (await request.get(`/api/nodes/${minister.id}`)).json()).minister.allow_canvas_edits).toBe(true);
    await input.fill("现在试试");
    await send.click();
    await expect(log).toContainText("尚未验证模型回复");
    expect(await log.innerText()).not.toMatch(/edits_allowed|writable_config_fields|canvas_connect/);
    const nodes = await (await request.get("/api/nodes")).json();
    const area = nodes.find((node: { type: string; equipment?: unknown }) => node.type === "conversation" && !node.equipment);
    areaId = area.id;
    participantId = nodes.find((node: { type: string; minister?: unknown }) => node.type === "agent" && !node.minister).id;
    expect(nodes.some((node: { type: string }) => node.type === "text")).toBe(false);
    const inspection = await (await request.get(`/api/ministers/${minister.id}/world`, { params: { query: area.id } })).json();
    expect(inspection.nodes[0].chat_readiness.routing_ready).toBe(true);
    expect(inspection.nodes[0].chat_readiness.reply_observed).toBe(false);
    await closeChat(page);
    // A horizontal SVG path has a zero-height bounding box in Playwright.
    // Its endpoint circle verifies that React Flow actually rendered the edge.
    await expect(page.locator(`circle[data-edge-endpoint="source"][data-source-id="${participantId}"][data-target-id="${area.id}"]`)).toBeVisible();
    await page.screenshot({ path: "../.tmp/minister-chat-connection.png" });
    const card = page.locator(`[data-card-id="${area.id}"]`);
    await expect(card).toHaveAttribute("data-surface-level", "workspace");
    const workspace = page.locator(`[data-workspace-node-id="${area.id}"]`);
    await expect(workspace.getByText("1 active participants", { exact: true })).toBeVisible();
    await workspace.getByLabel("Conversation message", { exact: true }).fill("你好");
    await workspace.getByRole("button", { name: "Send message", exact: true }).click();
    await expect(workspace.locator(".workspace-transcript")).toContainText("你好，可以开始聊天。");
    await page.screenshot({ path: "../.tmp/minister-chat-environment.png" });
  } finally {
    await request.delete(`/api/nodes/${minister.id}`);
    if (areaId) await request.delete(`/api/nodes/${areaId}`);
    if (participantId) await request.delete(`/api/nodes/${participantId}`);
  }
});

test('local administration shares glue and resize, and deletion waits for a real review', async ({ page, context, request }) => {
  const ids: string[] = [];
  const create = async (type: string, name: string, x: number, y: number) => {
    const node = await (await request.post('/api/nodes', { data: { type, name, position: { x, y }, size: { width: 96, height: 96 } } })).json();
    ids.push(node.id); return node;
  };
  const minister = await create('agent', 'Local administrator', 500, 350);
  await request.patch(`/api/nodes/${minister.id}`, { data: { minister: {} } });
  const agent = await create('agent', 'Worker', 150, 240);
  const note = await create('text', 'Working notes', 350, 240);
  await request.post('/api/edges', { data: { source: agent.id, target: note.id, relationship: 'read' } });
  const observer = await context.newPage();
  try {
    await page.goto('/'); await observer.goto('/');
    for (const canvas of [page, observer]) {
      await canvas.getByRole('button', { name: 'Collapse Working notes card', exact: true }).click();
      await canvas.getByRole('button', { name: 'Collapse Worker card', exact: true }).click();
    }
    await openChat(page, 'Local administrator');
    const send = async (text: string) => {
      await openChat(page, 'Local administrator');
      await page.getByRole('textbox', { name: 'Message Local administrator', exact: true }).fill(text);
      await page.getByRole('button', { name: 'Send to Local administrator', exact: true }).click();
    };
    await send(`resize:${agent.id}`);
    await expect.poll(async () => (await (await request.get(`/api/nodes/${agent.id}`)).json()).size.width).toBe(140);
    await expect(observer.locator(`.react-flow__node[data-id="${agent.id}"]`)).toHaveCSS('width', '140px');
    await send(`glue:${agent.id}:${note.id}`);
    await expect.poll(async () => (await (await request.get('/api/canvas/glue')).json()).bonds.length).toBe(1);
    await expect(observer.locator('.glue-seam:not(.is-preview)')).toHaveCount(1);
    await send(`delete:${note.id}`);
    const review = page.locator('.minister-presence').getByRole('region', { name: 'Review canvas changes' });
    await expect(review).toBeVisible();
    await page.getByRole('textbox', { name: 'Message Local administrator', exact: true }).press('Escape');
    await page.mouse.move(20, 20);
    await page.clock.install();
    await page.clock.fastForward(61000);
    await expect(review).toBeVisible();
    await page.reload();
    await expect(review).toBeVisible();
    await page.clock.setSystemTime(new Date());
    await expect(review).toContainText('Working notes');
    await expect(review).toContainText('Worker');
    expect((await request.get(`/api/nodes/${note.id}`)).status()).toBe(200);
    await page.screenshot({ path: '../.tmp/minister-authority-review.png' });
    await review.getByRole('button', { name: 'Reject', exact: true }).click();
    await expect(review).toHaveCount(0);
    expect((await request.get(`/api/nodes/${note.id}`)).status()).toBe(200);
    await send(`delete:${note.id}`);
    await expect(review).toBeVisible();
    await review.getByRole('button', { name: 'Confirm changes', exact: true }).click();
    await expect.poll(async () => (await request.get(`/api/nodes/${note.id}`)).status()).toBe(404);
    await expect(observer.locator(`[data-card-id="${note.id}"]`)).toHaveCount(0);
    await expect(observer.locator('.glue-seam:not(.is-preview)')).toHaveCount(0);
    expect((await request.get(`/api/nodes/${agent.id}`)).status()).toBe(200);
    await openChat(page, 'Local administrator');
    await expect(page.getByRole('log', { name: 'Local administrator conversation', exact: true }))
      .toContainText('I confirmed the proposed changes. Inspect the current canvas and continue the remaining task.');
  } finally {
    await observer.close();
    for (const id of ids.reverse()) await request.delete(`/api/nodes/${id}`);
  }
});


test('the collapsed Minister opens chat from its node and can reopen it while settings stay open', async ({ page, request }) => {
  const card = await (await request.post('/api/nodes', { data: {
    type: 'agent', minister: {}, name: 'Compact chat', position: { x: 350, y: 350 },
  } })).json();
  try {
    await page.goto('/');
    const node = page.locator(`[data-card-id="${card.id}"]`);
    await node.getByRole('button', { name: 'Collapse Compact chat card', exact: true }).click();
    await page.mouse.move(20, 20);
    await node.locator('.card-kind-icon').hover();
    const input = page.getByRole('textbox', { name: 'Message Compact chat', exact: true });
    await expect(input).toBeVisible();
    await expect(page.getByText('Hi! Need a hand with this part of your canvas?', { exact: true })).toBeVisible();
    await input.fill('Keep this draft');
    await page.getByRole('button', { name: 'Minister settings', exact: true }).click();
    await expect(input).toBeHidden();
    await page.getByRole('button', { name: 'Open Minister Compact chat', exact: true }).click();
    await expect(input).toBeVisible();
    await expect(input).toHaveValue('Keep this draft');
    await expect(node.getByRole('tab', { name: 'Minister', exact: true })).toHaveAttribute('aria-selected', 'true');
    await input.fill('What is nearby?');
    await page.getByRole('button', { name: 'Send to Compact chat', exact: true }).click();
    await expect(page.locator('.minister-presence .is-agent')).not.toHaveCount(0);
  } finally { await request.delete(`/api/nodes/${card.id}`); }
});

for (const sender of ['user', 'agent']) test(`Minister ${sender} messages use a single bubble with local scrolling`, async ({ page, request }) => {
  await page.setViewportSize({ width: 1400, height: 1100 });
  const card = await (await request.post('/api/nodes', { data: {
    type: 'agent', minister: {}, size: { width: 96, height: 96 }, name: 'Bubble check', position: { x: 350, y: 700 },
  } })).json();
  // Deterministic transcript fixture; exercise the actual presence renderer and CSS.
  await page.route('**/sessions/*/timeline?*', async route => {
    const response = await route.fetch();
    const body = await response.json();
    const created_at = new Date(Date.now() + 100).toISOString();
    await route.fulfill({ response, json: { ...body, items: [
      { id: 'short-bubble', sender_kind: sender, sender_name: 'Bubble check', content: 'A short message.', created_at, sequence: 1 },
      { id: 'long-bubble', sender_kind: sender, sender_name: 'Bubble check', content: Array.from({ length: 24 }, (_, i) => `Paragraph ${i + 1}: This long message stays inside one readable bubble with **Markdown** formatting.`).join('\n\n'), created_at, sequence: 2 },
    ], has_before: false, has_after: false } });
  });
  try {
    await page.goto('/');
    await openChat(page, 'Bubble check');
    await page.getByRole('textbox', { name: 'Message Bubble check', exact: true }).fill('Draft keeps the chat open');
    const bubble = page.locator('[data-message-id="long-bubble"]');
    const content = bubble.locator('.minister-bubble-content');
    await expect(content).toBeVisible();
    const layout = await bubble.evaluate(element => {
      const scroll = element.querySelector('.minister-bubble-content')!;
      const markdown = getComputedStyle(element.querySelector('.conversation-markdown')!);
      return { height: scroll.clientHeight, overflowing: scroll.scrollHeight > scroll.clientHeight,
        background: markdown.backgroundColor, border: markdown.borderTopWidth, padding: markdown.paddingTop,
        outerOverflow: getComputedStyle(element.parentElement!.parentElement!).overflowY };
    });
    expect(layout).toEqual({ height: 280, overflowing: true, background: 'rgba(0, 0, 0, 0)', border: '0px', padding: '0px', outerOverflow: 'visible' });
    const short = page.locator('[data-message-id="short-bubble"] .minister-bubble-content');
    expect(await short.evaluate(el => el.scrollHeight === el.clientHeight && el.clientHeight < 100)).toBe(true);
    await expect(content).toHaveCSS('mask-image', 'none');
    await content.focus();
    // Blurring the growing composer moves the anchored bubble stack; hover its settled position.
    await expect(page.locator('.minister-composer textarea')).toHaveCSS('height', '22px');
    await content.hover();
    const viewport = await page.locator('.react-flow__viewport').getAttribute('style');
    await page.mouse.wheel(0, 160);
    await expect(content).toHaveAttribute('data-scrolled', 'true');
    expect(await content.evaluate(el => getComputedStyle(el).maskImage)).toContain('linear-gradient');
    expect(await page.locator('.react-flow__viewport').getAttribute('style')).toBe(viewport);
    await page.screenshot({ path: `../.tmp/minister-single-bubble-review/${sender}-light.png` });
    await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark'));
    await page.screenshot({ path: `../.tmp/minister-single-bubble-review/${sender}-dark.png` });
    await content.evaluate(el => { el.scrollTop = 0; });
    await expect(content).toHaveAttribute('data-scrolled', 'false');
    await expect(content).toHaveCSS('mask-image', 'none');
    await page.getByRole('textbox', { name: 'Message Bubble check', exact: true }).focus();
    await expect(page.locator('.minister-composer textarea')).toHaveCSS('height', '48px');
    await content.hover();
    await page.mouse.wheel(0, 160);
    await expect(content).toHaveAttribute('data-scrolled', 'true');
  } finally { await request.delete(`/api/nodes/${card.id}`); }
});

test('Minister chat avoids the Equipment button and switches sides near the canvas edge', async ({ page, request }) => {
  const card = await (await request.post('/api/nodes', { data: {
    type: 'agent', minister: {}, name: 'Equipment chat', position: { x: 350, y: 400 },
  } })).json();
  try {
    await page.goto('/');
    const node = page.locator(`.world-card[data-card-id="${card.id}"]`);
    await node.getByRole('button', { name: 'Collapse Equipment chat card', exact: true }).click();
    await node.locator('.card-kind-icon').hover();
    const input = page.getByRole('textbox', { name: 'Message Equipment chat', exact: true });
    await input.fill('Keep this draft');
    const equipment = node.getByRole('button', { name: 'Equipment for Equipment chat', exact: true });
    const presence = page.locator('.minister-presence:not([hidden])');
    const canClickEquipment = async () => {
      await expect.poll(() => equipment.evaluate(el => {
        const bounds = el.getBoundingClientRect();
        return el.contains(document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2));
      })).toBe(true);
      await equipment.click();
      await expect(equipment).toHaveAttribute('aria-expanded', 'true');
      await equipment.click();
      await expect(equipment).toHaveAttribute('aria-expanded', 'false');
      await expect(input).toHaveValue('Keep this draft');
    };
    await expect.poll(async () => {
      const a = (await equipment.boundingBox())!, b = (await presence.boundingBox())!;
      return b.x > a.x + a.width;
    }).toBe(true);
    await canClickEquipment();
    await page.screenshot({ path: 'test-results/minister-equipment-right.png' });
    const icon = (await node.locator('.card-kind-icon').boundingBox())!;
    await page.mouse.move(icon.x + icon.width / 2, icon.y + icon.height / 2);
    await page.mouse.down();
    await page.mouse.move(1100, 450, { steps: 20 });
    await page.mouse.up();
    await expect.poll(async () => {
      const a = (await node.boundingBox())!, b = (await presence.boundingBox())!;
      return b.x + b.width < a.x;
    }).toBe(true);
    await input.focus();
    await canClickEquipment();
    await page.screenshot({ path: 'test-results/minister-equipment-left.png' });
  } finally { await request.delete(`/api/nodes/${card.id}`); }
});

test("Minister composer centers icons and gives immediate send feedback", async ({ page, request }) => {
  const card = await (await request.post('/api/nodes', { data: {
    type: 'agent', minister: {}, size: { width: 96, height: 96 }, name: 'Composer check', position: { x: 350, y: 500 },
  } })).json();
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  try {
    await page.goto('/');
    await openChat(page, 'Composer check');
    const input = page.getByRole('textbox', { name: 'Message Composer check', exact: true });
    const composer = page.locator('.minister-composer');
    const centered = async () => {
      const offset = await composer.locator('button').evaluate(el => {
        const button = el.getBoundingClientRect(), icon = el.querySelector('svg')!.getBoundingClientRect();
        return { x: Math.abs(button.x + button.width / 2 - icon.x - icon.width / 2),
          y: Math.abs(button.y + button.height / 2 - icon.y - icon.height / 2) };
      });
      expect(offset.x).toBeLessThan(0.5);
      expect(offset.y).toBeLessThan(0.5);
    };
    await expect(input).toHaveCSS('height', '22px');
    expect(await input.evaluate(el => el.scrollHeight === el.clientHeight)).toBe(true);
    await centered();
    await input.fill('What is nearby?');
    await expect(input).toHaveCSS('height', '48px');
    expect(await input.evaluate(el => el.scrollHeight === el.clientHeight)).toBe(true);
    await page.route('**/sessions/*/messages', async route => { await gate; await route.continue(); });
    await composer.getByRole('button', { name: 'Send to Composer check' }).click();
    await expect(page.locator('.minister-thinking')).toHaveText('Sending...');
    await expect(composer.locator('button')).toBeDisabled();
    await centered();
    await page.screenshot({ path: 'test-results/minister-composer-sending.png' });
    release();
    await expect(page.locator('.minister-presence .is-agent')).not.toHaveCount(0);
    await page.route('**/sessions/*/timeline?*', async route => {
      const response = await route.fetch();
      await route.fulfill({ response, json: { ...await response.json(), active_agent_ids: [card.id] } });
    });
    await page.reload();
    await openChat(page, 'Composer check');
    await expect(composer.getByRole('button', { name: 'Stop Composer check' })).toBeVisible();
    await centered();
    await page.screenshot({ path: 'test-results/minister-composer-running.png' });
    await input.fill(Array.from({ length: 12 }, (_, i) => `Line ${i}`).join('\n'));
    await expect(input).toHaveCSS('height', '48px');
    expect(await input.evaluate(el => el.scrollHeight > el.clientHeight)).toBe(true);
  } finally { release(); await request.delete(`/api/nodes/${card.id}`); }
});

test("canvas presence greets locally and opens management explicitly", async ({ page, request }) => {
  const card = await (await request.post('/api/nodes', { data: {
    type: 'agent', minister: {}, size: { width: 96, height: 96 }, name: 'Presence check', position: { x: 320, y: 300 },
  } })).json();
  let sent = 0;
  page.on('request', request => { if (request.method() === 'POST' && request.url().includes('/messages')) sent++; });
  try {
    await page.goto('/');
    const avatar = page.getByRole('button', { name: 'Open Minister Presence check', exact: true });
    await avatar.hover();
    const node = page.locator(`[data-card-id="${card.id}"]`);
    await expect(node.locator('.minister-label')).toHaveCount(0);
    await expect(page.locator(`[data-minister-scope="${card.id}"] .minister-radius`)).toBeVisible();
    const input = page.getByRole('textbox', { name: 'Message Presence check', exact: true });
    await expect(input).toBeVisible();
    await expect(page.getByText('Hi! Need a hand with this part of your canvas?', { exact: true })).toBeVisible();
    const panel = page.getByRole('region', { name: 'Presence check controls' });
    await expect(panel).toHaveCount(0);
    await input.fill('What is nearby?');
    await page.mouse.move(20, 20);
    await expect(page.locator(`[data-minister-scope="${card.id}"] .minister-radius`)).toBeHidden();
    await expect(page.locator(`[data-minister-scope="${card.id}"] .minister-radius-handle`)).toBeHidden();
    await expect(input).toBeVisible();
    expect(sent).toBe(0);
    await page.screenshot({ path: '../.tmp/minister-presence.png' });
    await page.getByRole('button', { name: 'Send to Presence check', exact: true }).click();
    const bubbles = page.locator('.minister-presence .minister-messages');
    await expect(bubbles).toContainText('What is nearby?');
    await expect(bubbles.locator('.is-agent')).not.toHaveCount(0);

    await openPermissions(page, 'Presence check');
    await expect(panel).toHaveCount(0);
    await expect(page.getByRole('spinbutton', { name: 'Control radius' })).toBeVisible();
    await expect(page.getByRole('region', { name: 'Minister permissions' })).toBeVisible();
    await page.getByRole('button', { name: 'Close Presence check inspector', exact: true }).click();
    await avatar.hover();
    await input.fill('');
    await input.press('Escape');
    await page.mouse.move(20, 20);
    await avatar.hover();
    await expect(input).toBeVisible();
    await expect(page.getByText('Hi! Need a hand with this part of your canvas?', { exact: true })).toHaveCount(0);

  } finally { await request.delete(`/api/nodes/${card.id}`); }
});
