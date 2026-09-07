import { expect, test } from "@playwright/test";

test("edit execution cards and bind secrets outside portable documents", async ({ page, request }, testInfo) => {
  await page.setViewportSize({ width: 1500, height: 1000 });
  const nodes: string[] = [];
  try {
    for (const [type, name, x] of [["environment", "Local environment", 420], ["compute-target", "Local destination", 950]] as const) {
      const response = await request.post("/api/nodes", { data: { type, name, position: { x, y: 340 } } });
      expect(response.status()).toBe(201);
      nodes.push((await response.json()).id);
    }
    await page.goto("/");
    const environment = page.locator(`[data-card-id="${nodes[0]}"]`);
    await environment.locator(".card-kind-icon").click();
    const editor = environment.getByLabel("Execution configuration JSON");
    await expect(editor).toHaveValue(/variables/);
    await editor.fill(JSON.stringify({ variables: { DEMO_REGION: "local-test", API_TOKEN: { secret_ref: "token" } } }, null, 2));
    await environment.getByRole("button", { name: "Save configuration", exact: true }).click();
    await expect(environment.getByText("token · Unbound", { exact: true })).toBeVisible();
    await environment.getByLabel("Secret for token").fill("e2e-private-credential");
    await environment.getByRole("button", { name: "Bind secret", exact: true }).click();
    await expect(environment.getByText("token · Configured", { exact: true })).toBeVisible();
    await expect(environment.getByLabel("Secret for token")).toHaveValue("");
    const document = await (await request.get(`/api/nodes/${nodes[0]}/document`)).json();
    expect(document.value.variables.API_TOKEN).toEqual({ secret_ref: "token" });
    expect(JSON.stringify(document)).not.toContain("e2e-private-credential");
    expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain("e2e-private-credential");
    await page.screenshot({ path: testInfo.outputPath("environment-profile.png") });
    await editor.fill('{"variables":{"LD_PRELOAD":"unsafe"}}');
    await environment.getByRole("button", { name: "Save configuration", exact: true }).click();
    await expect(environment.getByRole("alert")).toContainText("reserved");
    await environment.getByRole("button", { name: "Reload", exact: true }).click();
    await expect(editor).toHaveValue(/DEMO_REGION/);
    await environment.getByRole("button", { name: "Unbind", exact: true }).click();
    await expect(environment.getByText("token · Unbound", { exact: true })).toBeVisible();
    await environment.getByRole("button", { name: "Close Local environment inspector", exact: true }).click();
    const target = page.locator(`[data-card-id="${nodes[1]}"]`);
    await target.locator(".card-kind-icon").click();
    const config = { name: "Local test", provider_id: "example", config: { lanes: ["a", "b"], count: 2 } };
    await target.getByLabel("Execution configuration JSON").fill(JSON.stringify(config, null, 2));
    await target.getByRole("button", { name: "Save configuration", exact: true }).click();
    await expect.poll(async () => (await (await request.get(`/api/nodes/${nodes[1]}/document`)).json()).value).toEqual(config);
    await page.screenshot({ path: testInfo.outputPath("compute-target.png") });
    await page.reload();
    await target.locator(".card-kind-icon").click();
    await expect(target.getByLabel("Execution configuration JSON")).toHaveValue(/"lanes"/);
  } finally {
    for (const id of nodes) await request.delete(`/api/nodes/${id}`);
  }
});
