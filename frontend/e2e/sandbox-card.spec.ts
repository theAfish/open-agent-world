import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { TEST_CATALOG } from "../src/state/catalog.fixture";

test("sandbox window keeps files, preview and terminal together with separate settings (mock API)", async ({ page }, testInfo) => {
  test.setTimeout(60_000);
  let card = {
    id: "sandbox-ui-check", type: "sandbox", name: "Project workspace",
    position: { x: 550, y: 340 }, size: { width: 96, height: 96 }, expanded: false, status: "stopped",
    config: { runtime: "auto", workspace_path: null as string | null, workspace_access: "read_write", output: [] as string[] },
  };
  let state = "stopped";
  let executions = 0;
  let previewState = "text";
  const receipts: Record<string, unknown>[] = [];
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
    const url = new URL(route.request().url());
    const path = url.pathname;
    const reply = (json: unknown, status = 200) => route.fulfill({ status, json });
    if (path === "/api/catalog") return reply(TEST_CATALOG);
    if (path === "/api/world") return reply({ nodes: [{ ...card, status: state }], edges: [], chunks: ["0:0"] });
    if (path === "/api/legions") return reply([]);
    if (path === "/api/sandbox/runtimes") return reply({
      default_runtime: "wsl:Ubuntu",
      runtimes: [{ id: "wsl:Ubuntu", label: "WSL · Ubuntu", platform: "linux", available: true,
        reason: null, shell: ["/bin/sh", "-c"], supports_workspace: true }],
    });
    if (path.endsWith("/document")) return reply({ value: { variables: {} }, revision: 0, summary: {} });
    if (path.endsWith("/credentials")) return reply({});
    if (path.endsWith("/configuration")) return reply({ profile_id: null, ready: true, variables: [] });
    if (path.endsWith("/history")) return reply(receipts);
    if (path.endsWith("/files")) {
      const operation = url.searchParams.get("operation");
      if (operation === "list") return reply({ entries: [{ name: "result.txt", directory: false, blocked: false, size: 5 }], truncated: false });
      if (operation === "preview") return reply({ state: previewState, text: "hello" });
      if (operation === "download") return route.fulfill({ contentType: "application/octet-stream", headers: { "Content-Disposition": "attachment; filename=result.txt" }, body: "hello" });
      return reply([{ id: "workspace", label: "Workspace", access: "read_only", directory: true }]);
    }
    if (path.endsWith("/diagnostics")) return reply({ status: "checked", stdout: "python3: available\nWorkspace: readable", stderr: "", network_reason: "Disabled" });
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
      executions++;
      expect(route.request().postDataJSON()).toEqual({ command: "printf 'hello'" });
      receipts.push({ id: `manual-${executions}`, caller: "user", state: "finished", argv: ["/bin/sh", "-c", "printf 'hello'"], stdout: "hello", stderr: "", exit_code: 0, duration_seconds: 0.01 });
      return reply({ stdout: "hello\n", stderr: "", exit_code: 0 });
    }
    return reply({ detail: `Unexpected mock request: ${path}` }, 404);
  });

  await page.goto("/");
  const panel = page.locator(`[data-card-id="${card.id}"]`);
  await panel.locator(".card-kind-icon").click();
  await expect(panel).toHaveAttribute("data-surface-level", "inspector");
  await expect(panel.getByRole("status")).toHaveText("Stopped");
  await expect(panel.getByLabel("Working folder", { exact: true })).toHaveCount(0);
  await expect(panel.getByLabel("Command", { exact: true })).toHaveCount(0);
  await panel.getByRole("button", { name: "Open Window", exact: true }).click();

  const window = page.getByRole("dialog", { name: "Project workspace workspace" });
  const sidebar = window.getByLabel("Sandbox files");
  const preview = window.locator(".sandbox-preview");
  const terminal = window.locator(".sandbox-terminal");
  await expect(window.getByRole("tab", { name: "Workspace", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(sidebar).toBeVisible();
  await expect(preview).toBeVisible();
  await expect(terminal).toBeVisible();
  await expect(preview).toContainText("Select a file");
  await expect(sidebar.getByRole("button", { name: "result.txt", exact: true })).toBeVisible();
  await expect.poll(() => window.evaluate(element => {
    // Read all panes in one frame while the canvas animates the window into view.
    const files = element.querySelector(".sandbox-files")!.getBoundingClientRect();
    const preview = element.querySelector(".sandbox-preview")!.getBoundingClientRect();
    const terminal = element.querySelector(".sandbox-terminal")!.getBoundingClientRect();
    return files.right <= preview.left + 1 && Math.abs(preview.left - terminal.left) < 1
      && preview.bottom <= terminal.top + 1 && files.top <= preview.top + 1
      && files.bottom >= terminal.bottom - 1;
  })).toBe(true);
  await sidebar.getByRole("button", { name: "result.txt", exact: true }).click();
  await expect(preview.locator("pre")).toHaveText("hello");
  await window.getByLabel("Command", { exact: true }).fill("printf 'hello'");
  expect(executions).toBe(0);

  await window.getByRole("tab", { name: /^Settings/ }).click();
  await expect(sidebar).toBeHidden();
  await expect(window.getByLabel("Working folder", { exact: true })).toBeVisible();
  await expect(window.getByRole("button", { name: "Check execution environment" })).toBeHidden();
  await window.getByLabel("Working folder", { exact: true }).fill("D:\\projects\\demo");
  await window.getByLabel("Folder access").selectOption("read_only");
  await expect(window.getByRole("button", { name: "Start", exact: true })).toBeDisabled();
  await window.getByRole("button", { name: "Save", exact: true }).click();
  await expect(window.getByRole("alert")).toHaveText("Working folder does not exist.");
  await expect(window.getByLabel("Working folder", { exact: true })).toHaveValue("D:\\projects\\demo");
  await expect(window.getByText("Unsaved changes", { exact: true })).toBeVisible();
  expect(card.config.workspace_path).toBeNull();

  await window.getByRole("tab", { name: "Workspace", exact: true }).click();
  await expect(preview.locator("pre")).toHaveText("hello");
  await expect(terminal).toBeVisible();
  await expect(window.getByLabel("Command", { exact: true })).toHaveValue("printf 'hello'");
  await expect(window.getByRole("button", { name: "Start", exact: true })).toBeDisabled();
  await window.getByRole("tab", { name: /^Settings/ }).click();
  await expect(window.getByLabel("Working folder", { exact: true })).toHaveValue("D:\\projects\\demo");
  rejectSave = false;
  await window.getByRole("button", { name: "Save", exact: true }).click();
  await expect(window.getByLabel("Working folder", { exact: true })).toBeDisabled();
  await expect(window.getByRole("button", { name: "Start", exact: true })).toBeDisabled();
  await expect.poll(() => savedRequest).toEqual({ runtime: "auto", workspace_path: "D:\\projects\\demo", workspace_access: "read_only" });
  await expect.poll(() => typeof releaseSave).toBe("function");
  releaseSave!();
  await expect(window.getByText("Settings saved", { exact: true })).toBeVisible();
  await window.getByRole("button", { name: "Start", exact: true }).click();
  await expect(window.getByRole("status").filter({ hasText: "Ready" })).toBeVisible();
  await expect(window.getByRole("combobox", { name: "Runtime", exact: true })).toBeDisabled();
  await expect(window.getByLabel("Working folder", { exact: true })).toBeDisabled();
  await window.locator("summary").filter({ hasText: /^Diagnostics$/ }).click();
  await window.getByRole("button", { name: "Check execution environment" }).click();
  await expect(window).toContainText("python3: available");
  await expect(window.getByRole("tab", { name: "Settings", exact: true })).toHaveAttribute("aria-selected", "true");
  await window.screenshot({ path: testInfo.outputPath("sandbox-settings.png") });
  await window.getByRole("tab", { name: "Workspace", exact: true }).click();
  await expect(preview).toContainText("Select a file");
  await sidebar.getByRole("button", { name: "result.txt", exact: true }).click();
  await expect(preview.locator("pre")).toHaveText("hello");

  await window.getByLabel("Command", { exact: true }).press("Control+Enter");
  await expect(window.getByRole("log")).toContainText("hello");
  await expect(preview.locator("pre")).toHaveText("hello");
  const downloadEvent = page.waitForEvent("download");
  await preview.getByRole("button", { name: "Download file", exact: true }).click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe("result.txt");
  expect(await readFile((await download.path())!, "utf8")).toBe("hello");
  for (const [failure, message] of [["oversized", "Preview exceeds 1 MiB"], ["missing", "File no longer exists"], ["permission_denied", "Permission denied"], ["unsupported", "Preview unsupported"]]) {
    previewState = failure;
    await sidebar.getByRole("button", { name: "result.txt", exact: true }).click();
    await expect(preview).toContainText(message);
    await expect(sidebar).toBeVisible();
    await expect(terminal).toBeVisible();
  }
  previewState = "text";
  await sidebar.getByRole("button", { name: "result.txt", exact: true }).click();
  await expect(preview.locator("pre")).toHaveText("hello");
  const activityClose = page.getByRole("button", { name: "Close runtime activity" });
  if (await activityClose.isVisible()) await activityClose.click();

  const fileDivider = window.getByRole("separator", { name: "Resize file sidebar" });
  const beforeResize = (await sidebar.boundingBox())!;
  const divider = (await fileDivider.boundingBox())!;
  await page.mouse.move(divider.x + divider.width / 2, divider.y + 80);
  await page.mouse.down();
  await page.mouse.move(divider.x + 50, divider.y + 80, { steps: 8 });
  await page.mouse.up();
  await expect.poll(async () => (await sidebar.boundingBox())!.width).toBeGreaterThan(beforeResize.width + 20);
  const widthAfterDrag = Number(await fileDivider.getAttribute("aria-valuenow"));
  await fileDivider.focus();
  await fileDivider.press("ArrowLeft");
  await expect.poll(async () => Number(await fileDivider.getAttribute("aria-valuenow"))).toBeLessThan(widthAfterDrag);

  const terminalDivider = window.getByRole("separator", { name: "Resize terminal" });
  await expect(terminalDivider).toHaveAttribute("aria-orientation", "horizontal");
  const beforeTerminalResize = (await terminal.boundingBox())!;
  const horizontalDivider = (await terminalDivider.boundingBox())!;
  await page.mouse.move(horizontalDivider.x + 80, horizontalDivider.y + horizontalDivider.height / 2);
  await page.mouse.down();
  await page.mouse.move(horizontalDivider.x + 80, horizontalDivider.y - 45, { steps: 8 });
  await page.mouse.up();
  await expect.poll(async () => (await terminal.boundingBox())!.height).toBeGreaterThan(beforeTerminalResize.height + 15);
  const terminalValue = await terminalDivider.getAttribute("aria-valuenow");
  await terminalDivider.focus();
  await terminalDivider.press("ArrowDown");
  await expect(terminalDivider).not.toHaveAttribute("aria-valuenow", terminalValue!);
  await expect(preview).toBeVisible();
  await expect(window.getByLabel("Command", { exact: true })).toBeVisible();
  await window.screenshot({ path: testInfo.outputPath("sandbox-window.png") });
  await page.getByRole("button", { name: "Use dark theme" }).click();
  await window.screenshot({ path: testInfo.outputPath("sandbox-window-dark.png") });

  await window.getByRole("button", { name: "Close workspace" }).click();
  await panel.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(window.getByRole("tab", { name: "Settings", exact: true })).toHaveAttribute("aria-selected", "true");
  await window.getByRole("tab", { name: "Workspace", exact: true }).click();
  await expect(window.getByLabel("Command", { exact: true })).toHaveValue("printf 'hello'");
  await window.getByRole("tab", { name: "History", exact: true }).click();
  await expect(window.locator(".sandbox-history")).toContainText("user · finished");
  await expect(sidebar).toBeVisible();
  await expect(preview).toBeVisible();
  expect(executions).toBe(1);
  await page.reload();
  await expect(page.getByRole("dialog", { name: "Project workspace workspace" })).toBeVisible();
  expect(executions).toBe(1);
});
