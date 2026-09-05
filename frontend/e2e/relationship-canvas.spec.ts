import { expect, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";

interface CreatedCard {
  id: string;
}

interface PersistedCard {
  position: { x: number; y: number };
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

async function panCanvas(page: Page, direction: "left" | "right", times: number) {
  const pane = page.locator(".react-flow__pane");
  await expect(pane).toBeVisible();
  const box = await pane.boundingBox();
  if (!box) throw new Error("Canvas pane geometry is unavailable");
  const y = box.y + box.height * 0.72;
  const left = box.x + box.width * 0.32;
  const right = box.x + box.width * 0.72;

  for (let index = 0; index < times; index += 1) {
    await page.mouse.move(direction === "left" ? right : left, y);
    await page.mouse.down();
    await page.mouse.move(direction === "left" ? left : right, y, { steps: 5 });
    await page.mouse.up();
  }
}

async function cardBox(card: Locator, label: string) {
  const box = await card.boundingBox();
  if (!box) throw new Error(`${label} geometry is unavailable`);
  return box;
}

function pointDistance(
  first: { x: number; y: number },
  second: { x: number; y: number },
) {
  return Math.hypot(first.x - second.x, first.y - second.y);
}

async function persistedPosition(request: APIRequestContext, id: string) {
  const response = await request.get(`/api/nodes/${id}`);
  expect(response.ok()).toBe(true);
  return ((await response.json()) as PersistedCard).position;
}

async function expectPersistedMovement(
  request: APIRequestContext,
  id: string,
  original: { x: number; y: number },
  minimumDistance = 45,
) {
  let latest = original;
  await expect.poll(async () => {
    latest = await persistedPosition(request, id);
    return pointDistance(latest, original);
  }).toBeGreaterThan(minimumDistance);
  return latest;
}

async function dragCardBy(
  card: Locator,
  delta: { x: number; y: number },
  options: {
    holdBeforeMoveMs?: number;
    expectedLevelWhilePressed?: "node" | "preview" | "inspector" | "workspace";
    startRatio?: { x: number; y: number };
  } = {},
) {
  const page = card.page();
  const before = await cardBox(card, "Card drag start");
  const ratio = options.startRatio ?? { x: 0.5, y: 0.5 };
  const start = {
    x: before.x + before.width * ratio.x,
    y: before.y + before.height * ratio.y,
  };
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  if (options.holdBeforeMoveMs) {
    await page.waitForTimeout(options.holdBeforeMoveMs);
  }
  if (options.expectedLevelWhilePressed) {
    await expect(card).toHaveAttribute("data-surface-level", options.expectedLevelWhilePressed);
  }
  await page.mouse.move(start.x + delta.x, start.y + delta.y, { steps: 8 });
  await page.mouse.up();
  await expect.poll(async () => {
    const after = await cardBox(card, "Card drag result");
    return pointDistance(after, before);
  }).toBeGreaterThan(Math.min(50, pointDistance(delta, { x: 0, y: 0 }) * 0.45));
  return { before, after: await cardBox(card, "Settled card drag result") };
}

test("procedural terrain streams deterministic chunks across distant canvas coordinates", async ({ page }) => {
  await page.goto("/");
  const chunks = page.locator("svg.contour-chunk");
  await expect(chunks).toHaveCount(9);
  await expect(page.locator('svg.contour-chunk[data-chunk="0:0"] path.contour')).not.toHaveCount(0);

  await panCanvas(page, "left", 5);
  await expect.poll(async () => chunks.evaluateAll((elements) => elements.map((element) => (
    Number(element.getAttribute("data-chunk")?.split(":")[0])
  )).some((x) => x >= 2))).toBe(true);
  await expect(chunks.locator("path.contour").first()).toHaveAttribute("d", /M/);

  await panCanvas(page, "right", 10);
  await expect.poll(async () => chunks.evaluateAll((elements) => elements.map((element) => (
    Number(element.getAttribute("data-chunk")?.split(":")[0])
  )).some((x) => x <= -2))).toBe(true);
  await expect(chunks.locator("path.contour").first()).toHaveAttribute("d", /M/);
});

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

test("compact connection endpoints stay fixed throughout dragging and cancellation", async ({ page, request }) => {
  const suffix = Date.now();
  const source = await createCard(request, `e2e-fixed-source-${suffix}`, "agent", "E2E Fixed Source", { x: 310, y: 150 });
  const target = await createCard(request, `e2e-fixed-target-${suffix}`, "agent", "E2E Fixed Target", { x: 820, y: 420 });
  try {
    await page.goto("/");
    const sourceCard = page.locator(`[data-card-id="${source.id}"]`);
    const targetCard = page.locator(`[data-card-id="${target.id}"]`);
    await sourceCard.getByRole("button", { name: "Collapse E2E Fixed Source card" }).click();
    await targetCard.getByRole("button", { name: "Collapse E2E Fixed Target card" }).click();
    await page.waitForTimeout(450);
    const sourceBefore = await cardBox(sourceCard, "Source before connection");
    const targetBefore = await cardBox(targetCard, "Target before connection");
    const start = await handleCenter(sourceCard, "right");
    const end = await handleCenter(targetCard, "left");
    await page.mouse.move(start.x, start.y);
    await page.mouse.down();
    await page.mouse.move(end.x, end.y, { steps: 10 });
    await page.waitForTimeout(600);
    await expect(sourceCard).toHaveAttribute("data-surface-level", "node");
    await expect(targetCard).toHaveAttribute("data-surface-level", "node");
    expect(await cardBox(sourceCard, "Source during connection")).toEqual(sourceBefore);
    expect(await cardBox(targetCard, "Target during connection")).toEqual(targetBefore);
    await page.mouse.move(end.x + 100, end.y + 100);
    await page.mouse.up();
    await page.waitForTimeout(400);
    await expect(sourceCard).toHaveAttribute("data-surface-level", "node");
    await expect(targetCard).toHaveAttribute("data-surface-level", "node");
    expect(await cardBox(sourceCard, "Source after cancellation")).toEqual(sourceBefore);
    await dragConnection(sourceCard, targetCard);
    await expect(page.getByRole("dialog", { name: "Choose a capability" })).toBeVisible();
    await expect(sourceCard).toHaveAttribute("data-surface-level", "node");
    await expect(targetCard).toHaveAttribute("data-surface-level", "node");
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

test("close compact nodes remain independently draggable and persist both positions", async ({ page, request }) => {
  const suffix = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
  const firstPosition = { x: 350, y: 170 };
  const secondPosition = { x: 460, y: 280 };
  const first = await createCard(
    request,
    `e2e-close-first-${suffix}`,
    "agent",
    "E2E Close First",
    firstPosition,
  );
  const second = await createCard(
    request,
    `e2e-close-second-${suffix}`,
    "agent",
    "E2E Close Second",
    secondPosition,
  );

  try {
    await page.goto("/");
    const firstCard = page.locator(`[data-card-id="${first.id}"]`);
    const secondCard = page.locator(`[data-card-id="${second.id}"]`);
    await firstCard.getByRole("button", { name: "Collapse E2E Close First card" }).click();
    await secondCard.getByRole("button", { name: "Collapse E2E Close Second card" }).click();
    await expect(firstCard).toHaveAttribute("data-surface-level", "node");
    await expect(secondCard).toHaveAttribute("data-surface-level", "node");

    await dragCardBy(firstCard, { x: -120, y: 70 }, { expectedLevelWhilePressed: "node", startRatio: { x: 0.5, y: 0.75 } });
    await expectPersistedMovement(request, first.id, firstPosition);

    await dragCardBy(secondCard, { x: 115, y: 75 }, { expectedLevelWhilePressed: "node", startRatio: { x: 0.5, y: 0.75 } });
    await expectPersistedMovement(request, second.id, secondPosition);

    await page.reload();
    await expect(firstCard).toHaveAttribute("data-surface-level", "node");
    await expect(secondCard).toHaveAttribute("data-surface-level", "node");
    expect(pointDistance(await persistedPosition(request, first.id), firstPosition)).toBeGreaterThan(45);
    expect(pointDistance(await persistedPosition(request, second.id), secondPosition)).toBeGreaterThan(45);
  } finally {
    await request.delete(`/api/nodes/${first.id}`);
    await request.delete(`/api/nodes/${second.id}`);
  }
});

test("surfaces change only explicitly and restore their previous level", async ({ page, request }, testInfo) => {
  const card = await createCard(request, `e2e-surfaces-${Date.now()}`, "agent", "E2E Surfaces", { x: 510, y: 280 });
  try {
    await page.goto("/");
    const surface = page.locator(`[data-card-id="${card.id}"]`);
    await expect(surface).toHaveAttribute("data-surface-level", "preview");
    await surface.hover();
    await page.mouse.move(20, 20);
    await page.waitForTimeout(600);
    await expect(surface).toHaveAttribute("data-surface-level", "preview");
    await page.screenshot({ path: testInfo.outputPath("preview-node.png") });
    await surface.getByRole("button", { name: "Collapse E2E Surfaces card" }).click();
    await expect(surface).toHaveAttribute("data-surface-level", "node");
    await page.waitForTimeout(450);
    await surface.hover({ position: { x: 48, y: 72 } });
    await page.waitForTimeout(600);
    await expect(surface).toHaveAttribute("data-surface-level", "node");
    const expandButton = surface.getByRole("button", { name: "Expand E2E Surfaces card" });
    await expect(expandButton).toHaveCSS("opacity", "1");
    const buttonBox = await cardBox(expandButton, "Compact expand button");
    for (const region of await surface.locator(".card-kind-icon, .card-title-group, .card-status, .react-flow__handle").all()) {
      const regionBox = await cardBox(region, "Node content or connection region");
      expect(buttonBox.x >= regionBox.x + regionBox.width || buttonBox.x + buttonBox.width <= regionBox.x
        || buttonBox.y >= regionBox.y + regionBox.height || buttonBox.y + buttonBox.height <= regionBox.y).toBe(true);
    }
    // Traverse the hover-only control with real pointer events; it must remain reachable.
    await page.mouse.move(buttonBox.x + buttonBox.width / 2, buttonBox.y + buttonBox.height / 2, { steps: 12 });
    await expect(expandButton).toHaveCSS("opacity", "1");
    await expect(surface).not.toHaveAttribute("data-connection-hot", "true");
    await page.screenshot({ path: testInfo.outputPath("compact-node.png") });
    await surface.click({ position: { x: 48, y: 72 } });
    await expect(surface).toHaveAttribute("data-surface-level", "inspector");
    await surface.getByRole("button", { name: "Open workspace" }).click();
    await expect(surface).toHaveAttribute("data-surface-level", "workspace");
    await surface.getByRole("button", { name: "Close workspace" }).click();
    await expect(surface).toHaveAttribute("data-surface-level", "inspector");
    await surface.getByRole("button", { name: "Close E2E Surfaces inspector" }).click();
    await expect(surface).toHaveAttribute("data-surface-level", "node");
    await page.reload();
    await expect(surface).toHaveAttribute("data-surface-level", "node");
    await surface.hover({ position: { x: 48, y: 72 } });
    await surface.getByRole("button", { name: "Expand E2E Surfaces card" }).click();
    await expect(surface).toHaveAttribute("data-surface-level", "preview");
    await page.waitForTimeout(450);
    await surface.click({ modifiers: ["Shift"] });
    await expect(surface).toHaveAttribute("data-surface-level", "preview");
    await surface.click();
    await expect(surface).toHaveAttribute("data-surface-level", "inspector");
    await page.keyboard.press("Escape");
    await expect(surface).toHaveAttribute("data-surface-level", "preview");
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

test("detail and workspace surfaces are draggable canvas nodes while controls remain usable", async ({ page, request }) => {
  const suffix = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
  const card = await createCard(
    request,
    `e2e-draggable-surface-${suffix}`,
    "agent",
    "E2E Draggable Surface",
    { x: 510, y: 280 },
  );

  try {
    await page.goto("/");
    const surface = page.locator(`[data-card-id="${card.id}"]`);
    await surface.click();
    await expect(surface).toHaveAttribute("data-surface-level", "inspector");
    const section = surface.locator(".card-section").first();
    const detailBefore = await surface.boundingBox();
    const sectionBox = await section.boundingBox();
    if (!detailBefore || !sectionBox) throw new Error("Detail drag geometry is unavailable");
    await page.mouse.move(sectionBox.x + 18, sectionBox.y + 14);
    await page.mouse.down();
    await page.mouse.move(sectionBox.x + 138, sectionBox.y + 52, { steps: 5 });
    await page.mouse.up();
    await expect.poll(async () => Math.abs(((await surface.boundingBox())?.x ?? detailBefore.x) - detailBefore.x)).toBeGreaterThan(50);

    await surface.getByRole("button", { name: "Open workspace" }).click();
    const workspace = page.locator(`[data-workspace-node-id="${card.id}"]`);
    await expect(workspace).toBeVisible();
    await expect(workspace.locator("xpath=ancestor::div[contains(@class, 'react-flow__node-worldCard')]")).toHaveCount(1);
    await page.waitForTimeout(450);
    const workspaceBefore = await workspace.boundingBox();
    const workspaceTitle = workspace.getByText("E2E Draggable Surface", { exact: true });
    await expect(workspaceTitle).toBeVisible();
    const titleBox = await workspaceTitle.boundingBox();
    if (!workspaceBefore || !titleBox) throw new Error("Workspace drag geometry is unavailable");
    await page.mouse.move(titleBox.x + titleBox.width / 2, titleBox.y + titleBox.height / 2);
    await page.mouse.down();
    await page.mouse.move(titleBox.x + titleBox.width / 2 + 80, titleBox.y + titleBox.height / 2 + 40, { steps: 5 });
    await page.mouse.up();
    await expect.poll(async () => Math.abs(((await workspace.boundingBox())?.x ?? workspaceBefore.x) - workspaceBefore.x)).toBeGreaterThan(35);
  } finally {
    await request.delete(`/api/nodes/${card.id}`);
  }
});

test("inspector-displaced compact and preview surfaces can be dragged without hover upgrades or rebound", async ({
  page,
  request,
}) => {
  const suffix = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
  const inspectorPosition = { x: 400, y: 260 };
  const compactPosition = { x: 560, y: 170 };
  const previewPosition = { x: 560, y: 390 };
  const inspector = await createCard(
    request,
    `e2e-reflow-inspector-${suffix}`,
    "agent",
    "E2E Reflow Inspector",
    inspectorPosition,
  );
  const compact = await createCard(
    request,
    `e2e-reflow-compact-${suffix}`,
    "agent",
    "E2E Reflow Compact",
    compactPosition,
  );
  const preview = await createCard(
    request,
    `e2e-reflow-preview-${suffix}`,
    "agent",
    "E2E Reflow Preview",
    previewPosition,
  );

  try {
    await page.goto("/");
    const inspectorSurface = page.locator(`[data-card-id="${inspector.id}"]`);
    const compactSurface = page.locator(`[data-card-id="${compact.id}"]`);
    const previewSurface = page.locator(`[data-card-id="${preview.id}"]`);
    await compactSurface.getByRole("button", { name: "Collapse E2E Reflow Compact card" }).click();
    await expect(inspectorSurface).toHaveAttribute("data-surface-level", "preview");
    await expect(compactSurface).toHaveAttribute("data-surface-level", "node");
    await expect(previewSurface).toHaveAttribute("data-surface-level", "preview");

    await inspectorSurface.click();
    await expect(inspectorSurface).toHaveAttribute("data-surface-level", "inspector");
    // The inspector initially covers the compact node while its neighbors move out.
    // Wait for the exposed drag region before choosing a screen-space start point.
    await compactSurface.hover({ position: { x: 48, y: 72 } });

    const compactBefore = await cardBox(compactSurface, "Compact card before active reflow drag");
    const compactStart = {
      x: compactBefore.x + compactBefore.width / 2,
      y: compactBefore.y + compactBefore.height * 0.75,
    };
    await page.mouse.move(compactStart.x, compactStart.y);
    await page.mouse.down();
    await page.mouse.move(compactStart.x + 8, compactStart.y + 3, { steps: 2 });
    await page.waitForTimeout(230);
    await expect(compactSurface).toHaveAttribute("data-surface-level", "node");
    await page.mouse.move(compactStart.x + 190, compactStart.y + 55, { steps: 8 });
    await page.mouse.up();
    await expectPersistedMovement(request, compact.id, compactPosition);
    await expect.poll(async () => {
      const box = await cardBox(compactSurface, "Compact card after active reflow drag");
      return pointDistance(box, compactBefore);
    }).toBeGreaterThan(80);
    const compactAfter = await cardBox(compactSurface, "Compact card after drag");
    await page.waitForTimeout(500);
    const compactSettled = await cardBox(compactSurface, "Compact card after animation window");
    expect(pointDistance(compactSettled, compactAfter)).toBeLessThan(15);

    await previewSurface.hover();
    await expect(previewSurface).toHaveAttribute("data-surface-level", "preview");
    await expect.poll(async () => (await cardBox(previewSurface, "Displaced preview")).width).toBeGreaterThan(280);
    await page.waitForTimeout(420);
    const inspectorBox = await cardBox(inspectorSurface, "Inspector obstacle");
    const previewBefore = await cardBox(previewSurface, "Preview before drag");
    const rectanglesOverlap = !(
      previewBefore.x >= inspectorBox.x + inspectorBox.width
      || previewBefore.x + previewBefore.width <= inspectorBox.x
      || previewBefore.y >= inspectorBox.y + inspectorBox.height
      || previewBefore.y + previewBefore.height <= inspectorBox.y
    );
    expect(rectanglesOverlap).toBe(false);

    await dragCardBy(
      previewSurface,
      { x: 135, y: -45 },
      { expectedLevelWhilePressed: "preview", startRatio: { x: 0.5, y: 0.4 } },
    );
    await expectPersistedMovement(request, preview.id, previewPosition);
    const previewAfter = await cardBox(previewSurface, "Preview after drag");
    await page.waitForTimeout(500);
    const previewSettled = await cardBox(previewSurface, "Preview after animation window");
    expect(pointDistance(previewSettled, previewAfter)).toBeLessThan(15);
  } finally {
    await request.delete(`/api/nodes/${inspector.id}`);
    await request.delete(`/api/nodes/${compact.id}`);
    await request.delete(`/api/nodes/${preview.id}`);
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
    await workspace.getByRole("button", { name: "Create group" }).click();

    await expect(workspace.getByText("E2E Review Group", { exact: true }).last()).toBeVisible();
    await workspace.getByRole("button", { name: "Add agents to session" }).click();
    await workspace.getByLabel("Add E2E River to session").check();
    await workspace.getByRole("button", { name: "Add selected" }).click();
    await expect(workspace.getByText("2 active participants", { exact: true })).toBeVisible();

    const composer = workspace.getByLabel("Conversation message");
    await composer.fill("Please ask@E2E At");
    const mentionMenu = workspace.getByRole("listbox", { name: "Mention an Agent" });
    await expect(mentionMenu.getByRole("option")).toHaveCount(1);
    await expect(mentionMenu.getByRole("option", { name: /E2E Atlas/ })).toBeVisible();
    await composer.press("Enter");
    await composer.type("and @E2E Riv");
    await expect(mentionMenu.getByRole("option")).toHaveCount(1);
    await expect(mentionMenu.getByRole("option", { name: /E2E River/ })).toBeVisible();
    await composer.press("Enter");
    await composer.type("compare this result");
    await composer.press("Shift+Enter");
    await composer.type("Include the second line");
    await expect(composer).toHaveValue("Please ask@E2E Atlas and @E2E River compare this result\nInclude the second line");
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

test("a Conversation group can remove a participant and be dissolved", async ({ page, request }) => {
  const suffix = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
  const atlas = await createCard(request, `e2e-kick-atlas-${suffix}`, "agent", "E2E Kick Atlas", { x: 260, y: 180 });
  const river = await createCard(request, `e2e-kick-river-${suffix}`, "agent", "E2E Kick River", { x: 820, y: 180 });
  const conversation = await createCard(request, `e2e-kick-conversation-${suffix}`, "conversation", "E2E Kick Conversation", { x: 540, y: 390 });
  await request.post("/api/edges", { data: { source: atlas.id, target: conversation.id, relationship: "participate" } });
  await request.post("/api/edges", { data: { source: river.id, target: conversation.id, relationship: "participate" } });

  try {
    await page.goto("/");
    const conversationCard = page.locator(`[data-card-id="${conversation.id}"]`);
    await conversationCard.click();
    await conversationCard.getByRole("button", { name: "Open workspace" }).click();
    const workspace = page.locator(`[data-workspace-node-id="${conversation.id}"]`);
    await workspace.getByRole("button", { name: "New group" }).click();
    await workspace.getByLabel("Session name").fill("E2E Kick Group");
    await workspace.getByLabel("E2E Kick Atlas").check();
    await workspace.getByLabel("E2E Kick River").check();
    await workspace.getByRole("button", { name: "Create group" }).click();
    await expect(workspace.getByText("2 active participants", { exact: true })).toBeVisible();

    page.once("dialog", (dialog) => dialog.accept());
    await workspace.getByRole("button", { name: "Remove E2E Kick River from session" }).click();
    await expect(workspace.getByText("1 active participants", { exact: true })).toBeVisible();
    page.once("dialog", (dialog) => dialog.accept());
    await workspace.getByRole("button", { name: "Dissolve session" }).click();
    await expect(workspace.getByText("E2E Kick Group", { exact: true })).toHaveCount(0);
  } finally {
    await request.delete(`/api/nodes/${conversation.id}`);
    await request.delete(`/api/nodes/${atlas.id}`);
    await request.delete(`/api/nodes/${river.id}`);
  }
});
