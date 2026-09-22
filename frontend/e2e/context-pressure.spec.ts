import { expect, test } from "@playwright/test";

test("session pressure rings stay around persistent avatars and settle after compaction", async ({ page, request }) => {
  await page.setViewportSize({ width: 1800, height: 1100 });
  const room = await (await request.post("/api/nodes", { data: { type: "conversation", name: "Context pressure QA", position: { x: 800, y: 450 } } })).json();
  const agents = [];
  try {
    for (const name of ["Atlas", "Boreal", "Plugin"]) {
      const agent = await (await request.post("/api/nodes", { data: { type: "agent", name, position: { x: 50, y: 80 } } })).json();
      agents.push(agent);
      expect((await request.post("/api/edges", { data: { source: agent.id, target: room.id, relationship: "participate" } })).ok()).toBeTruthy();
    }
    const base = `/api/conversations/${room.id}`;
    const first = await (await request.post(`${base}/sessions`, { data: { title: "Research", group_title: "Context QA", participant_ids: agents.map(agent => agent.id) } })).json();
    const second = await (await request.post(`${base}/sessions`, { data: { title: "Another session", group_id: first.group_id, participant_ids: agents.map(agent => agent.id) } })).json();
    let pressure = .92;
    let state = "high";
    let count = 1;
    // Exercise the actual Conversation UI against the REST projection contract.
    // Real compaction/provider behavior is covered by backend ADK tests.
    await page.route(`**${base}`, async route => {
      const response = await route.fetch();
      if (!response.ok()) { await route.fulfill({ response }); return; }
      const body = await response.json();
      body.sessions.sort((a: { id: string }) => a.id === first.id ? -1 : 1);
      body.context_statuses = {
        [first.id]: {
          [agents[0].id]: { pressure, state, compaction_count: count },
          [agents[1].id]: { pressure: .3, state: "normal", compaction_count: 0 },
        },
        [second.id]: { [agents[0].id]: { pressure: .1, state: "normal", compaction_count: 0 } },
      };
      await route.fulfill({ response, json: body });
    });
    await page.goto("/");
    const workspace = page.locator(`[data-workspace-node-id="${room.id}"]`);
    const ring = workspace.getByTitle("Context 92% · compacted 1 times");
    await expect(ring).toBeVisible();
    await expect(workspace.locator(".context-pressure-ring")).toHaveCount(2);
    await expect(workspace.locator(".workspace-transcript .context-pressure-ring")).toHaveCount(0);
    const geometry = await ring.evaluate(node => {
      const avatar = node.getBoundingClientRect();
      const svg = node.querySelector(".context-pressure-ring")!.getBoundingClientRect();
      return { centeredX: (avatar.x + avatar.width / 2) - (svg.x + svg.width / 2),
        centeredY: (avatar.y + avatar.height / 2) - (svg.y + svg.height / 2),
        diameter: svg.width, height: svg.height };
    });
    expect(Math.abs(geometry.centeredX)).toBeLessThan(1);
    expect(Math.abs(geometry.centeredY)).toBeLessThan(1);
    expect(geometry.diameter).toBe(geometry.height);
    expect(geometry.diameter).toBeLessThan(45);
    await page.screenshot({ path: "../.outputs/context-pressure-high.png" });
    await workspace.getByTitle("Another session", { exact: true }).click();
    await expect(workspace.getByTitle("Context 10% · compacted 0 times")).toBeVisible();
    await workspace.getByTitle("Research", { exact: true }).click();
    state = "compacting";
    // Canonical message event invalidates the same authoritative REST snapshot.
    await request.post(`${base}/sessions/${first.id}/messages`, { data: { content: "Context lifecycle visual check" } });
    await expect(workspace.locator('[data-context-state="compacting"]')).toBeVisible();
    state = "normal"; pressure = .2; count = 2;
    await request.post(`${base}/sessions/${first.id}/messages`, { data: { content: "Continuation after compaction" } });
    const reduced = workspace.getByTitle("Context 20% · compacted 2 times");
    await expect(reduced).toBeVisible();
    await expect(workspace.getByTitle("Context 30% · compacted 0 times")).toBeVisible();
    await expect(reduced.locator(".context-pressure-fill")).toHaveCSS("stroke-dashoffset", "80px");
    await page.screenshot({ path: "../.outputs/context-pressure-reduced.png" });
    await page.emulateMedia({ reducedMotion: "reduce" });
    expect(await reduced.locator(".context-pressure-fill").evaluate(node =>
      parseFloat(getComputedStyle(node).transitionDuration))).toBeLessThan(.001);
  } finally {
    await request.delete(`/api/nodes/${room.id}`);
    for (const agent of agents) await request.delete(`/api/nodes/${agent.id}`);
  }
});
