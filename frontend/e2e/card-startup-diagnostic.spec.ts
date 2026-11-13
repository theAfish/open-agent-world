import { expect, test } from '@playwright/test';

test('measure first-load and warm card placement', async ({ page, request }) => {
  test.setTimeout(60_000);
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation,
    changes: { 'oaw.locale': 'en', 'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }) },
  } });
  let library = await (await request.get('/api/card-library')).json();
  for (const id of library.available_pack_ids) library = await (await request.post('/api/card-library/actions', {
    data: { action: 'open_pack', id, expected_revision: library.revision },
  })).json();
  await request.post('/api/card-library/actions', { data: {
    action: 'update_deck', id: library.active_deck_id,
    entries: [{ kind: 'node', id: 'agent' }, { kind: 'node', id: 'text' }], expected_revision: library.revision,
  } });
  const requests = new Map<object, number>();
  page.on('request', r => requests.set(r, Date.now()));
  const ids: string[] = [];
  try {
    const started = Date.now();
    await page.goto('/');
    const tray = page.getByRole('complementary', { name: 'Active card deck' });
    await expect(tray).toBeVisible();
    for (const [index, type] of ['agent', 'text', 'agent'].entries()) {
      await tray.hover();
      const source = tray.locator(`[data-palette-card="${type}"]`);
      await expect(source).toBeVisible();
      const response = page.waitForResponse(r => r.url().endsWith('/api/card-library/nodes') && r.request().method() === 'POST');
      await source.dragTo(page.locator('#oaw-world-map .react-flow__pane'), { targetPosition: { x: 350 + index * 280, y: 250 } });
      const result = await response;
      expect(result.status()).toBe(201);
      const received = Date.now();
      const card = await result.json();
      ids.push(card.id);
      await expect(page.locator(`[data-card-id="${card.id}"]`).first()).toBeVisible();
      console.log('CARD_PLACEMENT_LATENCY', JSON.stringify({ type, index,
        sinceNavigationMs: Date.now() - started, requestMs: received - requests.get(result.request())!,
        responseToVisibleMs: Date.now() - received }));
    }
  } finally {
    for (const id of ids) await request.delete(`/api/nodes/${id}`);
  }
});
