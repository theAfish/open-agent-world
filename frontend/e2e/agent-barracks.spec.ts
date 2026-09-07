import { expect, test } from "@playwright/test";

test("connectable containers use the shared boundary-following connection hint", async ({ page, request }) => {
  await page.setViewportSize({ width: 1800, height: 1200 });
  const box = await (await request.post("/api/nodes", { data: {
    type: "oaw.barracks", name: "E2E Connection Hint", position: { x: 460, y: 220 },
  } })).json();
  try {
    await page.goto("/");
    const surface = page.locator(`[data-card-id="${box.id}"]`);
    const hint = surface.locator("[data-connection-hover-hint]");
    await expect(surface).toBeVisible();
    await expect(hint).toBeAttached();
    const frame = await surface.boundingBox();
    if (!frame) throw new Error("Container geometry is unavailable");
    await surface.hover({ position: { x: frame.width - 3, y: frame.height / 2 } });
    await expect(surface).toHaveAttribute("data-connection-hot", "true");
    const hintBox = await hint.boundingBox();
    if (!hintBox) throw new Error("Container connection hint geometry is unavailable");
    expect(Math.abs(hintBox.x + hintBox.width / 2 - (frame.x + frame.width))).toBeLessThanOrEqual(2);
  } finally {
    await request.delete(`/api/nodes/${box.id}`);
  }
});

test("summoning streams a virtual workspace and generated connection onto the canvas", async ({ page, request }) => {
  await page.setViewportSize({ width: 1800, height: 1200 });
  const box = await (await request.post('/api/nodes', { data: {
    type: 'oaw.barracks', name: 'Summon source', position: { x: 0, y: 0 },
    size: { width: 96, height: 96 },
  } })).json();
  const agent = await (await request.post('/api/nodes', { data: {
    type: 'agent', name: 'Worker', parent_id: box.id, position: { x: 850, y: 150 },
  } })).json();
  await page.goto('/');
  await expect(page.locator(`[data-card-id="${box.id}"]`)).toBeVisible();
  const response = await request.post(`/api/nodes/${box.id}/summoning/actions`, { data: {
    action: 'summon', agent_id: agent.id, prompt: 'Hello',
  } });
  expect(response.ok()).toBeTruthy();
  const instance = await response.json();
  const workspace = page.locator(`[data-card-id="${instance.workspace_id}"]`);
  await expect(workspace).toHaveClass(/virtual-workspace/);
  await expect(workspace).toHaveCSS('border-top-style', 'dashed');
  await expect.poll(async () => {
    const source = await page.locator(`[data-card-id="${box.id}"]`).boundingBox();
    const region = await workspace.boundingBox();
    return !!source && !!region && (source.x + source.width <= region.x || region.x + region.width <= source.x
      || source.y + source.height <= region.y || region.y + region.height <= source.y);
  }).toBeTruthy();
  await expect(page.locator(`[data-card-id="${instance.entry_agent_id}"]`)).toBeAttached();
  const worker = page.locator(`[data-card-id="${instance.entry_agent_id}"]`);
  const expectContained = async () => {
    await expect.poll(async () => {
      const frame = await workspace.boundingBox();
      const member = await worker.boundingBox();
      return !!frame && !!member && member.x >= frame.x && member.y >= frame.y
        && member.x + member.width <= frame.x + frame.width
        && member.y + member.height <= frame.y + frame.height;
    }).toBeTruthy();
  };
  await expectContained();
  await worker.click({ position: { x: 140, y: 75 } });
  await expect(worker).toHaveAttribute('data-surface-level', 'inspector');
  await expectContained();
  await expect(page.locator(`.semantic-edge-path.is-generated[data-target-id="${instance.workspace_id}"]`)).toHaveCSS('stroke-dasharray', '8px, 6px');
  await request.post(`/api/nodes/${box.id}/summoning/actions`, { data: {
    action: 'reclaim', instance_id: instance.id,
  } });
  await expect(workspace).toHaveCount(0);
  await expect(page.locator(`.semantic-edge-path.is-generated[data-target-id="${instance.workspace_id}"]`)).toHaveCount(0);
  expect((await request.post('/api/nodes/batch-delete', { data: { node_ids: [agent.id, box.id] } })).ok()).toBeTruthy();
});

test("multiple equipment drops and owner deletion stay synchronized through undo and redo", async ({ page, request }) => {
  await page.setViewportSize({ width: 1800, height: 1200 });
  const create = async (type: string, x: number, y: number) => (await request.post('/api/nodes', { data: { type, position: { x, y } } })).json();
  const agent = await create('agent', 750, 350);
  const first = await create('sandbox', 200, 350);
  const second = await create('text', 200, 650);
  const conversation = await create('conversation', 200, 900);
  await page.goto('/');
  const surface = (id: string) => page.locator(`[data-card-id="${id}"]`);
  await surface(agent.id).getByRole('button', { name: `Equipment for ${agent.name}`, exact: true }).click();
  for (const item of [first, second, conversation]) {
    await expect(surface(item.id)).toBeVisible();
    const from = (await surface(item.id).boundingBox())!;
    const to = (await page.locator(`[data-equipment-panel="${agent.id}"]`).boundingBox())!;
    await page.mouse.move(from.x + 40, from.y + 32);
    await page.mouse.down();
    await page.mouse.move(to.x + to.width / 2, to.y + to.height - 30, { steps: 20 });
    await expect(page.locator(`[data-equip-target="${agent.id}"].is-active`)).toBeVisible();
    await page.mouse.up();
    await expect(surface(item.id)).toHaveClass(/equipment-card/);
  }
  await expect(surface(first.id)).toHaveClass(/equipment-card/);
  await page.screenshot({ path: "../.open-agent-world/equipment-slots.png" });
  await surface(conversation.id).getByRole('button', { name: conversation.name, exact: true }).click();
  await expect(surface(conversation.id)).toHaveAttribute('data-surface-level', 'inspector');
  await surface(conversation.id).getByRole('button', { name: `Close ${conversation.name} inspector`, exact: true }).click();
  const source = (await surface(first.id).locator('[data-handleid="boundary-right"]').boundingBox())!;
  const target = (await surface(agent.id).locator('[data-handleid="boundary-right"]').boundingBox())!;
  await page.mouse.move(source.x + source.width / 2, source.y + source.height / 2);
  await page.mouse.down();
  await page.mouse.move(target.x + target.width / 2, target.y + target.height / 2, { steps: 15 });
  await page.mouse.up();
  await expect(page.getByRole('button', { name: 'Grant capability', exact: true })).toHaveCount(0);
  await surface(agent.id).click({ position: { x: 40, y: 32 } });
  await expect(surface(agent.id).getByText('When to use this Agent', { exact: true })).toHaveCount(0);
  await expect(surface(agent.id).getByText('Effective capabilities', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Duplicate Agent with equipment', exact: true })).toHaveCount(0);
  await surface(agent.id).getByRole('button', { name: 'Open workspace', exact: true }).click();
  await page.getByRole('tab', { name: 'Settings', exact: true }).click();
  await expect(page.getByText('When to use this Agent', { exact: true })).toBeVisible();
  await expect(page.locator('.capability-chips')).toContainText('New Sandbox');
  await expect(page.locator('.capability-chips')).toContainText('New Conversation');
  await page.screenshot({ path: "../.open-agent-world/agent-window-settings.png" });
  await page.getByRole('button', { name: 'Close workspace', exact: true }).click();
  await page.keyboard.press('Delete');
  for (const item of [agent, first, second, conversation]) await expect(surface(item.id)).toHaveCount(0);
  for (const item of [agent, first, second, conversation]) expect((await request.get(`/api/nodes/${item.id}`)).status()).toBe(404);
  await page.keyboard.press('Control+z');
  for (const item of [first, second, conversation]) await expect(surface(item.id)).toHaveClass(/equipment-card/);
  await page.keyboard.press('Control+Shift+z');
  for (const item of [agent, first, second, conversation]) await expect(surface(item.id)).toHaveCount(0);
  await expect(page.getByText(/was not removed/)).toHaveCount(0);
});

test("Barracks membership and explicit equipment drops preserve card identity", async ({ page, request }) => {
  await page.setViewportSize({ width: 2200, height: 1600 });
  const create = async (type: string, name: string, x: number, y: number) => {
    const response = await request.post("/api/nodes", { data: { type, name, position: { x, y } } });
    expect(response.status()).toBe(201); return response.json();
  };
  const box = await create("oaw.barracks", "Normal Barracks", 1000, 250);
  const agent = await create("agent", "Equipped worker", 160, 320);
  const sandbox = await create("sandbox", "Private environment", 180, 800);
  const shared = await create("text", "Shared notes", 600, 850);
  await request.post("/api/edges", { data: { source: agent.id, target: shared.id, relationship: "read" } });
  const read = async (id: string) => (await request.get(`/api/nodes/${id}`)).json();
  await page.goto("/");
  const surface = (id: string) => page.locator(`[data-card-id="${id}"]`);
  const drag = async (id: string, x: number, y: number, inspect?: () => Promise<void>) => {
    const from = (await surface(id).boundingBox())!;
    await page.mouse.move(from.x + 40, from.y + 32);
    await page.mouse.down();
    await page.mouse.move(x, y, { steps: 20 });
    await inspect?.();
    await page.mouse.up();
  };
  const target = (await surface(box.id).boundingBox())!;
  await drag(agent.id, target.x + 380, target.y + 250);
  await expect.poll(async () => (await read(agent.id)).parent_id).toBe(box.id);
  await expect(page.getByRole("dialog", { name: "Normal Barracks workspace", exact: true })).toHaveCount(0);
  await expect(page.getByText("Save callable template", { exact: true })).toHaveCount(0);
  await expect.poll(async () => (await (await request.get(`/api/nodes/${box.id}/summoning`)).json()).agents.map((a: { id: string }) => a.id)).toEqual([agent.id]);
  await surface(agent.id).getByRole("button", { name: `Equipment for ${agent.name}`, exact: true }).click();
  const worker = (await page.locator(`[data-equipment-panel="${agent.id}"]`).boundingBox())!;
  await drag(sandbox.id, worker.x + worker.width / 2, worker.y + worker.height / 2, async () => {
    await expect(page.locator(`[data-equip-target="${agent.id}"].is-active`)).toBeVisible();
  });
  await expect.poll(async () => (await read(sandbox.id)).equipment?.owner_id).toBe(agent.id);
  await expect(surface(sandbox.id)).toHaveClass(/equipment-card/);
  await page.keyboard.press("Control+z");
  await expect.poll(async () => (await read(sandbox.id)).equipment).toBeNull();
  await expect(surface(sandbox.id)).not.toHaveClass(/equipment-card/);
  await page.keyboard.press("Control+Shift+z");
  await expect.poll(async () => (await read(sandbox.id)).equipment?.owner_id).toBe(agent.id);
  await page.reload();
  await surface(agent.id).getByRole("button", { name: `Equipment for ${agent.name}`, exact: true }).click();
  await expect(surface(sandbox.id)).toHaveClass(/equipment-card/);
  await surface(sandbox.id).getByRole("button", { name: "Private environment", exact: true }).click();
  await expect(surface(sandbox.id)).toHaveAttribute("data-surface-level", "inspector");
  await surface(sandbox.id).getByRole("button", { name: "Close Private environment inspector", exact: true }).click();
  await surface(sandbox.id).getByRole("button", { name: "Unequip Private environment", exact: true }).click();
  await expect.poll(async () => (await read(sandbox.id)).equipment).toBeNull();
  await expect(surface(sandbox.id)).not.toHaveClass(/equipment-card/);
  await drag(agent.id, 500, 550);
  await expect.poll(async () => (await read(agent.id)).parent_id).toBeNull();
  const edges = (await (await request.get("/api/world")).json()).edges;
  expect(edges.some((e: { source: string; target: string }) => e.source === agent.id && e.target === shared.id)).toBe(true);
  await page.screenshot({ path: "../.open-agent-world/equipment-membership.png" });
  await request.post("/api/nodes/batch-delete", { data: { node_ids: [box.id, agent.id, sandbox.id, shared.id] } });
});

test("equipped Summoning skill uses its own connector and Barracks tests create fresh instances", async ({ page, request }) => {
  await page.setViewportSize({ width: 2200, height: 1600 });
  const create = async (data: object) => (await request.post("/api/nodes", { data })).json();
  const box = await create({ type: "oaw.barracks", name: "Worker catalog", position: { x: 950, y: 180 } });
  const blueprint = await create({ type: "agent", name: "Researcher", parent_id: box.id, position: { x: 1200, y: 400 } });
  const resource = await create({ type: "sandbox", name: "Research workspace", equipment: { owner_id: blueprint.id, relationship: "execute" } });
  const caller = await create({ type: "agent", name: "Caller", position: { x: 180, y: 250 } });
  const barracksSkill = await create({ type: "oaw.barracks.summoner", name: "Recruiter", equipment: { owner_id: caller.id } });
  await page.goto("/");
  await page.getByRole("tab", { name: /^Tools/ }).click();
  await expect(page.getByRole("button", { name: "Create Summoning", exact: true })).toBeVisible();
  await page.getByRole("tab", { name: /^Agents/ }).click();
  await expect(page.getByRole("button", { name: "Create Summoning", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Equipment for Caller", exact: true }).click();
  const sourceHandle = page.locator(`[data-card-id="${barracksSkill.id}"] [data-handleid="boundary-right"]`);
  const targetHandle = page.locator(`[data-card-id="${box.id}"] [data-handleid="boundary-left"]`);
  const a = (await sourceHandle.boundingBox())!; const b = (await targetHandle.boundingBox())!;
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2); await page.mouse.down();
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 20 }); await page.mouse.up();
  await page.getByRole("button", { name: "Grant capability", exact: true }).click();
  await expect.poll(async () => (await (await request.get("/api/world")).json()).edges.some((e: { source: string; target: string }) => e.source === barracksSkill.id && e.target === box.id)).toBe(true);
  await page.locator(`[data-card-id="${box.id}"]`).getByRole("button", { name: "Open barracks", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Worker catalog workspace", exact: true });
  await expect(dialog.locator(".barracks-agents")).toContainText("Researcher");
  await dialog.getByRole("textbox", { name: "Task / follow-up", exact: true }).fill("Inspect the sources");
  await dialog.getByRole("button", { name: "Summon new instance", exact: true }).click();
  const instances = dialog.locator(".barracks-instances");
  await expect(instances).toContainText("succeeded");
  await dialog.getByRole("textbox", { name: "Task / follow-up", exact: true }).fill("Continue research");
  await instances.getByRole("button", { name: "Follow up", exact: true }).click();
  await expect(instances).toContainText("Mock response: Continue research");
  const snapshot = await (await request.get(`/api/nodes/${box.id}/summoning`)).json();
  const spawned = snapshot.instances[0];
  expect(spawned.entry_agent_id).not.toBe(blueprint.id);
  expect(spawned.node_ids).toHaveLength(2);
  const newResourceId = spawned.node_ids.find((id: string) => id !== spawned.entry_agent_id);
  expect(newResourceId).not.toBe(resource.id);
  await page.screenshot({ path: "../.open-agent-world/equipment-summoning.png" });
  await instances.getByRole("button", { name: "Reclaim instance", exact: true }).click();
  await expect(instances).toContainText("reclaimed");
  expect((await request.get(`/api/nodes/${newResourceId}`)).status()).toBe(404);
  expect((await request.get(`/api/nodes/${resource.id}`)).status()).toBe(200);
  await request.post("/api/nodes/batch-delete", { data: { node_ids: [box.id, blueprint.id, caller.id] } });
});
