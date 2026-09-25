import { expect, test, type Locator } from "@playwright/test";

async function selectRectangle(first: Locator, second: Locator) {
  const page = first.page();
  const [firstBox, secondBox] = await Promise.all([first.boundingBox(), second.boundingBox()]);
  if (!firstBox || !secondBox) throw new Error("Selection targets are not rendered");
  await page.keyboard.down("Shift");
  await page.mouse.move(Math.min(firstBox.x, secondBox.x) - 20, Math.min(firstBox.y, secondBox.y) - 20);
  await page.mouse.down();
  await page.mouse.move(
    Math.max(firstBox.x + firstBox.width, secondBox.x + secondBox.width) + 20,
    Math.max(firstBox.y + firstBox.height, secondBox.y + secondBox.height) + 20,
    { steps: 15 },
  );
  await page.mouse.up();
  await page.keyboard.up("Shift");
}

test("the selection trash button removes selected cards and their links in one undoable action", async ({ page, request }) => {
  await page.setViewportSize({ width: 1800, height: 1000 });
  const profile = await (await request.get("/api/application")).json();
  expect((await request.patch("/api/application/preferences", { data: {
    profile_id: profile.profile_id,
    generation: profile.generation,
    changes: {
      "oaw-onboarding-v1": JSON.stringify({ version: 1, state: { status: "skipped" } }),
      "oaw.locale": "en",
      "oaw-canvas-viewport-v1": null,
      "oaw-node-surfaces-v1": null,
    },
  } })).ok()).toBe(true);

  const ids: string[] = [];
  const edgeIds: string[] = [];
  try {
    for (const [name, x, y] of [
      ["Selected planner", 330, 300],
      ["Selected worker", 690, 440],
      ["Unselected reviewer", 1300, 340],
    ] as const) {
      const response = await request.post("/api/nodes", { data: { type: "agent", name, position: { x, y } } });
      expect(response.status()).toBe(201);
      ids.push((await response.json()).id);
    }
    for (const target of ids.slice(1)) {
      const response = await request.post("/api/edges", {
        data: { source: ids[0], target, relationship: "communicate" },
      });
      expect(response.status()).toBe(201);
      edgeIds.push((await response.json()).id);
    }

    await page.goto("/");
    const cards = ids.map((id) => page.locator(`[data-card-id="${id}"]`));
    for (const card of cards) await expect(card).toBeVisible();
    await selectRectangle(cards[0], cards[1]);
    await expect(cards[0]).toHaveClass(/is-selected/);
    await expect(cards[1]).toHaveClass(/is-selected/);
    await expect(cards[2]).not.toHaveClass(/is-selected/);
    const selectionBar = page.getByTestId("legion-selection-bar");
    await expect(selectionBar).toContainText("2 selected");
    await expect(selectionBar).toContainText("1 internal links");
    const remove = selectionBar.getByRole("button", { name: "Delete selected cards", exact: true });
    await expect(remove).toBeEnabled();
    await remove.click();

    for (const card of cards.slice(0, 2)) await expect(card).toHaveCount(0);
    await expect(cards[2]).toBeVisible();
    await expect(selectionBar).toHaveCount(0);
    for (const edgeId of edgeIds) await expect(page.locator(`path[data-edge-id="${edgeId}"]`)).toHaveCount(0);
    for (const id of ids.slice(0, 2)) expect((await request.get(`/api/nodes/${id}`)).status()).toBe(404);
    expect((await request.get(`/api/nodes/${ids[2]}`)).status()).toBe(200);

    await page.keyboard.press("Control+z");
    for (const card of cards) await expect(card).toBeVisible();
    for (const edgeId of edgeIds) await expect(page.locator(`path[data-edge-id="${edgeId}"]`)).toHaveCount(1);
    for (const id of ids) expect((await request.get(`/api/nodes/${id}`)).status()).toBe(200);
  } finally {
    if (ids.length) await request.post("/api/nodes/batch-delete", { data: { node_ids: ids } });
  }
});
