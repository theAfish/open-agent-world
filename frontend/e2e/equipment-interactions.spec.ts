import { expect, test, type Locator } from "@playwright/test";

async function expectAttached(endpoint: Locator, surface: Locator) {
  await expect(async () => {
    const point = (await endpoint.boundingBox())!;
    const shape = await surface.evaluate((element) => {
      const box = element.getBoundingClientRect();
      const scale = box.width / (element as HTMLElement).offsetWidth;
      return { x: box.x, y: box.y, width: box.width, height: box.height,
        radius: Number.parseFloat(getComputedStyle(element).borderTopLeftRadius) * scale };
    });
    const radius = Math.min(shape.radius, shape.width / 2, shape.height / 2);
    const dx = Math.abs(point.x + point.width / 2 - shape.x - shape.width / 2) - (shape.width / 2 - radius);
    const dy = Math.abs(point.y + point.height / 2 - shape.y - shape.height / 2) - (shape.height / 2 - radius);
    const distance = Math.hypot(Math.max(dx, 0), Math.max(dy, 0)) + Math.min(Math.max(dx, dy), 0) - radius;
    expect(Math.abs(distance)).toBeLessThan(2);
  }).toPass();
}

test("equipment links follow visible boundaries and backpack controls stay clear of the card", async ({ page, request }) => {
  await page.setViewportSize({ width: 1800, height: 1200 });
  const create = async (data: object) => {
    const response = await request.post("/api/nodes", { data });
    expect(response.status()).toBe(201);
    return response.json();
  };
  const owner = await create({ type: "agent", name: "Equipment owner", position: { x: 500, y: 250 } });
  const external = await create({ type: "agent", name: "External reader", position: { x: 1000, y: 560 } });
  const item = await create({ type: "text", name: "Equipped notes", equipment: { owner_id: owner.id, relationship: "read" } });
  const edge = await request.post("/api/edges", { data: { source: external.id, target: item.id, relationship: "read" } });
  expect(edge.status()).toBe(201);
  try {
    await page.goto("/");
    const surface = (id: string) => page.locator(`[data-card-id="${id}"]`);
    const ownerSurface = surface(owner.id);
    const toggle = ownerSurface.getByRole("button", { name: "Equipment for Equipment owner", exact: true });
    const endpoint = page.locator(`[data-source-id="${external.id}"][data-target-id="${item.id}"][data-edge-endpoint="target"]`);
    await ownerSurface.getByRole("button", { name: "Collapse Equipment owner card", exact: true }).click();
    await expect(ownerSurface).toHaveAttribute("data-surface-level", "node");
    await expect(async () => {
      const { bounds, button } = await ownerSurface.evaluate((element) => ({
        bounds: element.getBoundingClientRect().toJSON(),
        button: element.querySelector(":scope > .equipment-toggle")!.getBoundingClientRect().toJSON(),
      }));
      expect(bounds.width).toBeLessThan(98);
      expect(button.x).toBeGreaterThan(bounds.x + bounds.width + 4);
      expect(button.width).toBeGreaterThanOrEqual(27);
    }).toPass();
    await page.getByRole("button", { name: "Use dark theme", exact: true }).click();
    await expectAttached(endpoint, ownerSurface);
    for (let cycle = 0; cycle < 3; cycle += 1) {
      await toggle.click();
      await expect(surface(item.id)).toBeVisible();
      await expectAttached(endpoint, surface(item.id));
      await page.mouse.move(30, 150);
      if (cycle === 0) await page.screenshot({ path: "../.open-agent-world/equipment-boundaries.png" });
      if (cycle === 2) await page.getByRole("button", { name: "Close equipment slots", exact: true }).click();
      else await toggle.click();
      await expect(surface(item.id)).toHaveCount(0);
      await expectAttached(endpoint, ownerSurface);
    }
    await ownerSurface.click({ position: { x: 45, y: 40 } });
    await expect(ownerSurface).toHaveAttribute("data-surface-level", "inspector");
    await expect(ownerSurface.locator(".card-footer .equipment-toggle")).toBeVisible();
    await expect(ownerSurface.locator(".card-id")).toHaveCount(0);
    await expectAttached(endpoint, ownerSurface);
    await toggle.click();
    await expectAttached(endpoint, surface(item.id));
    await page.mouse.move(30, 150);
    await page.screenshot({ path: "../.open-agent-world/equipment-inspector-toggle.png" });
    const before = (await ownerSurface.boundingBox())!;
    await page.mouse.move(before.x + 50, before.y + 30);
    await page.mouse.down();
    await page.mouse.move(before.x + 130, before.y + 70, { steps: 15 });
    await page.mouse.up();
    await expectAttached(endpoint, surface(item.id));
    await toggle.click();
    await expectAttached(endpoint, ownerSurface);
    const stored = await (await request.get("/api/world")).json();
    expect(stored.edges.some((link: { source: string; target: string }) => link.source === external.id && link.target === item.id)).toBe(true);
  } finally {
    await request.post("/api/nodes/batch-delete", { data: { node_ids: [owner.id, external.id] } });
  }
});

test("deck cards drop straight into equipment and the whole slot opens a stable inspector", async ({ page, request }) => {
  await page.setViewportSize({ width: 1800, height: 1200 });
  const owner = await (await request.post("/api/nodes", { data: { type: "agent", name: "Deck recipient", position: { x: 500, y: 250 } } })).json();
  try {
    await page.goto("/");
    const toggle = page.getByRole("button", { name: "Equipment for Deck recipient", exact: true });
    await toggle.click();
    const panel = page.locator(`[data-equipment-panel="${owner.id}"]`);
    await page.getByRole("tab", { name: /^Fields/ }).click();
    const [response] = await Promise.all([
      page.waitForResponse((result) => result.url().endsWith("/api/nodes") && result.request().method() === "POST", { timeout: 7000 }),
      page.getByRole("button", { name: "Create Conversation", exact: true }).dragTo(panel, { targetPosition: { x: 235, y: 110 }, timeout: 7000 }),
    ]);
    expect(response.status()).toBe(201);
    expect(response.request().postDataJSON().equipment.owner_id).toBe(owner.id);
    const item = await response.json();
    expect(item.equipment.owner_id).toBe(owner.id);
    const slot = page.locator(`[data-card-id="${item.id}"]`);
    await expect(slot).toHaveClass(/equipment-card/);
    // Empty space beside the title is an equally valid click target.
    await slot.click({ position: { x: 150, y: 30 } });
    await expect(slot).toHaveAttribute("data-surface-level", "inspector");
    await expect(page.locator(`[data-equipment-origin="${item.id}"]`)).toBeVisible();
    await expect(page.locator(`[data-surface-bridge="${item.id}"]`)).toHaveCount(1);
    await toggle.click();
    await expect(slot).toHaveCount(0);
    await toggle.click();
    await slot.click({ position: { x: 150, y: 30 } });
    await expect(slot).toHaveAttribute("data-surface-level", "inspector");
    await slot.getByRole("button", { name: "Open workspace", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: `${item.name} workspace`, exact: true });
    await expect(dialog).toBeVisible();
    await page.getByRole("button", { name: "Close workspace", exact: true }).click();
    await expect(slot).toHaveAttribute("data-surface-level", "inspector");
    await slot.getByRole("button", { name: `Close ${item.name} inspector`, exact: true }).click();
    await expect(slot).toHaveClass(/equipment-card/);
    await page.keyboard.press("Control+z");
    await expect(slot).toHaveCount(0);
    await expect.poll(async () => (await request.get(`/api/nodes/${item.id}`)).status()).toBe(404);
    await page.keyboard.press("Control+Shift+z");
    await expect(slot).toHaveClass(/equipment-card/);
    expect((await (await request.get(`/api/nodes/${item.id}`)).json()).equipment.owner_id).toBe(owner.id);
  } finally {
    await request.post("/api/nodes/batch-delete", { data: { node_ids: [owner.id] } });
  }
});
