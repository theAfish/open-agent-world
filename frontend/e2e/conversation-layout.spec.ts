import { expect, test } from '@playwright/test';

test('detached sessions adapt to wide, narrow and short panes without moving the toolbar', async ({ page, request }) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1200, height: 900 });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const room = await (await request.post('/api/nodes', { data: { type: 'conversation', name: 'Conversation layout', position: { x: 400, y: 300 } } })).json();
  const group = (await (await request.post('/api/legion-groups', { data: { name: 'Conversation studio', node_ids: [room.id] } })).json())[0];
  try {
    const base = `/api/conversations/${room.id}`;
    const groupTitle = 'Research and development — a deliberately long group name';
    const first = await (await request.post(`${base}/sessions`, { data: { group_title: groupTitle, title: 'Initial design review' } })).json();
    for (let index = 1; index < 18; index++) {
      await request.post(`${base}/sessions`, { data: { group_id: first.group_id, title: `Topic ${index} — detailed research notes and next steps` } });
    }
    const profile = await (await request.get('/api/application')).json();
    await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en', 'oaw-theme': 'light',
      'oaw-canvas-viewport-v1': null, 'oaw-node-surfaces-v1': null,
      'oaw-conversation-view-v1': JSON.stringify({ version: 0, state: { sessions: { [room.id]: first.id } } }),
    } } });
    const pane = (section_id: string) => ({ kind: 'pane', view: { card_id: room.id, section_id } });
    const layouts = [
      { name: 'wide', root: { kind: 'split', axis: 'vertical', ratio: .3, first: pane('sessions'), second: pane('conversation') } },
      { name: 'narrow', root: { kind: 'split', axis: 'horizontal', ratio: .25, first: pane('sessions'), second: pane('conversation') } },
      { name: 'short', root: { kind: 'split', axis: 'horizontal', ratio: .25,
        first: { kind: 'split', axis: 'vertical', ratio: .3, first: pane('sessions'), second: pane('participants') }, second: pane('conversation') } },
    ];
    for (const layout of layouts) {
      await request.patch(`/api/nodes/${group.id}`, { data: { config: { workspace_layout: { version: 2, hidden_sections: [], root: layout.root } } } });
      await page.goto('/');
      await page.getByRole('button', { name: 'Fit view', exact: true }).click();
      await page.locator(`[data-card-id="${group.id}"]`).getByRole('button', { name: 'Workspace mode', exact: true }).click();
      const workspace = page.getByRole('dialog', { name: 'Conversation studio workspace mode' });
      const sessions = workspace.locator('[data-workspace-section-pane="sessions"]');
      const rows = sessions.locator('.conversation-session-list .workspace-session');
      await expect(rows).toHaveCount(18);
      const row = rows.first();
      const shape = await row.evaluate(element => {
        const name = element.querySelector('strong')!.getBoundingClientRect();
        const time = element.querySelector('time')!.getBoundingClientRect();
        return { height: element.getBoundingClientRect().height, sameLine: Math.abs(name.y - time.y) < 2, timeWidth: time.width };
      });
      if (layout.name === 'narrow') {
        expect(shape.height).toBeGreaterThanOrEqual(44);
        expect(shape.sameLine).toBe(false);
      } else {
        expect(shape.height).toBeLessThanOrEqual(34);
        if (layout.name === 'wide') expect(shape.sameLine).toBe(true);
        else expect(shape.timeWidth).toBeLessThanOrEqual(1);
      }
      const toolbar = sessions.locator('.conversation-session-toolbar');
      const before = await toolbar.boundingBox();
      const scroll = sessions.locator('.conversation-session-list .conversation-sidebar-scroll');
      await scroll.evaluate(element => { element.scrollTop = element.scrollHeight; });
      expect(await toolbar.boundingBox()).toEqual(before);
      await expect(rows.last()).toBeInViewport();
      await scroll.evaluate(element => { element.scrollTop = 0; });
      const bounds = await sessions.evaluate(element => ({ w: element.clientWidth, sw: element.scrollWidth, h: element.clientHeight, sh: element.scrollHeight }));
      expect(bounds.sw).toBeLessThanOrEqual(bounds.w + 1);
      expect(bounds.sh).toBeLessThanOrEqual(bounds.h + 1);
      await sessions.screenshot({ path: `../.outputs/conversation-sessions-${layout.name}.png` });
      const trigger = sessions.getByRole('button', { name: `Switch group: ${groupTitle}` });
      const beforeMenu = await row.boundingBox();
      await trigger.click();
      const menu = sessions.getByRole('dialog', { name: 'Groups', exact: true });
      await expect(menu).toBeInViewport();
      expect(await row.boundingBox()).toEqual(beforeMenu);
      const region = (await sessions.locator('.conversation-session-region').boundingBox())!;
      const popup = (await menu.boundingBox())!;
      expect(popup.y + popup.height).toBeLessThanOrEqual(region.y + region.height + 1);
      await sessions.screenshot({ path: `../.outputs/conversation-groups-${layout.name}.png` });
      await page.keyboard.press('Escape');
      await expect(trigger).toBeFocused();
      await expect(menu).toHaveCount(0);
      await workspace.getByRole('button', { name: 'Back to canvas', exact: true }).click();
      await expect(workspace).toHaveCount(0);
    }
    expect(errors).toEqual([]);
  } finally {
    await request.delete(`/api/nodes/${room.id}`);
    await request.delete(`/api/nodes/${group.id}`);
  }
});
