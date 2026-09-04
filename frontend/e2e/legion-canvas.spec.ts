import { expect, test, type APIRequestContext, type Locator } from "@playwright/test";

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

test("a selected subgraph becomes a reusable Legion and deploys as one undoable topology", async ({
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
    await selectionBar.getByRole("button", { name: "Save as Legion" }).click();

    const dialog = page.getByRole("dialog", { name: "Create a Legion card" });
    await expect(dialog).toBeVisible();
    await dialog.getByLabel("Legion name").fill(legionName);
    await dialog.getByRole("button", { name: "Collect Legion" }).click();
    await expect(dialog).not.toBeVisible();

    await expect.poll(async () => {
      const response = await request.get("/api/legions");
      const legions = await response.json() as Array<{ id: string; name: string; node_count: number; edge_count: number }>;
      const legion = legions.find((item) => item.name === legionName);
      legionId = legion?.id;
      return legion ? { nodes: legion.node_count, edges: legion.edge_count } : undefined;
    }).toEqual({ nodes: 2, edges: 1 });

    await page.getByRole("tab", { name: /^Legions/ }).click();
    const deployButton = page.getByRole("button", { name: `Deploy Legion ${legionName}` });
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
      for (const node of nodes) {
        if (node.name === firstName || node.name === secondName) await request.delete(`/api/nodes/${node.id}`);
      }
    }
  }
});
