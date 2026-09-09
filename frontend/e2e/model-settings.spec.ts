import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";

test("manage multiple accounts, persist models, and keep settings navigation on the left", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Open settings", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Settings", exact: true });
  await expect(dialog.getByLabel("Connection name")).toBeVisible();
  await expect(dialog.getByText("Authentication source")).toHaveCount(0);
  // The initial imported connection exposes its key directly.
  await expect(dialog.getByLabel("API key", { exact: true })).toBeVisible();
  await dialog.getByLabel("API key", { exact: true }).fill("e2e-legacy-secret");
  await dialog.getByLabel("New connection type").selectOption("openai");
  await dialog.getByRole("button", { name: "Add connection", exact: true }).click();
  await dialog.getByLabel("Connection name").fill("Work account");
  await dialog.getByLabel("API key", { exact: true }).fill("e2e-work-secret");
  await dialog.getByRole("button", { name: "Add model", exact: true }).click();
  await dialog.getByLabel("Model 1 display name").fill("Work assistant");
  await dialog.getByLabel("Model 1 ID", { exact: true }).fill("gpt-4o-mini");
  await dialog.getByRole("combobox", { name: /^Default for new agents/ }).selectOption({ label: "Work account / Work assistant" });

  await dialog.getByLabel("New connection type").selectOption("anthropic");
  await dialog.getByRole("button", { name: "Add connection", exact: true }).click();
  await dialog.getByLabel("Connection name").fill("Personal account");
  await dialog.getByLabel("API key", { exact: true }).fill("e2e-personal-secret");
  await dialog.getByRole("button", { name: "Add model", exact: true }).click();
  await dialog.getByLabel("Model 1 display name").fill("Writing assistant");
  await dialog.getByLabel("Model 1 ID", { exact: true }).fill("claude-test");
  await dialog.getByRole("button", { name: "Sandbox", exact: true }).click();
  await expect(dialog.getByLabel("Default Workspace location", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "Models", exact: true }).click();
  await dialog.getByRole("button", { name: /Personal account 1 models/ }).click();
  await expect(dialog.getByLabel("API key", { exact: true })).toHaveValue("e2e-personal-secret");
  const nav = await dialog.locator(".settings-sections").boundingBox();
  const content = await dialog.locator(".settings-content").boundingBox();
  expect(nav!.x + nav!.width).toBeLessThanOrEqual(content!.x + 1);
  await dialog.getByLabel("New connection type").selectOption("local");
  await dialog.getByRole("button", { name: "Add connection", exact: true }).click();
  await expect(dialog.getByLabel("API key", { exact: true })).toBeVisible();
  await expect(dialog.getByText(/Optional for this connection/)).toBeVisible();
  await dialog.getByLabel("API key", { exact: true }).fill("e2e-local-secret");
  await dialog.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(dialog).toHaveCount(0);

  await page.reload();
  await page.getByRole("button", { name: "Open settings", exact: true }).click();
  await dialog.getByRole("button", { name: /Work account 1 models/ }).click();
  await expect(dialog.getByLabel("API key", { exact: true })).toHaveValue("");
  await expect(dialog.getByLabel("API key", { exact: true })).toHaveAttribute("placeholder", /Saved securely/);
  await expect(dialog.getByRole("combobox", { name: /^Default for new agents/ })).toContainText("Work account / Work assistant");
  await mkdir("../.outputs/model-settings", { recursive: true });
  await page.screenshot({ path: "../.outputs/model-settings/desktop.png", animations: "disabled" });
  await page.setViewportSize({ width: 540, height: 800 });
  await expect(dialog.getByLabel("Connection name")).toBeVisible();
  const fits = await dialog.evaluate(element => element.scrollWidth <= element.clientWidth);
  expect(fits).toBe(true);
  await page.screenshot({ path: "../.outputs/model-settings/narrow.png", animations: "disabled" });
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
});
