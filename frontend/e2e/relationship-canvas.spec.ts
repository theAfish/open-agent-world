import { expect, test, type APIRequestContext, type Locator } from "@playwright/test";

interface CreatedCard {
  id: string;
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

async function handleCenter(card: Locator, side: "left" | "right") {
  const box = await card.locator(`[data-connection-side="${side}"]`).boundingBox();
  if (!box) throw new Error(`The ${side} connection boundary is not visible`);
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

async function expectEndpointOnBoundary(endpoint: Locator, card: Locator) {
  const [point, rect] = await Promise.all([endpoint.boundingBox(), card.boundingBox()]);
  if (!point || !rect) throw new Error("Endpoint or card geometry is unavailable");
  const centerX = point.x + point.width / 2;
  const centerY = point.y + point.height / 2;
  const distanceToOutline = Math.min(
    Math.abs(centerX - rect.x),
    Math.abs(centerX - (rect.x + rect.width)),
    Math.abs(centerY - rect.y),
    Math.abs(centerY - (rect.y + rect.height)),
  );
  expect(distanceToOutline).toBeLessThan(9);
}

test("an Agent relationship can be dragged between boundaries and exposes real endpoints", async ({
  page,
  request,
}) => {
  const suffix = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
  const source = await createAgent(
    request,
    `e2e-source-${suffix}`,
    "E2E Coordinator",
    { x: 310, y: 150 },
  );
  const target = await createAgent(
    request,
    `e2e-target-${suffix}`,
    "E2E Researcher",
    { x: 820, y: 420 },
  );

  try {
    await page.goto("/");
    const sourceCard = page.locator(`[data-card-id="${source.id}"]`);
    const targetCard = page.locator(`[data-card-id="${target.id}"]`);
    await expect(sourceCard).toBeVisible();
    await expect(targetCard).toBeVisible();
    await expect(sourceCard).toHaveAttribute("data-card-type", "agent");

    const start = await handleCenter(sourceCard, "right");
    const end = await handleCenter(targetCard, "left");
    await page.mouse.move(start.x, start.y);
    await page.mouse.down();
    await page.mouse.move(end.x, end.y, { steps: 14 });
    await page.mouse.up();

    const dialog = page.getByRole("dialog", { name: "Choose a capability" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText("Communicate", { exact: true })).toBeVisible();
    await dialog.getByRole("button", { name: "Grant capability" }).click();

    const edgeSelector = `[data-source-id="${source.id}"][data-target-id="${target.id}"]`;
    const sourceEndpoint = page.locator(`circle[data-edge-endpoint="source"]${edgeSelector}`);
    const targetEndpoint = page.locator(`circle[data-edge-endpoint="target"]${edgeSelector}`);
    await expect(sourceEndpoint).toBeVisible();
    await expect(targetEndpoint).toBeVisible();
    await expect(page.locator(`path.semantic-edge-path${edgeSelector}`)).toHaveCount(1);
    await expectEndpointOnBoundary(sourceEndpoint, sourceCard);
    await expectEndpointOnBoundary(targetEndpoint, targetCard);
  } finally {
    await request.delete(`/api/nodes/${source.id}`);
    await request.delete(`/api/nodes/${target.id}`);
  }
});
