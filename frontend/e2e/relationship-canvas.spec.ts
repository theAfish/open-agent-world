import { expect, test, type APIRequestContext, type Locator } from "@playwright/test";

interface CreatedCard {
  id: string;
}

async function createCard(
  request: APIRequestContext,
  id: string,
  type: "agent" | "text" | "conversation",
  name: string,
  position: { x: number; y: number },
): Promise<CreatedCard> {
  const response = await request.post("/api/nodes", {
    data: { id, type, name, position },
  });
  expect(response.status()).toBe(201);
  return response.json() as Promise<CreatedCard>;
}

async function handleCenter(card: Locator, side: "left" | "right") {
  const box = await card.locator(`[data-connection-side="${side}"]`).boundingBox();
  if (!box) throw new Error(`The ${side} connection boundary is not visible`);
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

async function dragConnection(source: Locator, target: Locator) {
  const page = source.page();
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const start = await handleCenter(source, "right");
    const end = await handleCenter(target, "left");
    await page.mouse.move(start.x, start.y);
    await page.mouse.down();
    await page.mouse.move(end.x, end.y, { steps: 10 });
    await page.mouse.up();
    const dialog = page.getByRole("dialog", { name: "Choose a capability" });
    if (await dialog.isVisible()) return;
    await page.waitForTimeout(150);
  }
}

async function expectEndpointOnBoundary(endpoint: Locator, card: Locator) {
  await expect(async () => {
    const [point, rect] = await Promise.all([endpoint.boundingBox(), card.boundingBox()]);
    expect(point).not.toBeNull();
    expect(rect).not.toBeNull();
    if (!point || !rect) return;
    const centerX = point.x + point.width / 2;
    const centerY = point.y + point.height / 2;
    const distanceToOutline = Math.min(
      Math.abs(centerX - rect.x),
      Math.abs(centerX - (rect.x + rect.width)),
      Math.abs(centerY - rect.y),
      Math.abs(centerY - (rect.y + rect.height)),
    );
    expect(distanceToOutline).toBeLessThan(9);
  }).toPass();
}

test("an Agent relationship can be dragged between boundaries and exposes real endpoints", async ({
  page,
  request,
}) => {
  const suffix = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
  const source = await createCard(
    request,
    `e2e-source-${suffix}`,
    "agent",
    "E2E Coordinator",
    { x: 310, y: 150 },
  );
  const target = await createCard(
    request,
    `e2e-target-${suffix}`,
    "agent",
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

    await dragConnection(sourceCard, targetCard);

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

test("a reverse Text-to-Agent drag uses the allowed Agent-to-Text direction", async ({
  page,
  request,
}) => {
  const suffix = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
  const text = await createCard(
    request,
    `e2e-text-${suffix}`,
    "text",
    "E2E Text",
    { x: 310, y: 150 },
  );
  const agent = await createCard(
    request,
    `e2e-agent-${suffix}`,
    "agent",
    "E2E Agent",
    { x: 820, y: 420 },
  );

  try {
    await page.goto("/");
    const textCard = page.locator(`[data-card-id="${text.id}"]`);
    const agentCard = page.locator(`[data-card-id="${agent.id}"]`);
    await expect(textCard).toBeVisible();
    await expect(agentCard).toBeVisible();

    await dragConnection(textCard, agentCard);

    const dialog = page.getByRole("dialog", { name: "Choose a capability" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel("E2E Agent connects to E2E Text")).toBeVisible();
    await expect(dialog.getByText("Read", { exact: true })).toBeVisible();
    await dialog.getByRole("button", { name: "Grant capability" }).click();

    const edgeSelector = `[data-source-id="${agent.id}"][data-target-id="${text.id}"]`;
    await expect(page.locator(`path.semantic-edge-path${edgeSelector}`)).toHaveCount(1);
  } finally {
    await request.delete(`/api/nodes/${text.id}`);
    await request.delete(`/api/nodes/${agent.id}`);
  }
});

test("a preview stays open while the pointer uses its outer hover buffer", async ({ page, request }) => {
  const suffix = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
  const card = await createCard(
    request,
    `e2e-preview-${suffix}`,
    "agent",
    "E2E Hover Buffer",
    { x: 510, y: 280 },
  );

  try {
    await page.goto("/");
    const surface = page.locator(`[data-card-id="${card.id}"]`);
    const hoverHint = surface.locator("[data-connection-hover-hint]");
    await expect(surface).toBeVisible();
    await expect(surface).not.toHaveAttribute("data-connection-hot", "true");
    await surface.hover();
    await expect(surface).toHaveAttribute("data-surface-level", "preview");
    await expect(surface.locator("[data-preview-hover-buffer]")).toBeVisible();

    await page.waitForTimeout(350);
    const previewBox = await surface.boundingBox();
    if (!previewBox) throw new Error("Preview card geometry is unavailable");
    await page.mouse.move(previewBox.x + previewBox.width - 3, previewBox.y + previewBox.height / 2);
    await expect(surface).toHaveAttribute("data-connection-hot", "true");
    const hintBox = await hoverHint.boundingBox();
    if (!hintBox) throw new Error("Connection hover hint geometry is unavailable");
    expect(Math.abs(hintBox.x + hintBox.width / 2 - (previewBox.x + previewBox.width))).toBeLessThan(2);
    await page.mouse.move(previewBox.x + previewBox.width / 2, previewBox.y + previewBox.height / 2);
    await expect(surface).not.toHaveAttribute("data-connection-hot", "true");

    await page.mouse.move(previewBox.x + previewBox.width + 14, previewBox.y + previewBox.height / 2);
    await page.waitForTimeout(320);
    await expect(surface).toHaveAttribute("data-surface-level", "preview");

    await page.mouse.move(previewBox.x + previewBox.width + 36, previewBox.y + previewBox.height / 2);
    await expect(surface).toHaveAttribute("data-surface-level", "node", { timeout: 1000 });
  } finally {
    await request.delete(`/api/nodes/${card.id}`);
  }
});

test("a detail card uses the same boundary-following connection hint", async ({ page, request }) => {
  const suffix = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
  const card = await createCard(
    request,
    `e2e-detail-${suffix}`,
    "agent",
    "E2E Detail Hint",
    { x: 510, y: 280 },
  );

  try {
    await page.goto("/");
    const surface = page.locator(`[data-card-id="${card.id}"]`);
    const hoverHint = surface.locator("[data-connection-hover-hint]");
    await expect(surface).toBeVisible();
    await surface.click();
    await expect(surface).toHaveAttribute("data-surface-level", "inspector");

    await page.waitForTimeout(350);
    const inspectorBox = await surface.boundingBox();
    if (!inspectorBox) throw new Error("Detail card geometry is unavailable");
    await page.mouse.move(inspectorBox.x + inspectorBox.width - 3, inspectorBox.y + inspectorBox.height / 2);
    await expect(surface).toHaveAttribute("data-connection-hot", "true");
    const hintBox = await hoverHint.boundingBox();
    if (!hintBox) throw new Error("Detail connection hint geometry is unavailable");
    expect(Math.abs(hintBox.x + hintBox.width / 2 - (inspectorBox.x + inspectorBox.width))).toBeLessThan(2);
  } finally {
    await request.delete(`/api/nodes/${card.id}`);
  }
});

test("a Conversation workspace creates a group and routes explicit mentions", async ({ page, request }) => {
  const suffix = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
  const atlas = await createCard(request, `e2e-atlas-${suffix}`, "agent", "E2E Atlas", { x: 260, y: 180 });
  const river = await createCard(request, `e2e-river-${suffix}`, "agent", "E2E River", { x: 820, y: 180 });
  const conversation = await createCard(request, `e2e-conversation-${suffix}`, "conversation", "E2E Conversation", { x: 540, y: 390 });
  await request.post("/api/edges", { data: { source: atlas.id, target: conversation.id, relationship: "participate" } });
  await request.post("/api/edges", { data: { source: river.id, target: conversation.id, relationship: "participate" } });

  try {
    await page.goto("/");
    const conversationCard = page.locator(`[data-card-id="${conversation.id}"]`);
    await expect(conversationCard).toHaveAttribute("data-card-type", "conversation");
    await conversationCard.click();
    await expect(conversationCard).toHaveAttribute("data-surface-level", "inspector");
    await conversationCard.getByRole("button", { name: "Open workspace" }).click();

    const workspace = page.locator(`[data-workspace-node-id="${conversation.id}"]`);
    await expect(workspace).toBeVisible();
    await workspace.getByRole("button", { name: "New group" }).click();
    await workspace.getByLabel("Session name").fill("E2E Review Group");
    await workspace.getByLabel("E2E Atlas").check();
    await workspace.getByLabel("E2E River").check();
    await workspace.getByRole("button", { name: "Create group" }).click();

    await expect(workspace.getByText("E2E Review Group", { exact: true }).last()).toBeVisible();
    const composer = workspace.getByLabel("Conversation message");
    await composer.fill("@E2E Atlas and @E2E River compare this result");
    await composer.press("Shift+Enter");
    await composer.type("Include the second line");
    await expect(composer).toHaveValue("@E2E Atlas and @E2E River compare this result\nInclude the second line");
    await composer.press("Enter");
    await expect(composer).toHaveValue("");

    await expect(workspace.getByText("Mock response:", { exact: false })).toHaveCount(2, { timeout: 10000 });
    await expect(workspace.getByText("E2E Atlas", { exact: true }).last()).toBeVisible();
    await expect(workspace.getByText("E2E River", { exact: true }).last()).toBeVisible();

    await workspace.getByRole("button", { name: "Close workspace" }).click();
    await conversationCard.getByRole("button", { name: "Close E2E Conversation inspector" }).click();
    const atlasCard = page.locator(`[data-card-id="${atlas.id}"]`);
    await atlasCard.click();
    await atlasCard.getByRole("button", { name: "Open workspace" }).click();
    const agentWorkspace = page.locator(`[data-workspace-node-id="${atlas.id}"]`);
    await expect(agentWorkspace.getByText("Runtime history", { exact: true })).toBeVisible();
    await expect(agentWorkspace.getByText("E2E Review Group", { exact: true })).toBeVisible();
    await expect(agentWorkspace.getByLabel("Conversation message")).toHaveCount(0);
  } finally {
    await request.delete(`/api/nodes/${conversation.id}`);
    await request.delete(`/api/nodes/${atlas.id}`);
    await request.delete(`/api/nodes/${river.id}`);
  }
});
