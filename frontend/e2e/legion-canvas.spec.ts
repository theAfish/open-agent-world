import { expect, test, type APIRequestContext, type Locator } from "@playwright/test";
import { resetTutorialProfile } from './tutorial-profile';

test.beforeEach(async ({ request, page }) => {
  await resetTutorialProfile(request);
  await page.emulateMedia({ reducedMotion: 'reduce' });
});

interface CreatedCard {
  id: string;
}

interface WorldResponse {
  nodes?: Array<{ id: string; name: string }>;
  cards?: Array<{ id: string; name: string }>;
}

async function createAgent(
  request: APIRequestContext,
  id: string,
  name: string,
  position: { x: number; y: number },
): Promise<CreatedCard> {
  const response = await request.post("/api/nodes", {
    data: { id, type: "agent", name, position },
  });
  expect(response.status()).toBe(201);
  return response.json() as Promise<CreatedCard>;
}

async function positionOf(request: APIRequestContext, id: string) {
  const response = await request.get(`/api/nodes/${id}`);
  expect(response.ok()).toBe(true);
  return ((await response.json()) as { position: { x: number; y: number } }).position;
}

async function selectRectangle(first: Locator, second: Locator) {
  const page = first.page();
  const [firstBox, secondBox] = await Promise.all([first.boundingBox(), second.boundingBox()]);
  if (!firstBox || !secondBox) throw new Error("Legion selection geometry is unavailable");
  const start = {
    x: Math.max(2, Math.min(firstBox.x, secondBox.x) - 18),
    y: Math.max(2, Math.min(firstBox.y, secondBox.y) - 18),
  };
  const end = {
    x: Math.max(firstBox.x + firstBox.width, secondBox.x + secondBox.width) + 18,
    y: Math.max(firstBox.y + firstBox.height, secondBox.y + secondBox.height) + 18,
  };
  await page.keyboard.down("Shift");
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(end.x, end.y, { steps: 8 });
  await page.mouse.up();
  await page.keyboard.up("Shift");
}

test("a Legion contains live members, shares state, moves as a team and preserves external links", async ({ page, request }) => {
  await page.setViewportSize({ width: 1800, height: 1100 });
  const suffix = `group-${Date.now()}`;
  const first = await createAgent(request, `${suffix}-planner`, "Planner", { x: 600, y: 200 });
  const second = await createAgent(request, `${suffix}-worker`, "Worker", { x: 930, y: 420 });
  const outside = await createAgent(request, `${suffix}-outside`, "Outside reviewer", { x: 1530, y: 320 });
  const external = await request.post("/api/edges", { data: { source: first.id, target: outside.id, relationship: "communicate" } });
  expect(external.status()).toBe(201);
  let groupId: string | undefined;
  try {
    await page.goto("/");
    await selectRectangle(page.locator(`[data-card-id="${first.id}"]`), page.locator(`[data-card-id="${second.id}"]`));
    await page.getByRole("button", { name: "Form Legion", exact: true }).click();
    const group = page.locator('[data-card-type="legion"]');
    await expect(group).toHaveCount(1);
    groupId = (await group.getAttribute("data-card-id"))!;
    await expect.poll(async () => (await (await request.get(`/api/nodes/${first.id}`)).json()).parent_id).toBe(groupId);
    await group.getByRole("button", { name: "Legion settings", exact: true }).click();
    const controls = page.getByRole("complementary", { name: "Legion settings", exact: true });
    await controls.getByLabel("Enable shared team settings").check();
    await controls.getByLabel("Team instruction", { exact: true }).fill("Plan, execute, then review the result.");
    await controls.getByRole("combobox", { name: "Team model override", exact: true }).click();
    await expect.poll(async () => (await (await request.get(`/api/nodes/${groupId}`)).json()).config.instruction).toContain("Plan, execute");
    await controls.getByRole("button", { name: "Add variable", exact: true }).click();
    await controls.getByLabel("Variable 1 name").fill("goal");
    await controls.getByLabel("Variable 1 value").fill("Prepare a report");
    await controls.getByRole("button", { name: "Save variables", exact: true }).click();
    await expect(controls.getByRole("button", { name: "Saved", exact: true })).toBeVisible();
    await controls.getByRole("button", { name: "Pause team", exact: true }).click();
    await expect(controls.getByRole("button", { name: "Resume team", exact: true })).toBeVisible();
    await controls.getByRole("button", { name: "Resume team", exact: true }).click();
    await controls.getByRole("button", { name: "Close Legion settings" }).click();
    const before = await positionOf(request, first.id);
    const bounds = await group.boundingBox();
    if (!bounds) throw new Error("Group not rendered");
    await page.mouse.move(bounds.x + 35, bounds.y + 35);
    await page.mouse.down();
    await page.mouse.move(bounds.x + 115, bounds.y + 85, { steps: 8 });
    await page.mouse.up();
    await expect.poll(async () => movement(await positionOf(request, first.id), before)).toBeGreaterThan(60);
    const edgeId = (await external.json()).id;
    await expect(page.locator(`path[data-edge-id="${edgeId}"]`)).toHaveCount(1);
    await page.screenshot({ path: test.info().outputPath("legion-team-light.png") });
    await page.getByRole("button", { name: "Use dark theme" }).click();
    await page.screenshot({ path: test.info().outputPath("legion-team-dark.png") });
    await page.getByRole("button", { name: "Use light theme" }).click();
    await page.reload();
    await group.getByRole("button", { name: "Legion settings", exact: true }).click();
    await expect(page.getByRole("complementary", { name: "Legion settings" }).getByLabel("Variable 1 value")).toHaveValue("Prepare a report");
    await page.getByRole("button", { name: "Close Legion settings" }).click();
    await expect(page.locator(`path[data-edge-id="${edgeId}"]`)).toHaveCount(1);
    const memberPosition = await positionOf(request, first.id);
    await group.getByRole("button", { name: "Dissolve", exact: true }).click();
    await expect(group).toHaveCount(0);
    expect((await (await request.get(`/api/nodes/${first.id}`)).json()).parent_id).toBeNull();
    expect(await positionOf(request, first.id)).toEqual(memberPosition);
    await expect(page.locator(`path[data-edge-id="${edgeId}"]`)).toHaveCount(1);
    await page.keyboard.press("Control+z");
    await expect(group).toHaveCount(1);
    await group.getByRole("button", { name: "Legion settings", exact: true }).click();
    await expect(page.getByRole("complementary", { name: "Legion settings" }).getByLabel("Variable 1 value")).toHaveValue("Prepare a report");
    await page.getByRole("button", { name: "Close Legion settings" }).click();
    await page.keyboard.press("Control+Shift+z");
    await expect(group).toHaveCount(0);
    await page.keyboard.press("Control+z");
    await expect(group).toHaveCount(1);
    await group.getByRole("button", { name: "Delete Legion and members", exact: true }).click();
    await expect(group).toHaveCount(0);
    await expect(page.locator(`[data-card-id="${first.id}"]`)).toHaveCount(0);
    await expect(page.locator(`[data-card-id="${second.id}"]`)).toHaveCount(0);
    await expect(page.locator(`[data-card-id="${outside.id}"]`)).toHaveCount(1);
    await page.keyboard.press("Control+z");
    await group.getByRole("button", { name: "Legion settings", exact: true }).click();
    await expect(page.getByRole("complementary", { name: "Legion settings" }).getByLabel("Variable 1 value")).toHaveValue("Prepare a report");
    await page.getByRole("button", { name: "Close Legion settings" }).click();
    await expect(page.locator(`path[data-edge-id="${edgeId}"]`)).toHaveCount(1);
    await page.keyboard.press("Control+Shift+z");
    await expect(group).toHaveCount(0);
    await page.keyboard.press("Control+z");
    await expect(group).toHaveCount(1);
    await group.getByRole("button", { name: "Legion settings", exact: true }).click();
    await page.getByRole("button", { name: "Detach Worker", exact: true }).click();
    await expect.poll(async () => (await (await request.get(`/api/nodes/${second.id}`)).json()).parent_id).toBeNull();
  } finally {
    await request.post("/api/nodes/batch-delete", { data: { node_ids: [first.id, second.id, outside.id, ...(groupId ? [groupId] : [])] } });
  }
});

function movement(position: { x: number; y: number }, origin: { x: number; y: number }) {
  return Math.hypot(position.x - origin.x, position.y - origin.y);
}

async function dragSelectedCard(card: Locator, delta: { x: number; y: number }) {
  const page = card.page();
  const box = await card.boundingBox();
  if (!box) throw new Error("Selected Legion card geometry is unavailable");
  const start = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(start.x + delta.x, start.y + delta.y, { steps: 8 });
  await page.mouse.up();
}

test("form a Legion, configure variables, then save and deploy an independent preset", async ({
  page,
  request,
}) => {
  const suffix = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
  const firstName = `E2E Legion Scout ${suffix}`;
  const secondName = `E2E Legion Analyst ${suffix}`;
  const legionName = `E2E Legion ${suffix}`;
  const first = await createAgent(request, `e2e-legion-first-${suffix}`, firstName, { x: 300, y: 180 });
  const second = await createAgent(request, `e2e-legion-second-${suffix}`, secondName, { x: 610, y: 350 });
  const relationship = await request.post("/api/edges", {
    data: { source: first.id, target: second.id, relationship: "communicate" },
  });
  expect(relationship.status()).toBe(201);
  let legionId: string | undefined;

  try {
    await page.goto("/");
    const firstCard = page.locator(`[data-card-id="${first.id}"]`);
    const secondCard = page.locator(`[data-card-id="${second.id}"]`);
    await expect(firstCard).toBeVisible();
    await expect(secondCard).toBeVisible();
    await selectRectangle(firstCard, secondCard);

    const selectionBar = page.getByTestId("legion-selection-bar");
    await expect(selectionBar).toContainText("2 selected");
    await expect(selectionBar).toContainText("1 internal link");
    await dragSelectedCard(firstCard, { x: 90, y: 55 });
    await expect.poll(async () => movement(await positionOf(request, first.id), { x: 300, y: 180 })).toBeGreaterThan(40);
    await expect.poll(async () => movement(await positionOf(request, second.id), { x: 610, y: 350 })).toBeGreaterThan(40);
    await selectionBar.getByRole("button", { name: "Form Legion", exact: true }).click();
    const group = page.locator('[data-card-type="legion"]');
    await expect(group).toHaveCount(1);
    const groupId = await group.getAttribute("data-card-id");
    await group.getByLabel("Legion name", { exact: true }).fill(legionName);
    await group.getByRole("button", { name: "Legion settings", exact: true }).click();
    const controls = page.getByRole("complementary", { name: "Legion settings", exact: true });
    await controls.getByLabel("Enable shared team settings").check();
    await controls.getByRole("button", { name: "Add variable", exact: true }).click();
    await controls.getByLabel("Variable 1 name").fill("goal");
    await controls.getByLabel("Variable 1 value").fill("Reusable team goal");
    await controls.getByRole("button", { name: "Close Legion settings" }).click();
    // Saving a preset must also persist the variable draft without a separate save.
    await group.getByRole("button", { name: "Save to library", exact: true }).click();

    await expect.poll(async () => {
      const response = await request.get("/api/legions");
      const legions = await response.json() as Array<{ id: string; name: string; node_count: number; edge_count: number }>;
      const legion = legions.find((item) => item.name === legionName);
      legionId = legion?.id;
      return legion ? { nodes: legion.node_count, edges: legion.edge_count } : undefined;
    }).toEqual({ nodes: 3, edges: 1 });
    expect((await (await request.get(`/api/legion-groups/${groupId}/state`)).json()).value).toEqual({ goal: "Reusable team goal" });

    await page.getByRole("tab", { name: /Legions/ }).click();
    const deployButton = page.getByRole("button", { name: `Place ${legionName}` });
    await expect(deployButton).toBeEnabled();
    const cards = page.locator(".react-flow__node-worldCard");
    const edges = page.locator("path.semantic-edge-path");
    await expect(cards).toHaveCount(2);
    await expect(edges).toHaveCount(1);
    await deployButton.dragTo(page.getByTestId("world-canvas"), {
      targetPosition: { x: 720, y: 420 },
    });
    await expect(cards).toHaveCount(4);
    await expect(edges).toHaveCount(2);
    await expect(page.locator('[data-card-type="legion"]')).toHaveCount(2);
    const deployedId = await page.locator(`[data-card-type="legion"]:not([data-card-id="${groupId}"])`).getAttribute("data-card-id");
    expect((await (await request.get(`/api/legion-groups/${deployedId}/state`)).json()).value).toEqual({ goal: "Reusable team goal" });

    await page.keyboard.press("Control+z");
    await expect(cards).toHaveCount(2);
    await expect(edges).toHaveCount(1);
    expect(movement(await positionOf(request, first.id), { x: 300, y: 180 })).toBeGreaterThan(40);
    expect(movement(await positionOf(request, second.id), { x: 610, y: 350 })).toBeGreaterThan(40);
  } finally {
    if (!legionId) {
      const response = await request.get("/api/legions");
      if (response.ok()) {
        const legions = await response.json() as Array<{ id: string; name: string }>;
        legionId = legions.find((item) => item.name === legionName)?.id;
      }
    }
    if (legionId) await request.delete(`/api/legions/${legionId}`);
    const world = await request.get("/api/world");
    if (world.ok()) {
      const body = await world.json() as WorldResponse;
      const nodes = body.nodes ?? body.cards ?? [];
      await request.post("/api/nodes/batch-delete", { data: { node_ids: nodes.filter((node) =>
        node.name === firstName || node.name === secondName || node.name === legionName).map((node) => node.id) } });
    }
  }
});


test("formation reserves its header above the existing member surfaces", async ({ page, request }) => {
  await page.setViewportSize({ width: 1800, height: 1100 });
  const suffix = `header-${Date.now()}`;
  const first = await createAgent(request, `${suffix}-first`, "Header first", { x: 600, y: 400 });
  const second = await createAgent(request, `${suffix}-second`, "Header second", { x: 1000, y: 460 });
  try {
    await page.goto('/');
    const firstCard = page.locator(`[data-card-id="${first.id}"]`);
    const secondCard = page.locator(`[data-card-id="${second.id}"]`);
    await expect(firstCard).toBeVisible();
    await expect(secondCard).toBeVisible();
    const before = [await firstCard.boundingBox(), await secondCard.boundingBox()];
    await selectRectangle(firstCard, secondCard);
    await page.getByRole('button', { name: 'Form Legion', exact: true }).click();
    const group = page.locator('[data-card-type="legion"]');
    await expect(group).toHaveCount(1);
    await expect.poll(async () => {
      const after = [await firstCard.boundingBox(), await secondCard.boundingBox()];
      return Math.max(...after.flatMap((box, i) => [Math.abs(box!.x - before[i]!.x), Math.abs(box!.y - before[i]!.y)]));
    }).toBeLessThan(1);
    const header = await group.locator(':scope > .container-header').boundingBox();
    expect(header!.y + header!.height).toBeLessThan(before[0]!.y);
    expect(await positionOf(request, first.id)).toEqual({ x: 600, y: 400 });
    await page.screenshot({ path: 'test-results/legion-header-preserves-layout.png' });
  } finally {
    const nodes = await (await request.get('/api/nodes')).json();
    const ids = nodes.filter((node: { id: string; type: string }) => node.id.startsWith(suffix) || node.type === 'legion').map((node: { id: string }) => node.id);
    if (ids.length) await request.post('/api/nodes/batch-delete', { data: { node_ids: ids } });
  }
});
