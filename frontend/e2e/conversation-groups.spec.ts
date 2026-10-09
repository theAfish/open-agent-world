import { expect, test, type Locator } from '@playwright/test';

async function openGroups(workspace: Locator) {
  const trigger = workspace.getByRole('button', { name: /^Switch group:/ });
  if (await trigger.getAttribute('aria-expanded') !== 'true') await trigger.click();
  return workspace.getByRole('dialog', { name: 'Groups', exact: true });
}

test('group menus rename durably and delete all sessions with confirmation', async ({ page, request }) => {
  await page.setViewportSize({ width: 1800, height: 1100 });
  const room = await (await request.post('/api/nodes', { data: { type: 'conversation', name: 'Group actions QA', position: { x: 800, y: 450 } } })).json();
  const base = `/api/conversations/${room.id}`;
  try {
    const first = await (await request.post(`${base}/sessions`, { data: { group_title: 'Research', title: 'First topic' } })).json();
    await request.post(`${base}/sessions`, { data: { group_id: first.group_id, title: 'Second topic' } });
    await page.goto('/');
    const workspace = page.locator(`[data-workspace-node-id="${room.id}"]`);
    await openGroups(workspace);
    await workspace.getByLabel('Group actions for Research').click();
    await workspace.getByRole('button', { name: 'Rename group', exact: true }).click();
    await workspace.getByRole('textbox', { name: 'Group name' }).fill('Renamed research');
    await workspace.getByRole('button', { name: 'Save name', exact: true }).click();
    await expect(workspace.getByRole('button', { name: 'Renamed research', exact: true })).toBeVisible();
    await page.reload();
    await openGroups(workspace);
    await expect(workspace.getByRole('button', { name: 'Renamed research', exact: true })).toBeVisible();
    await workspace.getByRole('button', { name: 'Renamed research', exact: true }).click();
    await expect(workspace.locator('.conversation-session-list .conversation-session-row')).toHaveCount(2);
    await workspace.locator('.conversation-session-list').getByRole('button', { name: /^First topic/ }).click();
    await openGroups(workspace);
    await workspace.getByRole('button', { name: 'General', exact: true }).click();
    await expect.poll(async () => {
      const preferences = await (await request.get('/api/application')).json();
      return JSON.parse(preferences.values['oaw-conversation-view-v1']).state.groupSessions[room.id]?.[first.group_id];
    }).toBe(first.id);
    await page.reload();
    await openGroups(workspace);
    await workspace.getByRole('button', { name: 'Renamed research', exact: true }).click();
    await expect(workspace.locator('.conversation-session-list .workspace-session.is-active')).toContainText('First topic');
    const positions = () => workspace.locator('.conversation-session-list .conversation-session-row').evaluateAll(rows => rows.map(row => {
      const rect = row.getBoundingClientRect(); return { x: rect.x, y: rect.y, height: rect.height };
    }));
    const beforeMenus = await positions();
    const sessionAction = workspace.getByLabel('Session actions for Second topic');
    await sessionAction.click();
    await expect(workspace.getByRole('button', { name: 'Rename session', exact: true })).toBeVisible();
    expect(await positions()).toEqual(beforeMenus);
    await sessionAction.press('Tab');
    await expect(workspace.getByRole('button', { name: 'Rename session', exact: true })).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(sessionAction).toBeFocused();
    await expect(workspace.getByRole('button', { name: 'Rename session', exact: true })).toHaveCount(0);
    await openGroups(workspace);
    await workspace.getByLabel('Group actions for Renamed research').click();
    expect(await positions()).toEqual(beforeMenus);
    await page.screenshot({ path: '../.outputs/conversation-group-actions.png' });
    await workspace.getByRole('button', { name: 'Delete group', exact: true }).click();
    const confirmation = page.getByRole('dialog', { name: 'Delete Renamed research?' });
    await expect(confirmation.getByRole('button', { name: 'Cancel', exact: true })).toBeFocused();
    await expect(confirmation.getByText('First topic', { exact: true })).toBeVisible();
    await expect(confirmation.getByText('Second topic', { exact: true })).toBeVisible();
    await page.screenshot({ path: '../.outputs/ux-delete-confirmation.png' });
    await confirmation.getByRole('button', { name: 'Cancel', exact: true }).click();
    await openGroups(workspace);
    await expect(workspace.getByRole('button', { name: 'Renamed research', exact: true })).toBeVisible();
    await workspace.getByLabel('Group actions for Renamed research').click();
    await workspace.getByRole('button', { name: 'Delete group', exact: true }).click();
    await confirmation.getByRole('button', { name: 'Delete group', exact: true }).click();
    await expect(confirmation).toHaveCount(0);
    await openGroups(workspace);
    await expect(workspace.getByRole('button', { name: 'Renamed research', exact: true })).toHaveCount(0);
    await expect(workspace.locator('.conversation-session-list .workspace-session.is-active')).toContainText('General');
    const summary = await (await request.get(base)).json();
    expect(summary.sessions).toHaveLength(1);
    await workspace.getByLabel('Group actions for General').click();
    await expect(workspace.getByRole('button', { name: 'Delete group', exact: true })).toBeDisabled();
  } finally { await request.delete(`/api/nodes/${room.id}`); }
});

test('creates groups in the floating switcher and keeps it attached while moving the workspace', async ({ page, request }) => {
  await page.setViewportSize({ width: 1800, height: 1100 });
  const room = await (await request.post('/api/nodes', { data: { type: 'conversation', name: 'Group creation QA', position: { x: 800, y: 450 } } })).json();
  const agent = await (await request.post('/api/nodes', { data: { type: 'agent', name: 'Research Agent', position: { x: 200, y: 300 } } })).json();
  try {
    expect((await request.post('/api/edges', { data: { source: agent.id, target: room.id, relationship: 'participate' } })).ok()).toBeTruthy();
    await page.goto('/');
    const workspace = page.locator(`[data-workspace-node-id="${room.id}"]`);
    await expect(workspace.locator('.conversation-participant-row')).toHaveCount(1);
    await openGroups(workspace);
    await workspace.getByRole('button', { name: 'New group', exact: true }).click();
    const name = workspace.getByRole('textbox', { name: 'Group name' });
    const picker = workspace.getByRole('group', { name: 'Participants', exact: true });
    await expect(name).toBeFocused();
    await expect(name).toHaveValue('New group');
    await expect(picker.getByRole('checkbox', { name: 'Research Agent' })).toBeChecked();
    await expect(workspace.getByText('Create session', { exact: true })).toHaveCount(0);
    const row = await workspace.locator('.conversation-group-tab').boundingBox();
    const popup = await workspace.getByRole('dialog', { name: 'Groups', exact: true }).boundingBox();
    expect(popup!.y).toBeGreaterThanOrEqual(row!.y + row!.height);
    const header = (await workspace.locator('.workspace-titlebar').boundingBox())!;
    await page.mouse.move(header.x + header.width / 2, header.y + 20);
    await page.mouse.down();
    for (const distance of [40, 80, 120]) {
      await page.mouse.move(header.x + header.width / 2 + distance, header.y + 20 + distance / 2, { steps: 4 });
      // Clicking the titlebar dismisses the switcher; reopen it at its new anchor.
      const movedRow = (await workspace.locator('.conversation-group-tab').boundingBox())!;
      expect(movedRow.x - row!.x).toBeGreaterThan(distance / 2);
    }
    await page.mouse.up();
    await openGroups(workspace);
    const movedRow = (await workspace.locator('.conversation-group-tab').boundingBox())!;
    const movedPopup = (await workspace.getByRole('dialog', { name: 'Groups', exact: true }).boundingBox())!;
    expect(Math.abs((movedPopup.x - movedRow.x) - (popup!.x - row!.x))).toBeLessThan(2);
    expect(Math.abs((movedPopup.y - movedRow.y) - (popup!.y - row!.y))).toBeLessThan(2);
    // Canvas panning must move the popup and its owning row together, too.
    const beforePan = (await workspace.locator('.conversation-group-tab').boundingBox())!;
    await page.mouse.move(100, 100);
    await page.mouse.down();
    await page.mouse.move(180, 150, { steps: 8 });
    await page.mouse.up();
    await openGroups(workspace);
    const pannedRow = (await workspace.locator('.conversation-group-tab').boundingBox())!;
    const pannedPicker = (await workspace.getByRole('dialog', { name: 'Groups', exact: true }).boundingBox())!;
    expect(Math.abs(pannedRow.x - beforePan.x)).toBeGreaterThan(40);
    expect(Math.abs((pannedPicker.x - pannedRow.x) - (popup!.x - row!.x))).toBeLessThan(2);
    expect(Math.abs((pannedPicker.y - pannedRow.y) - (popup!.y - row!.y))).toBeLessThan(2);
    await picker.getByRole('checkbox').uncheck();
    await expect(workspace.getByRole('button', { name: 'Create group', exact: true })).toBeDisabled();
    await picker.getByRole('checkbox').check();
    await name.fill('Research group');
    await page.screenshot({ path: '../.outputs/conversation-group-inline.png' });
    await name.press('Enter');
    await expect(name).toHaveCount(0);
    await expect(workspace.getByRole('button', { name: 'Switch group: Research group', exact: true })).toBeVisible();
    const summary = await (await request.get(`/api/conversations/${room.id}`)).json();
    expect(summary.sessions.find((item: { group_title: string }) => item.group_title === 'Research group').participant_ids).toEqual([agent.id]);
    await openGroups(workspace);
    await workspace.getByRole('button', { name: 'New group', exact: true }).click();
    await name.press('Escape');
    await expect(name).toHaveCount(0);
    await expect(workspace.getByRole('button', { name: 'New group', exact: true })).toBeFocused();
  } finally {
    await request.delete(`/api/nodes/${room.id}`);
    await request.delete(`/api/nodes/${agent.id}`);
  }
});
