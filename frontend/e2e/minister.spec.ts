import { expect, test } from "@playwright/test";

// Run against backend.tests.minister_app for deterministic, real tool invocations.
test.skip(process.env.OAW_MINISTER_E2E !== "1", "Use npm run test:e2e:minister for the isolated tool-using provider");

test("Minister can be collected and placed from the ordinary card deck", async ({ page, request }) => {
  await page.goto("/");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.getByRole("button", { name: "Open Pack and Card Library" }).click();
  const library = page.getByRole("dialog", { name: "Pack & Card Library" });
  const pack = library.getByRole("article", { name: "Core essentials", exact: true });
  await pack.locator(".pack-touch-area").focus();
  await page.keyboard.press("Enter");
  await pack.getByRole("button", { name: "View cards in Core essentials" }).click();
  await library.getByLabel("Search cards", { exact: true }).fill("Minister");
  await library.getByRole("button", { name: "Add Minister to deck", exact: true }).click();
  await library.getByRole("button", { name: "Close Library" }).click();
  const tray = page.getByRole("complementary", { name: "Active card deck" });
  await tray.hover();
  await tray.getByRole("button", { name: "Place Minister", exact: true }).dragTo(page.locator(".react-flow__pane").first(), { targetPosition: { x: 350, y: 230 } });
  await expect(page.getByRole("button", { name: "Open Minister Minister", exact: true })).toBeVisible();
  const cards = await (await request.get("/api/nodes")).json();
  const card = cards.find((node: { type: string }) => node.type === "core.minister");
  expect(card.config.allow_canvas_edits).toBe(true);
  expect(card.size).toEqual({ width: 96, height: 96 });
  await request.delete(`/api/nodes/${card.id}`);
});

test("Minister circle, radius, local search, durable chat and cross-tab edits", async ({ page, context, request }) => {
  const stamp = Date.now();
  const id = `minister-ui-${stamp}`, noteId = `minister-note-${stamp}`, farId = `minister-far-${stamp}`;
  for (const data of [
    { id, type: "core.minister", name: "Garden Minister", position: { x: 320, y: 200 }, config: { control_radius: 400, allow_canvas_edits: false } },
    { id: noteId, type: "text", name: "Near note", position: { x: 100, y: 260 }, size: { width: 96, height: 96 } },
    { id: farId, type: "text", name: "Far note", position: { x: 1000, y: 800 } },
  ]) expect((await request.post("/api/nodes", { data })).ok()).toBeTruthy();
  await page.addInitScript(() => localStorage.setItem("oaw-canvas-viewport-v1", JSON.stringify({ state: {
    viewport: { x: 0, y: 0, zoom: 1, width: 1280, height: 800 }, paletteCollapsed: true,
  }, version: 0 })));
  const observer = await context.newPage();
  try {
    await page.goto("/");
    await observer.goto("/");
    const orb = page.getByRole("button", { name: "Open Minister Garden Minister" });
    await expect(orb).toBeVisible();
    expect(await orb.evaluate(element => getComputedStyle(element).borderRadius)).toBe("50%");
    const node = page.locator(`[data-card-id="${id}"]`);
    const handle = page.getByRole("button", { name: "Resize Garden Minister control radius" });
    const grip = await handle.boundingBox();
    await page.mouse.move(grip!.x + grip!.width / 2, grip!.y + grip!.height / 2);
    await page.mouse.down();
    await page.mouse.move(grip!.x + grip!.width / 2 + 100, grip!.y + grip!.height / 2, { steps: 8 });
    await page.mouse.up();
    await expect.poll(async () => (await (await request.get(`/api/nodes/${id}`)).json()).config.control_radius).toBe(500);
    const origin = await orb.boundingBox();
    await orb.click();
    const panel = page.getByRole("region", { name: "Garden Minister controls" });
    await expect(panel).toBeVisible();
    const opened = await orb.boundingBox();
    expect(opened!.x).toBeCloseTo(origin!.x, 0);
    expect(opened!.y).toBeCloseTo(origin!.y, 0);
    const radius = page.getByRole("spinbutton", { name: "Control radius" });
    await radius.fill("300"); await radius.press("Enter");
    await expect.poll(async () => (await (await request.get(`/api/nodes/${id}`)).json()).config.control_radius).toBe(300);
    await page.getByRole("button", { name: "Nearby cards", exact: true }).click();
    await page.getByRole("textbox", { name: "Search nearby cards" }).fill("note");
    await expect(panel.getByRole("button", { name: /Near note/ })).toBeVisible();
    await expect(panel.getByRole("button", { name: /Far note/ })).toHaveCount(0);
    await page.getByRole("checkbox", { name: "Allow canvas edits" }).click();
    await expect.poll(async () => (await (await request.get(`/api/nodes/${id}`)).json()).config.allow_canvas_edits).toBe(true);
    await page.getByRole("button", { name: "Conversation", exact: true }).click();
    await page.getByRole("textbox", { name: "Message Garden Minister" }).fill(`rename ${noteId} Minister renamed this`);
    await page.getByRole("button", { name: "Send to Garden Minister" }).click();
    await expect(page.getByRole("log", { name: "Garden Minister conversation" })).toContainText("Renamed the card.");
    await expect(observer.locator(`[data-card-id="${noteId}"]`)).toContainText("Minister renamed this");
    expect((await (await request.get(`/api/nodes/${id}`)).json()).position).toEqual({ x: 320, y: 200 });
    await page.screenshot({ path: "../.tmp/minister-mvp-light.png" });
    await page.reload();
    await expect(page.getByRole("log", { name: "Garden Minister conversation" })).toContainText("Renamed the card.");
    await page.getByRole("button", { name: "Close Minister", exact: true }).click();
    await orb.click();
    await expect(page.getByRole("log", { name: "Garden Minister conversation" })).toContainText("Renamed the card.");
    await expect(node).toHaveAttribute("data-control-radius", "300");
    await page.getByRole("button", { name: "Use dark theme" }).click();
    await page.screenshot({ path: "../.tmp/minister-mvp-dark.png" });
    await page.getByRole("button", { name: "Close Minister", exact: true }).click();
    const beforeDrag = await orb.boundingBox();
    await page.mouse.move(beforeDrag!.x + 48, beforeDrag!.y + 48);
    await page.mouse.down();
    await page.mouse.move(beforeDrag!.x + 48, beforeDrag!.y + 128, { steps: 10 });
    await page.mouse.up();
    await expect.poll(async () => (await (await request.get(`/api/nodes/${id}`)).json()).position.y).toBeGreaterThan(250);
    const moved = (await (await request.get(`/api/nodes/${id}`)).json()).position;
    expect(moved.x).toBe(320);
    expect(moved.y).toBeLessThanOrEqual(280);
    expect((await orb.boundingBox())!.y).toBeCloseTo(moved.y, 0);
    await expect(panel).toHaveCount(0);
    expect((await (await request.get(`/api/ministers/${id}/world`)).json()).scope.center).toEqual({ x: moved.x + 48, y: moved.y + 48 });
  } finally {
    await observer.close();
    for (const key of [id, noteId, farId]) await request.delete(`/api/nodes/${key}`);
  }
});

test("two Minister conversations report an overlapping edit conflict", async ({ page, context, request }) => {
  const stamp = Date.now();
  const ids = [`minister-north-${stamp}`, `minister-south-${stamp}`], noteId = `minister-shared-${stamp}`;
  for (let index = 0; index < 2; index++) expect((await request.post("/api/nodes", { data: {
    id: ids[index], type: "core.minister", name: index ? "South" : "North", position: { x: 320 + index * 110, y: 200 },
    config: { allow_canvas_edits: true, control_radius: 500 },
  } })).ok()).toBeTruthy();
  expect((await request.post("/api/nodes", { data: { id: noteId, type: "text", name: "Shared note", position: { x: 150, y: 260 }, size: { width: 96, height: 96 } } })).ok()).toBeTruthy();
  const second = await context.newPage();
  try {
    await page.goto("/"); await second.goto("/");
    await page.getByRole("button", { name: "Open Minister North", exact: true }).click();
    await second.getByRole("button", { name: "Open Minister South", exact: true }).click();
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
    type: "core.minister", name: "Minister", position: { x: 900, y: 400 }, config: { allow_canvas_edits: false },
  } })).json();
  let areaId: string | undefined, participantId: string | undefined;
  try {
    await page.goto("/");
    await page.getByRole("button", { name: "Open Minister Minister", exact: true }).click();
    const input = page.getByRole("textbox", { name: "Message Minister", exact: true });
    const send = page.getByRole("button", { name: "Send to Minister", exact: true });
    const log = page.getByRole("log", { name: "Minister conversation", exact: true });
    await input.fill("帮我配置个聊天环境？");
    await send.click();
    await expect(log).toContainText("只能查看");
    await page.getByRole("checkbox", { name: "Allow canvas edits" }).click();
    await expect.poll(async () => (await (await request.get(`/api/nodes/${minister.id}`)).json()).config.allow_canvas_edits).toBe(true);
    await input.fill("现在试试");
    await send.click();
    await expect(log).toContainText("尚未验证模型回复");
    expect(await log.innerText()).not.toMatch(/edits_allowed|writable_config_fields|canvas_connect/);
    const nodes = await (await request.get("/api/nodes")).json();
    const area = nodes.find((node: { type: string; equipment?: unknown }) => node.type === "conversation" && !node.equipment);
    areaId = area.id;
    participantId = nodes.find((node: { type: string }) => node.type === "agent").id;
    expect(nodes.some((node: { type: string }) => node.type === "text")).toBe(false);
    const inspection = await (await request.get(`/api/ministers/${minister.id}/world`, { params: { query: area.id } })).json();
    expect(inspection.nodes[0].chat_readiness.routing_ready).toBe(true);
    expect(inspection.nodes[0].chat_readiness.reply_observed).toBe(false);
    await page.getByRole("button", { name: "Close Minister", exact: true }).click();
    // A horizontal SVG path has a zero-height bounding box in Playwright.
    // Its endpoint circle verifies that React Flow actually rendered the edge.
    await expect(page.locator(`circle[data-edge-endpoint="source"][data-source-id="${participantId}"][data-target-id="${area.id}"]`)).toBeVisible();
    await page.screenshot({ path: "../.tmp/minister-chat-connection.png" });
    const card = page.locator(`[data-card-id="${area.id}"]`);
    await card.click();
    await card.getByRole("button", { name: "Open workspace", exact: true }).click();
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
  const minister = await create('core.minister', 'Local administrator', 500, 180);
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
    await page.getByRole('button', { name: 'Open Minister Local administrator', exact: true }).click();
    const send = async (text: string) => {
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
    const review = page.getByRole('region', { name: 'Review canvas changes' });
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
    await expect(page.getByRole('log', { name: 'Local administrator conversation', exact: true }))
      .toContainText('I confirmed the proposed changes. Inspect the current canvas and continue the remaining task.');
  } finally {
    await observer.close();
    for (const id of ids.reverse()) await request.delete(`/api/nodes/${id}`);
  }
});
