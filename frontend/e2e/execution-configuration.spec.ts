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
    await environment.getByRole("button", { name: "Add variable", exact: true }).click();
    await environment.getByLabel("Environment variable 1 name").fill("DEMO_REGION");
    await environment.getByLabel("Environment variable 1 value").fill("local-test");
    await environment.getByRole("button", { name: "Add variable", exact: true }).click();
    await environment.getByLabel("Environment variable 2 name").fill("API_TOKEN");
    await environment.getByLabel("Environment variable 2 type").selectOption("secret");
    await environment.getByLabel("Environment variable 2 value").fill("token");
    await environment.getByRole("button", { name: "Save", exact: true }).click();
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
    await environment.getByLabel("Environment variable 1 name").fill("LD_PRELOAD");
    await environment.getByRole("button", { name: "Save", exact: true }).click();
    await expect(environment.getByRole("alert")).toContainText("reserved");
    await environment.getByRole("button", { name: "Reload", exact: true }).click();
    await expect(environment.locator('input[value="DEMO_REGION"]')).toBeVisible();
    await environment.getByRole("button", { name: "Unbind", exact: true }).click();
    await expect(environment.getByText("token · Unbound", { exact: true })).toBeVisible();
    await environment.getByRole("button", { name: "Close Local environment inspector", exact: true }).click();
    const target = page.locator(`[data-card-id="${nodes[1]}"]`);
    await target.locator(".card-kind-icon").click();
    const config = { name: "Local test", provider_id: "example", config: { lanes: ["a", "b"], count: 2 } };
    await target.getByLabel("Import execution configuration JSON").setInputFiles({ name: "target.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(config)) });
    await expect(target.getByLabel("Provider ID", { exact: true })).toHaveValue("example");
    await target.getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(async () => (await (await request.get(`/api/nodes/${nodes[1]}/document`)).json()).value).toEqual(config);
    await page.screenshot({ path: testInfo.outputPath("compute-target.png") });
    await page.reload();
    await expect(target.getByLabel("Provider ID", { exact: true })).toHaveValue("example");
  } finally {
    for (const id of nodes) await request.delete(`/api/nodes/${id}`);
  }
});

test("Sandbox local settings reuse the environment editor and a live default profile", async ({ page, request }) => {
  const profile = await (await request.post("/api/nodes", { data: { type: "environment", name: "Shared defaults", position: { x: 200, y: 200 } } })).json();
  const sandbox = await (await request.post("/api/nodes", { data: { type: "sandbox", name: "Configured lab", position: { x: 500, y: 300 } } })).json();
  async function profileValue(variables: Record<string, string>) {
    const doc = await (await request.get(`/api/nodes/${profile.id}/document`)).json();
    expect((await request.post(`/api/nodes/${profile.id}/actions/replace`, { data: { arguments: { variables }, expected_revision: doc.revision } })).ok()).toBeTruthy();
  }
  try {
    await profileValue({ REGION: "base", SHARED: "one" });
    await page.goto("/");
    const card = page.locator(`[data-card-id="${sandbox.id}"]`);
    await card.locator(".card-kind-icon").click();
    await card.getByRole("button", { name: "Settings", exact: true }).click();
    const workspace = page.locator(`[data-workspace-node-id="${sandbox.id}"]`);
    await expect(workspace.getByRole("tab", { name: "Settings", exact: true })).toHaveAttribute("aria-selected", "true");
    await expect(workspace.getByRole("combobox", { name: "Default profile", exact: true })).not.toBeVisible();
    await workspace.getByText("Environment variables", { exact: true }).click();
    await workspace.getByRole("combobox", { name: "Default profile", exact: true }).selectOption(profile.id);
    await workspace.getByRole("button", { name: "Add variable", exact: true }).click();
    await workspace.getByLabel("Environment variable 1 name").fill("REGION");
    await workspace.getByLabel("Environment variable 1 value").fill("local");
    await workspace.getByRole("button", { name: "Save environment", exact: true }).click();
    const effective = () => request.get(`/api/sandboxes/${sandbox.id}/configuration`).then(r => r.json());
    await expect.poll(async () => (await effective()).variables.find((v: { name: string }) => v.name === "REGION")?.value).toBe("local");
    await workspace.locator("summary").filter({ hasText: "Effective values" }).click();
    await profileValue({ REGION: "changed-base", SHARED: "updated" });
    await expect(workspace.locator(".sandbox-variable").filter({ hasText: "SHARED" })).toContainText("updated");
    await workspace.getByRole("combobox", { name: "Default profile", exact: true }).selectOption("");
    await expect.poll(async () => (await effective()).profile_id).toBeNull();
    await expect.poll(async () => (await effective()).variables.length).toBe(1);
    expect((await (await request.get(`/api/sandboxes/${sandbox.id}/history`)).json()).length).toBe(0);
    await expect(workspace.getByLabel("Command", { exact: true })).not.toBeVisible();
    await workspace.getByRole("tab", { name: "Workspace", exact: true }).click();
    await expect(workspace.getByLabel("Command", { exact: true })).toHaveValue("");
    await expect(workspace.getByRole("button", { name: "Run command", exact: true })).toBeDisabled();
    const world = await (await request.get("/api/world")).json();
    expect(world.nodes.filter((n: { type: string }) => n.type === "environment")).toHaveLength(1);
  } finally {
    await request.delete(`/api/nodes/${sandbox.id}`);
    await request.delete(`/api/nodes/${profile.id}`);
  }
});
