import { expect, test } from "@playwright/test";

test("a conversation workspace entering the screen during a pan does not scroll ancestors", async ({ page, request }) => {
  const id = `boundary-conversation-${Date.now()}`;
  expect((await request.post("/api/nodes", { data: { id, type: "conversation", name: "Boundary conversation", position: { x: 1742, y: 950 } } })).ok()).toBe(true);
  try {
    await page.addInitScript((nodeId) => {
      localStorage.setItem("oaw-node-surfaces-v1", JSON.stringify({ state: {
        surfaceLevels: { [nodeId]: "workspace" }, baseLevels: {}, maximizedWorkspaces: {},
      }, version: 3 }));
    }, id);
    await page.goto("/");
    await expect(page.locator(".contour-chunk").first()).toBeVisible();
    await page.evaluate(() => {
      (window as any).__canvasScrolls = [];
      document.addEventListener("scroll", (event) => {
        const target = event.target;
        if (target instanceof Element && target.matches(".react-flow, .react-flow__renderer, .world-canvas, .world-shell")) {
          (window as any).__canvasScrolls.push({ className: target.className, top: target.scrollTop, left: target.scrollLeft });
        }
      }, true);
    });
    await page.mouse.move(700, 500);
    await page.mouse.down();
    await page.mouse.move(700, 200, { steps: 30 });
    for (const x of [690, 710, 690, 710, 690]) {
      await page.mouse.move(x, 200, { steps: 20 });
    }
    await page.mouse.up();
    await page.waitForTimeout(200);
    expect(await page.evaluate(() => (window as any).__canvasScrolls)).toEqual([]);
  } finally {
    await request.delete(`/api/nodes/${id}`);
  }
});

test("horizontal movement across terrain seams after panning up does not scroll the canvas", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("oaw-canvas-viewport-v1", JSON.stringify({ state: { viewport: {
      x: 0, y: -2100, zoom: 1, width: 1280, height: 800,
    } }, version: 0 }));
  });
  await page.goto("/");
  await expect(page.locator(".contour-chunk").first()).toBeVisible();
  await page.evaluate(() => {
    (window as any).__canvasScrolls = [];
    document.addEventListener("scroll", (event) => {
      const target = event.target;
      if (target instanceof Element && target.closest(".world-canvas")) {
        (window as any).__canvasScrolls.push({ className: target.className, top: target.scrollTop, left: target.scrollLeft });
      }
    }, true);
  });
  await page.mouse.move(700, 500);
  await page.mouse.down();
  await page.mouse.move(700, 200, { steps: 30 });
  for (let cycle = 0; cycle < 3; cycle += 1) {
    for (const x of [710, 690, 710]) {
      await page.mouse.move(x, 200, { steps: 20 });
    }
  }
  await page.mouse.up();
  await page.waitForTimeout(200);
  expect(await page.evaluate(() => (window as any).__canvasScrolls)).toEqual([]);
});

test("canvas panning tracks the pointer at every screen boundary", async ({ page }) => {
  await page.goto("/");
  const viewport = page.locator(".react-flow__viewport");
  await expect(viewport).toBeVisible();
  const position = () => viewport.evaluate((element) => {
    const matrix = new DOMMatrix(getComputedStyle(element).transform);
    return { x: matrix.e, y: matrix.f };
  });
  for (const end of [{ x: 2, y: 400 }, { x: 1278, y: 400 }, { x: 640, y: 2 }, { x: 900, y: 798 }]) {
    const start = { x: 900, y: 400 };
    const before = await position();
    await page.mouse.move(start.x, start.y);
    await page.mouse.down();
    for (let step = 1; step <= 20; step += 1) {
      const delta = { x: (end.x - start.x) * step / 20, y: (end.y - start.y) * step / 20 };
      await page.mouse.move(start.x + delta.x, start.y + delta.y);
      const current = await position();
      expect(Math.abs(current.x - before.x - delta.x)).toBeLessThanOrEqual(1);
      expect(Math.abs(current.y - before.y - delta.y)).toBeLessThanOrEqual(1);
    }
    await page.mouse.up();
    const released = await position();
    await page.waitForTimeout(450);
    expect(await position()).toEqual(released);
  }
});

test("edge auto-pan keeps the dragged card under a stationary pointer", async ({ page, request }) => {
  const id = `boundary-drag-${Date.now()}`;
  expect((await request.post("/api/nodes", { data: { id, type: "text", name: "Boundary drag", position: { x: 450, y: 300 } } })).ok()).toBe(true);
  try {
    await page.goto("/");
    const card = page.locator(`[data-card-id="${id}"]`);
    await expect(card).toBeVisible();
    const box = await card.boundingBox();
    if (!box) throw new Error("Missing card bounds");
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(1270, 400, { steps: 30 });
    const samples = await card.evaluate(async (element) => {
      const points: number[] = [];
      for (let frame = 0; frame < 360; frame += 1) {
        await new Promise(requestAnimationFrame);
        points.push(element.getBoundingClientRect().x);
      }
      return points;
    });
    expect(Math.max(...samples) - Math.min(...samples)).toBeLessThan(25);
    await page.mouse.up();
    const released = await card.boundingBox();
    await page.waitForTimeout(600);
    const settled = await card.boundingBox();
    expect(settled?.x).toBeCloseTo(released!.x, 0);
  } finally {
    await request.delete(`/api/nodes/${id}`);
  }
});
