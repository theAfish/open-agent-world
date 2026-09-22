import { expect, test } from "@playwright/test";

for (const type of ["agent", "sandbox"]) {
  test(`${type} window retains resized dimensions`, async ({ page, request }) => {
    await page.setViewportSize({ width: 1800, height: 1200 });
    const response = await request.post("/api/nodes", { data: { type, name: "Resizable window", position: { x: 700, y: 500 } } });
    expect(response.ok()).toBe(true);
    const { id } = await response.json();
    try {
      const profile = await (await request.get('/api/application')).json();
      expect((await request.patch('/api/application/preferences', { data: {
        profile_id: profile.profile_id, generation: profile.generation, changes: {
          'oaw-canvas-viewport-v1': null,
          'oaw-node-surfaces-v1': JSON.stringify({ state: { surfaceLevels: { [id]: 'workspace' } }, version: 3 }),
        },
      } })).ok()).toBe(true);
      await page.goto("/");
      const card = page.locator(`.world-card[data-card-id="${id}"]`);
      await expect(card).toHaveAttribute("data-surface-level", "workspace");
      await card.locator(".workspace-titlebar strong").click();
      const grip = page.locator(`[data-resize-node="${id}"][data-resize-corner="bottom-right"]`);
      await expect(grip).toBeVisible();
      await page.waitForTimeout(500);
      const before = (await card.boundingBox())!;
      const handle = (await grip.boundingBox())!;
      const x = handle.x + handle.width / 2, y = handle.y + handle.height / 2;
      await page.mouse.move(x, y);
      await page.mouse.down();
      await page.mouse.move(x + 120, y + 80, { steps: 12 });
      await page.mouse.up();
      await expect.poll(async () => (await card.boundingBox())!.width - before.width).toBeGreaterThan(100);
      const after = (await card.boundingBox())!;
      expect(after.height - before.height).toBeGreaterThan(60);
      expect(Math.abs(after.x - before.x)).toBeLessThan(2);
      expect(Math.abs(after.y - before.y)).toBeLessThan(2);
      await page.reload();
      await expect(card).toHaveAttribute("data-surface-level", "workspace");
      await expect.poll(async () => (await card.boundingBox())!.width).toBeCloseTo(after.width, 0);
      await expect.poll(async () => (await card.boundingBox())!.height).toBeCloseTo(after.height, 0);
    } finally { await request.delete(`/api/nodes/${id}`); }
  });
}
