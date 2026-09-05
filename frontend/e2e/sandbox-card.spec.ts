import { expect, test } from "@playwright/test";
import { TEST_CATALOG } from "../src/state/catalog.fixture";

test("sandbox card confirms folder settings and follows runtime metadata (mock API)", async ({ page }, testInfo) => {
  let card = {
    id: "sandbox-ui-check", type: "sandbox", name: "Project workspace",
    position: { x: 550, y: 340 }, size: { width: 96, height: 96 }, expanded: false, status: "stopped",
    config: { runtime: "auto", workspace_path: null as string | null, workspace_access: "read_write", output: [] as string[] },
  };
  let state = "stopped";
  let locked = false;
  let rejectSave = true;
  let releaseSave: (() => void) | undefined;
  let savedRequest: Record<string, unknown> | undefined;
  const info = () => ({
    sandbox_id: card.id, state, runtime_id: "wsl:Ubuntu", runtime_locked: locked,
    platform: "linux", shell: ["/bin/sh", "-c"], available: true, unavailable_reason: null,
    workspace_path: card.config.workspace_path, workspace_access: card.config.workspace_access,
    workspace: "/workspace", resources_path: "/resources", security_boundary: "Linux namespaces in WSL2",
  });
  await page.routeWebSocket("**/ws/events", () => {});
  await page.route(/^https?:\/\/[^/]+\/api\//, async (route) => {
    const path = new URL(route.request().url()).pathname;
    const reply = (json: unknown, status = 200) => route.fulfill({ status, json });
    if (path === "/api/catalog") return reply(TEST_CATALOG);
    if (path === "/api/world") return reply({ nodes: [{ ...card, status: state }], edges: [], chunks: ["0:0"] });
    if (path === "/api/legions") return reply([]);
    if (path === "/api/sandbox/runtimes") return reply({
      default_runtime: "wsl:Ubuntu",
      runtimes: [{ id: "wsl:Ubuntu", label: "WSL · Ubuntu", platform: "linux", available: true,
        reason: null, shell: ["/bin/sh", "-c"], supports_workspace: true }],
    });
    if (path === `/api/sandboxes/${card.id}`) return reply(info());
    if (path === `/api/nodes/${card.id}` && route.request().method() === "PATCH") {
      const patch = route.request().postDataJSON();
      savedRequest = patch.config;
      if (rejectSave) return reply({ detail: "Working folder does not exist." }, 422);
      await new Promise<void>((resolve) => { releaseSave = resolve; });
      card = { ...card, ...patch, config: { ...card.config, ...patch.config } };
      return reply(card);
    }
    if (path.endsWith("/start")) { state = "ready"; locked = true; return reply(info()); }
    if (path.endsWith("/stop")) { state = "stopped"; return reply(info()); }
    if (path.endsWith("/execute")) {
      expect(route.request().postDataJSON()).toEqual({ command: "printf 'hello'" });
      return reply({ stdout: "hello\n", stderr: "", exit_code: 0 });
    }
    return reply({ detail: `Unexpected mock request: ${path}` }, 404);
  });

  await page.goto("/");
  const panel = page.locator(`[data-card-id="${card.id}"]`);
  await panel.locator(".card-kind-icon").click();
  await expect(panel).toHaveAttribute("data-surface-level", "inspector");
  await expect(panel.getByRole("status")).toHaveText("Stopped");
  await expect(panel.getByText("Linux namespaces in WSL2", { exact: true })).toBeVisible();
  await expect(panel.getByText("/bin/sh -c", { exact: true })).toBeVisible();
  await panel.getByLabel("Working folder").fill("D:\\projects\\demo");
  await panel.getByLabel("Folder access").selectOption("read_only");
  await expect(panel.getByRole("button", { name: "Start", exact: true })).toBeDisabled();
  await panel.getByRole("button", { name: "Save", exact: true }).click();
  await expect(panel.getByRole("alert")).toHaveText("Working folder does not exist.");
  await expect(panel.getByLabel("Working folder")).toHaveValue("D:\\projects\\demo");
  await expect(panel.getByText("Unsaved changes", { exact: true })).toBeVisible();
  expect(card.config.workspace_path).toBeNull();

  rejectSave = false;
  await panel.getByRole("button", { name: "Save", exact: true }).click();
  await expect(panel.getByText("Saving…", { exact: true })).toBeVisible();
  await expect(panel.getByLabel("Working folder")).toBeDisabled();
  await expect(panel.getByRole("button", { name: "Start", exact: true })).toBeDisabled();
  expect(savedRequest).toEqual({ runtime: "auto", workspace_path: "D:\\projects\\demo", workspace_access: "read_only" });
  releaseSave!();
  await expect(panel.getByText("Settings saved", { exact: true })).toBeVisible();
  await panel.getByRole("button", { name: "Start", exact: true }).click();
  await expect(panel.getByRole("status")).toHaveText("Ready");
  await panel.locator(".sandbox-settings > summary").click();
  await expect(panel.getByRole("combobox", { name: "Runtime", exact: true })).toBeDisabled();
  await expect(panel.getByLabel("Working folder")).toBeDisabled();
  await panel.locator(".sandbox-settings > summary").click();
  await panel.getByLabel("Terminal", { exact: false }).fill("printf 'hello'");
  await panel.getByRole("button", { name: "Execute command" }).click();
  await expect(panel.getByRole("status")).toHaveText("Ready");
  await expect(panel.getByRole("log")).toContainText("hello");
  await panel.screenshot({ path: testInfo.outputPath("sandbox-ready.png") });
  await page.getByRole("button", { name: "Use dark theme" }).click();
  await panel.screenshot({ path: testInfo.outputPath("sandbox-ready-dark.png") });
  await panel.getByRole("button", { name: "Stop", exact: true }).click();
  await expect(panel.getByRole("status")).toHaveText("Stopped");
  await expect(panel.getByLabel("Working folder")).toBeEnabled();
  await expect(panel.getByRole("combobox", { name: "Runtime", exact: true })).toBeDisabled();
});
