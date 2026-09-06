import { expect, test } from "@playwright/test";

test("task board edits dependencies, rejects cycles, stays live and restores after deletion", async ({ page, request }) => {
  await page.setViewportSize({ width: 1600, height: 1100 });
  const created = await request.post("/api/nodes", { data: { type: "oaw.tasks", name: "Research plan", position: { x: 710, y: 430 } } });
  expect(created.status()).toBe(201);
  const node = await created.json();
  const url = `/api/nodes/${node.id}`;
  try {
    await page.goto("/");
    let card = page.locator(`[data-card-id="${node.id}"]`);
    await card.locator(".card-kind-icon").click();
    await card.getByRole("button", { name: "Open workspace", exact: true }).click();
    let board = page.getByRole("dialog", { name: "Research plan workspace", exact: true });
    await board.getByLabel("New task title").fill("Collect sources");
    await board.getByRole("button", { name: "Add task", exact: true }).click();
    await board.getByLabel("New task title").fill("Write report");
    await board.getByRole("button", { name: "Add task", exact: true }).click();
    await board.getByRole("button", { name: "Edit task Write report", exact: true }).click();
    await board.getByLabel("Depends on Collect sources").check();
    await board.getByRole("button", { name: "Save task", exact: true }).click();
    await expect(board.getByRole("button", { name: "Complete Write report", exact: true })).toBeDisabled();
    await board.getByRole("button", { name: "Edit task Collect sources", exact: true }).click();
    await board.getByLabel("Depends on Write report").check();
    await board.getByRole("button", { name: "Save task", exact: true }).click();
    await expect(board.getByRole("alert")).toContainText("cycle");
    await expect(board.getByLabel("Depends on Write report")).toBeChecked();
    await board.getByLabel("Depends on Write report").uncheck();
    await board.getByRole("button", { name: "Save task", exact: true }).click();
    await board.getByRole("button", { name: "Complete Collect sources", exact: true }).click();
    await expect(board.getByRole("button", { name: "Complete Write report", exact: true })).toBeEnabled();
    // Agent/another client's state changes are visible without reloading the page.
    const current = await (await request.get(url + "/document")).json();
    const report = current.value.tasks.find((task: { title: string }) => task.title === "Write report");
    const updated = await request.post(url + "/actions/progress", { data: { arguments: { task_id: report.id, status: "doing", note: "Draft in progress" }, expected_revision: current.revision } });
    expect(updated.ok()).toBe(true);
    await expect(board.getByRole("button", { name: "Edit task Write report", exact: true })).toContainText("In progress");
    await board.getByRole("button", { name: "Edit task Write report", exact: true }).click();
    await expect(board.getByLabel("Progress note")).toHaveValue("Draft in progress");
    await page.screenshot({ path: "../.open-agent-world/task-board-light.png" });
    await page.getByRole("button", { name: "Use dark theme" }).click();
    await page.screenshot({ path: "../.open-agent-world/task-board-dark.png" });
    await page.getByRole("button", { name: "Use light theme" }).click();
    await board.getByRole("button", { name: "Close task details", exact: true }).click();
    await board.getByRole("button", { name: "Dependencies", exact: true }).click();
    await expect(board.getByRole("img", { name: "Prerequisites flow from left to right" })).toBeVisible();
    await page.screenshot({ path: "../.open-agent-world/task-board-dependencies.png" });
    await page.reload();
    board = page.getByRole("dialog", { name: "Research plan workspace", exact: true });
    await expect(board.getByRole("button", { name: "Edit task Write report", exact: true })).toBeVisible();
    await board.getByRole("button", { name: "Close workspace", exact: true }).click();
    card = page.locator(`[data-card-id="${node.id}"]`);
    await card.locator(".card-kind-icon").click();
    await card.getByRole("button", { name: "Remove Research plan", exact: true }).click();
    await expect(card).toHaveCount(0);
    await page.getByRole("button", { name: "Undo last canvas action", exact: true }).click();
    await expect(card).toHaveCount(1);
    const restored = await (await request.get(url + "/document")).json();
    expect(restored.value.tasks).toHaveLength(2);
    expect(restored.value.tasks.find((task: { id: string }) => task.id === report.id).note).toBe("Draft in progress");
  } finally { await request.delete(url); }
});

test("optional execution settings dispatch connected agents and show durable results", async ({ page, request }) => {
  await page.setViewportSize({ width: 1600, height: 1100 });
  const create = async (type: string, name: string, x: number) => {
    const response = await request.post("/api/nodes", { data: { type, name, position: { x, y: 430 }, ...(type === "agent" ? { config: { runtime_provider_id: "core.mock" } } : {}) } });
    expect(response.status()).toBe(201); return response.json();
  };
  const node = await create("oaw.tasks", "Executable plan", 710);
  const first = await create("agent", "Primary worker", 1100);
  const second = await create("agent", "Second worker", 1450);
  const url = `/api/nodes/${node.id}`;
  try {
    for (const agent of [first, second]) {
      const edge = await request.post("/api/edges", { data: { source: node.id, target: agent.id, relationship: "oaw.tasks.executor" } });
      expect(edge.status()).toBe(201);
    }
    await request.post(url + "/actions/upsert", { data: { expected_revision: 0, arguments: { tasks: [
      { id: "a", title: "First analysis" }, { id: "b", title: "Second analysis" }, { id: "c", title: "Combined report", depends_on: ["a", "b"] },
    ] } } });
    await page.goto("/");
    const card = page.locator(`[data-card-id="${node.id}"]`);
    await card.locator(".card-kind-icon").click();
    await card.getByRole("button", { name: "Open workspace", exact: true }).click();
    const board = page.getByRole("dialog", { name: "Executable plan workspace", exact: true });
    await board.locator(".task-execution-settings summary").click();
    await board.getByLabel("Default executor", { exact: true }).selectOption(first.id);
    await expect(board.getByLabel("Default executor", { exact: true })).toHaveValue(first.id);
    await board.getByLabel("Execution mode").selectOption("2");
    await expect(board.getByLabel("Execution mode")).toHaveValue("2");
    await page.screenshot({ path: "../.open-agent-world/task-execution-settings.png" });
    await board.locator(".task-execution-settings summary").click();
    await board.getByRole("button", { name: "Edit task Second analysis", exact: true }).click();
    await board.getByLabel("Task executor", { exact: true }).selectOption(second.id);
    await board.getByRole("button", { name: "Save task", exact: true }).click();
    await board.getByRole("button", { name: "Run ready work", exact: true }).click();
    await expect(board.getByRole("heading", { name: "3 / 3 complete", exact: true })).toBeVisible();
    await board.locator(".execution-history summary").click();
    await expect(board.locator(".execution-history li")).toHaveCount(3);
    await board.locator(".execution-history summary").click();
    await board.getByRole("button", { name: "Edit task Combined report", exact: true }).click();
    await expect(board.getByLabel("Progress note", { exact: true })).toContainText("First analysis");
    await page.screenshot({ path: "../.open-agent-world/task-execution-light.png" });
    await page.getByRole("button", { name: "Use dark theme" }).click();
    await page.screenshot({ path: "../.open-agent-world/task-execution-dark.png" });
    await page.getByRole("button", { name: "Use light theme" }).click();
    await page.reload();
    await expect(page.getByRole("dialog", { name: "Executable plan workspace", exact: true }).getByRole("heading", { name: "3 / 3 complete", exact: true })).toBeVisible();
    const state = await (await request.get(url + "/execution")).json();
    expect(state.attempts.map((attempt: { item_id: string }) => attempt.item_id)).toEqual(["a", "b", "c"]);
  } finally {
    await request.post(url + "/execution/stop");
    for (const id of [node.id, first.id, second.id]) await request.delete(`/api/nodes/${id}`);
  }
});
