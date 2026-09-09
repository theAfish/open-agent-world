import { expect, test } from "@playwright/test";

for (const type of ["agent", "conversation", "text", "sandbox", "environment", "oaw.tasks", "oaw.skills.skill", "core.artifact-collection"]) {
  test(`${type} inspector selects text and drags from empty content`, async ({ page, request }) => {
    const response = await request.post("/api/nodes", { data: { type, name: "Drag and copy", position: { x: 550, y: 340 } } });
    expect(response.ok()).toBe(true);
    const { id } = await response.json();
    try {
      await page.addInitScript(nodeId => {
        localStorage.setItem("oaw-node-surfaces-v1", JSON.stringify({ state: {
          surfaceLevels: { [nodeId]: "inspector" }, baseLevels: {}, maximizedWorkspaces: {},
        }, version: 3 }));
      }, id);
      await page.goto("/");
      const card = page.locator(`[data-card-id="${id}"]`);
      await expect(card).toHaveAttribute("data-surface-level", "inspector");
      await page.waitForTimeout(500);
      const body = card.locator(".node-inspector-content");
      let text!: { x: number; y: number; width: number };
      await expect(async () => { text = await body.evaluate(element => {
        const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
        while (walker.nextNode()) {
          const node = walker.currentNode;
          if (!node.textContent?.trim() || node.textContent.trim().length < 6
            || node.parentElement?.closest("button, input, textarea, select, label, a, summary")) continue;
          const range = document.createRange(); range.selectNodeContents(node);
          const rect = range.getClientRects()[0];
          if (rect?.width > 25 && document.elementFromPoint(rect.x + 2, rect.y + rect.height / 2) === node.parentElement)
            return { x: rect.x + 1, y: rect.y + rect.height / 2, width: Math.min(rect.width - 2, 75) };
        }
        throw new Error("No visible selectable text");
      }); }).toPass({ timeout: 7000 });
      const before = (await card.boundingBox())!;
      await page.mouse.move(text.x, text.y); await page.mouse.down();
      await page.mouse.move(text.x + text.width, text.y, { steps: 10 }); await page.mouse.up();
      expect(await page.evaluate(() => window.getSelection()?.toString().trim().length)).toBeGreaterThan(0);
      expect(Math.abs((await card.boundingBox())!.x - before.x)).toBeLessThan(2);

      // Test both the body's padding and blank space inside a nested content block.
      for (const nested of [false, true]) {
        const point = await body.evaluate((element, nested) => {
          const box = element.getBoundingClientRect();
          for (let y = box.top + 3; y < box.bottom - 8; y += 7) for (let x = box.left + 8; x < box.right - 8; x += 7) {
            const target = document.elementFromPoint(x, y);
            if (!target || !element.contains(target) || (nested && target === element)
              || target.closest("button, input, textarea, select, label, a, summary, svg, .react-flow__handle, [role='button'], [role='separator']")) continue;
            const range = document.createRange();
            const hit = Array.from(target.childNodes).some(node => {
              if (node.nodeType !== Node.TEXT_NODE || !node.textContent?.trim()) return false;
              range.selectNodeContents(node);
              return Array.from(range.getClientRects()).some(r => x >= r.left && x <= r.right && y >= r.top && y <= r.bottom);
            });
            if (!hit) return { x, y };
          }
          throw new Error("No blank content area");
        }, nested);
        const start = (await card.boundingBox())!;
        await page.mouse.move(point.x, point.y); await page.mouse.down();
        await page.mouse.move(point.x + 55, point.y + 25, { steps: 10 }); await page.mouse.up();
        await expect.poll(async () => (await card.boundingBox())!.x - start.x).toBeGreaterThan(40);
      }
      await card.getByRole("button", { name: "Close Drag and copy inspector" }).click();
      await expect(card).not.toHaveAttribute("data-surface-level", "inspector");
    } finally { await request.delete(`/api/nodes/${id}`); }
  });
}
