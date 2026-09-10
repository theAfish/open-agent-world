import { expect, test } from "@playwright/test";

test("durable grouped sessions page both ways with stable scroll anchors", async ({ page, request }) => {
  test.setTimeout(90000);
  await page.setViewportSize({ width: 1800, height: 1100 });
  const response = await request.post("/api/nodes", { data: { type: "conversation", name: "History QA", position: { x: 800, y: 450 } } });
  expect(response.ok()).toBeTruthy();
  const room = await response.json();
  const base = `/api/conversations/${room.id}`;
  try {
    const created = await request.post(`${base}/sessions`, { data: { group_title: "Research history" } });
    const session = await created.json();
    for (let n = 1; n <= 220; n++) {
      const result = await request.post(`${base}/sessions/${session.id}/messages`, { data: { content: `Historical message ${n}` } });
      expect(result.ok()).toBeTruthy();
    }
    await page.goto("/");
    const card = page.locator(`[data-card-id="${room.id}"]`);
    await card.click();
    await card.getByRole("button", { name: "Open workspace" }).click();
    const workspace = page.locator(`[data-workspace-node-id="${room.id}"]`);
    const transcript = workspace.locator(".workspace-transcript");
    await expect(transcript.getByText("Historical message 220", { exact: true })).toBeVisible();
    await expect(transcript.locator("[data-message-id]")).toHaveCount(50);
    const latest = workspace.getByRole("button", { name: "Jump to latest" });
    await expect(latest).toHaveCount(0);
    await page.evaluate(() => Promise.all(document.getAnimations().filter((animation) => animation.effect?.getTiming().iterations !== Infinity).map((animation) => animation.finished.catch(() => {}))));
    const composerBefore = await workspace.locator(".workspace-composer").evaluate((node) => (node as HTMLElement).offsetTop);
    await transcript.evaluate((node) => { node.scrollTop = node.scrollHeight - node.clientHeight - 100; node.dispatchEvent(new Event("scroll")); });
    await expect(latest).toHaveCount(0);
    await transcript.evaluate((node) => { node.scrollTop = node.scrollHeight - node.clientHeight - 240; node.dispatchEvent(new Event("scroll")); });
    await expect(latest).toBeVisible();
    const circle = await latest.boundingBox();
    const composerAfter = await workspace.locator(".workspace-composer").boundingBox();
    expect(circle!.width).toBe(circle!.height);
    expect(circle!.y + circle!.height).toBeLessThan(composerAfter!.y);
    expect(await workspace.locator(".workspace-composer").evaluate((node) => (node as HTMLElement).offsetTop)).toBe(composerBefore);
    await latest.click();
    await expect(latest).toHaveCount(0);

    await transcript.evaluate((node) => { node.scrollTop = 0; node.dispatchEvent(new Event("scroll")); });
    await expect(transcript.locator("[data-message-id]")).toHaveCount(100);
    const anchored = await transcript.evaluate((node) => ({ top: node.scrollTop, height: node.scrollHeight }));
    expect(anchored.top).toBeGreaterThan(100);
    for (let n = 0; n < 3; n++) {
      await transcript.evaluate((node) => { node.scrollTop = 0; node.dispatchEvent(new Event("scroll")); });
      await expect(workspace.getByRole("status")).toHaveCount(0);
      await page.waitForTimeout(150);
    }
    expect(await transcript.locator("[data-message-id]").count()).toBeLessThanOrEqual(150);
    await expect(transcript.getByText("Historical message 1", { exact: true })).toBeVisible();
    await workspace.getByRole("button", { name: "Jump to latest" }).click();
    await expect(transcript.getByText("Historical message 220", { exact: true })).toBeVisible();
    await workspace.getByRole("button", { name: "New session", exact: true }).click();
    await expect(transcript.getByText("Historical message 220", { exact: true })).toHaveCount(0);
    await workspace.getByRole("button", { name: "Rename session" }).click();
    await workspace.getByLabel("Session title").fill("Second topic");
    await workspace.getByRole("button", { name: "Save name" }).click();
    await expect(workspace.locator(".conversation-session-list").getByRole("button", { name: /Second topic/ })).toBeVisible();
    const summary = await (await request.get(base)).json();
    expect(summary.sessions.filter((item: { group_id: string }) => item.group_id === session.group_id)).toHaveLength(2);
    await workspace.locator(".conversation-session-list").getByRole("button", { name: /Historical message 1/ }).click();
    await expect(transcript.getByText("Historical message 220", { exact: true })).toBeVisible();
    const sessionsPanel = workspace.locator(".conversation-participant-panel .conversation-session-list");
    await expect(sessionsPanel).toBeVisible();
    await expect(workspace.locator("nav .conversation-session-list")).toHaveCount(0);
    const newButton = await sessionsPanel.getByRole("button", { name: "New session", exact: true }).boundingBox();
    const firstSession = await sessionsPanel.locator(".conversation-sidebar-scroll .workspace-session").first().boundingBox();
    expect(firstSession!.y - (newButton!.y + newButton!.height)).toBeGreaterThanOrEqual(8);
    await transcript.evaluate((node) => { node.scrollTop = node.scrollHeight - node.clientHeight - 240; node.dispatchEvent(new Event("scroll")); });
    await expect(latest).toBeVisible();
    await page.screenshot({ path: "../.outputs/conversation-layout.png" });
    await page.setViewportSize({ width: 820, height: 900 });
    await expect(sessionsPanel).toBeVisible();

  } finally { await request.delete(`/api/nodes/${room.id}`); }
});


test("sending shows the bubble while the request and automatic title are still pending", async ({ page, request }) => {
  await page.setViewportSize({ width: 1800, height: 1100 });
  const room = await (await request.post("/api/nodes", { data: { type: "conversation", name: "Immediate send QA", position: { x: 800, y: 450 } } })).json();
  const base = `/api/conversations/${room.id}`;
  const session = await (await request.post(`${base}/sessions`, { data: { group_title: "Send timing" } })).json();
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  try {
    await page.route(`**/api/conversations/${room.id}/sessions/${session.id}/messages`, async (route) => {
      await held;
      await route.continue();
    });
    await page.goto("/");
    const card = page.locator(`[data-card-id="${room.id}"]`);
    await card.click();
    await card.getByRole("button", { name: "Open workspace" }).click();
    const workspace = page.locator(`[data-workspace-node-id="${room.id}"]`);
    const composer = workspace.getByLabel("Conversation message");
    await composer.fill("Show immediately");
    await workspace.getByRole("button", { name: "Send message", exact: true }).click();
    await expect(workspace.locator(".workspace-transcript").getByText("Show immediately", { exact: true })).toBeVisible();
    await expect(workspace.getByText("Sending...", { exact: true })).toBeVisible();
    await expect(workspace.locator(".workspace-conversation > header").getByText("New session", { exact: true })).toBeVisible();
    await expect(composer).toHaveValue("");
    await composer.fill("Next draft stays here");
    release();
    await expect(workspace.getByText("Sending...", { exact: true })).toHaveCount(0);
    await expect(workspace.locator(".workspace-transcript").getByText("Show immediately", { exact: true })).toHaveCount(1);
    await expect(composer).toHaveValue("Next draft stays here");
  } finally { release(); await request.delete(`/api/nodes/${room.id}`); }
});
