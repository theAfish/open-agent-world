import { expect, test } from '@playwright/test';

test('General auto joins agents and uploaded images open a preview after reload', async ({ page, request }) => {
  await page.setViewportSize({ width: 1800, height: 1100 });
  const room = await (await request.post('/api/nodes', { data: { type: 'conversation', name: 'Attachment QA', position: { x: 800, y: 450 } } })).json();
  const agent = await (await request.post('/api/nodes', { data: { type: 'agent', name: 'Attachment Agent', position: { x: 200, y: 300 } } })).json();
  try {
    expect((await request.post('/api/edges', { data: { source: agent.id, target: room.id, relationship: 'participate' } })).ok()).toBeTruthy();
    const summary = await (await request.get(`/api/conversations/${room.id}`)).json();
    expect(summary.sessions[0].participant_ids).toEqual([agent.id]);
    await page.goto('/');
    const card = page.locator(`[data-card-id="${room.id}"]`);
    await card.click();
    await card.getByRole('button', { name: 'Open workspace' }).click();
    const workspace = page.locator(`[data-workspace-node-id="${room.id}"]`);
    await expect(workspace.getByText('1 active participants')).toBeVisible();
    await workspace.locator('input[type=file]').setInputFiles([
      { name: 'chart.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAUAAAAC0CAIAAABqhmJGAAACwUlEQVR4nO3TRQ4YBBQA0X86vDgU1+LuWkOKt+jH3d3leNyBDZnkJe8As5k57fACUfO/FwD/mYEhbE4/skCUgSHMwBA2ZxxdIMrAEGZgCJszjy0QZWAIMzCEzVnHF4gyMIQZGMLm7KcWiDIwhBkYwuacpxeIMjCEGRjC5sAzC0QZGMIMDGFz7rMLRBkYwgwMYXPeiQWiDAxhBoawOf+5BaIMDGEGhrC54PkFogwMYQaGsLnwhQWiDAxhBoawuejFBaIMDGEGhrC5+KUFogwMYQaGsLnk5QWiDAxhBoawufSVBaIMDGEGhrA5eHKBKANDmIEhbC47tUCUgSHMwBA2l7+6QJSBIczAEDZXvLZAlIEhzMAQNle+vkCUgSHMwBA2V72xQJSBIczAEDZXv7lAlIEhzMAQNte8tUCUgSHMwBA21+4CUQaGMAND2Fz39gJRBoYwA0PYXP/OAlEGhjADQ9jc8O4CUQaGMAND2Nz43gJRBoYwA0PYHHp/gSgDQ5iBIWxu+mCBKANDmIEhbG7+cIEoA0OYgSFsbvlogSgDQ5iBIWxu/XiBKANDmIEhbG77ZIEoA0OYgSFsbv90gSgDQ5iBIWzu+GyBKANDmIEhbO78fIEoA0OYgSFs7vpigSgDQ5iBIWzu/nKBKANDmIEhbO75aoEoA0OYgSFs7v16gSgDQ5iBIWzu+2aBKANDmIEhbO7/doEoA0OYgSFsHvhugSgDQ5iBIWwe/H6BKANDmIEhbB76YYEoA0OYgSFsHv5xgSgDQ5iBIWwe+WmBKANDmIEhbB79eYEoA0OYgSFsHvtlgSgDQ5iBIWwe/3WBKANDmIEhbJ74bYEoA0OYgSFsnvx9gSgDQ5iBIWwO/7FAlIEhzMAQNkf+XCDKwBBmYAibo38tEGVgCDMwhM2xvxeIMjCEGRjC5vg/C0QZGMIMDGH/AlUWKM71iqmXAAAAAElFTkSuQmCC', 'base64') },
      { name: 'data.csv', mimeType: 'text/csv', buffer: Buffer.from('name,value\nx,42\n') },
    ]);
    await expect(workspace.getByRole('button', { name: 'Remove attachment data.csv' })).toBeVisible();
    await workspace.getByRole('button', { name: 'Send message', exact: true }).click();
    const image = workspace.getByRole('button', { name: 'Preview chart.png' });
    await expect(image).toBeVisible();
    await expect(image.locator('img')).toHaveJSProperty('naturalWidth', 320);
    await image.click();
    await expect(page.getByRole('dialog', { name: 'Preview chart.png' })).toBeVisible();
    await page.screenshot({ path: '../.outputs/conversation-attachment-preview.png' });
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog', { name: 'Preview chart.png' })).toHaveCount(0);
    const download = await Promise.all([page.waitForEvent('download'), workspace.getByRole('link', { name: 'Download data.csv', exact: true }).click()]);
    expect(download[0].suggestedFilename()).toBe('data.csv');
    await page.reload();
    await expect(workspace).toBeVisible();
    await expect(workspace.getByRole('button', { name: 'Preview chart.png' })).toBeVisible();
    await page.screenshot({ path: '../.outputs/conversation-attachments.png' });
  } finally {
    await request.delete(`/api/nodes/${agent.id}`);
    await request.delete(`/api/nodes/${room.id}`);
  }
});
