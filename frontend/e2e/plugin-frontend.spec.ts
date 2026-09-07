import { expect, test } from "@playwright/test";

test("local plugin assets and settings work through the host window", async ({ page, request }) => {
  await page.setViewportSize({ width: 1800, height: 1200 });
  const api = "http://127.0.0.1:8017/api";
  const catalog = await (await request.get(`${api}/catalog`)).json();
  const definition = catalog.node_types.find((d: { id: string }) => d.id === "openai.codex.agent");
  expect(definition.frontend).toEqual({ settings: "settings" });
  const asset = await request.get(`http://127.0.0.1:8017${definition.icon_url}`);
  expect(asset.ok()).toBeTruthy();
  expect(asset.headers()["content-type"]).toBe("image/svg+xml");
  expect(await asset.text()).toContain("<svg");
  const response = await request.post(`${api}/nodes`, { data: {
    type: definition.id, name: "Plugin UI check", position: { x: 700, y: 500 },
  } });
  expect(response.ok()).toBeTruthy();
  const card = await response.json();
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    await page.goto("/");
    const surface = page.locator(`[data-card-id="${card.id}"]`);
    await expect(surface).toBeVisible();
    await expect(surface.locator(".card-kind-icon .catalog-asset-icon")).toHaveCSS("mask-image", /\/api\/plugins\/openai.codex\/assets\/logo/);
    await surface.click({ position: { x: 150, y: 30 } });
    await surface.getByRole("button", { name: "Open workspace", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Plugin UI check workspace", exact: true });
    await dialog.getByRole("tab", { name: "Settings", exact: true }).click();
    await expect(dialog.getByLabel("Local Codex", { exact: true })).toHaveValue("auto");
    await dialog.getByLabel("Reasoning effort", { exact: true }).selectOption("high");
    await expect.poll(async () => (await (await request.get(`${api}/nodes/${card.id}`)).json()).config.reasoning_effort).toBe("high");
    await expect(dialog.getByRole("button", { name: "Run agent", exact: true })).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Stop", exact: true })).toBeVisible();
    await page.screenshot({ path: "../.outputs/plugin-frontend.png", fullPage: true });
    await dialog.getByRole("button", { name: "Close workspace", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    expect(errors).toEqual([]);
  } finally {
    await request.delete(`${api}/nodes/${card.id}`);
  }
});
