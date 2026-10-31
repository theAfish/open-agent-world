import { expect, test } from '@playwright/test';
import type { LibrarySnapshot } from '../src/state/cardLibrary';

test('switching decks keeps all seven time instruments visible, running and editable', async ({ page, request }) => {
  test.setTimeout(60000);
  await page.setViewportSize({ width: 1720, height: 1100 });
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en',
      'oaw-canvas-viewport-v1': null, 'oaw-node-surfaces-v1': null,
    },
  } })).ok()).toBe(true);
  const ids: Record<string, string> = {};
  const deckNames = ['Time switch A', 'Time switch B'];
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const positions = { digital: [400, 160], clock: [100, 160], calendar: [860, 160], countdown: [400, 360],
    stopwatch: [400, 550], pomodoro: [118, 470], counter: [860, 580] };
  try {
    for (const [kind, [x, y]] of Object.entries(positions)) {
      const response = await request.post('/api/nodes', { data: { type: `tools.time.${kind}`, position: { x, y } } });
      expect(response.ok()).toBe(true);
      ids[kind] = (await response.json()).id;
    }
    await page.goto('/');
    const surface = (kind: string) => page.locator(`.world-card[data-card-id="${ids[kind]}"]`);
    for (const kind of Object.keys(ids)) await expect(surface(kind).locator('.time-loading')).toHaveCount(0);
    await expect(surface('digital').locator('.time-tubes')).toBeVisible();
    const tray = page.getByRole('complementary', { name: 'Active card deck' });
    for (const name of deckNames) {
      await tray.getByRole('button', { name: 'Create a new card deck' }).click();
      await tray.getByRole('textbox', { name: 'Deck name', exact: true }).fill(name);
      const refreshed = page.waitForResponse(response => response.url().endsWith('/api/catalog') && response.ok());
      await tray.getByRole('button', { name: 'Create deck', exact: true }).click();
      await (await refreshed).finished();
      await expect(tray.getByRole('tab', { name: new RegExp(name) })).toHaveAttribute('aria-selected', 'true');
    }
    const pomodoro = surface('pomodoro'), counter = surface('counter');
    await pomodoro.getByRole('button', { name: 'Start', exact: true }).click();
    await expect(pomodoro.locator('.time-piece')).toHaveAttribute('data-running', 'true');
    const initialTime = await pomodoro.getByLabel('Timer value').textContent();
    await counter.getByRole('button', { name: 'Edit count', exact: true }).click();
    await counter.getByRole('spinbutton', { name: 'Set count', exact: true }).fill('41');
    const contentSelectors = { digital: '.time-tubes', clock: '.time-dial', calendar: '.time-calendar-content',
      countdown: '.time-digits', stopwatch: '.time-digits', pomodoro: '.time-progress-ring', counter: '.time-count-editor input' };
    const contents = await Promise.all(Object.entries(contentSelectors).map(async ([kind, selector]) => {
      const content = surface(kind).locator(selector);
      await expect(content).toBeVisible();
      return { kind, element: (await content.elementHandle())! };
    }));
    for (const name of [...deckNames, ...deckNames]) {
      const refreshed = page.waitForResponse(response => response.url().endsWith('/api/catalog') && response.ok());
      await tray.getByRole('tab', { name: new RegExp(name) }).click();
      await (await refreshed).finished();
      await expect(tray.getByRole('tab', { name: new RegExp(name) })).toHaveAttribute('aria-selected', 'true');
      // Flush catalog subscribers. Retaining each original element detects even a brief loading flash.
      await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
      for (const { kind, element } of contents) expect(await element.evaluate(node => node.isConnected), kind).toBe(true);
      await expect(counter.getByRole('spinbutton', { name: 'Set count', exact: true })).toHaveValue('41');
      await expect(pomodoro.locator('.time-piece')).toHaveAttribute('data-running', 'true');
    }
    await expect(pomodoro.getByLabel('Timer value')).not.toHaveText(initialTime!);
    expect(errors).toEqual([]);
  } finally {
    for (const id of Object.values(ids)) await request.delete(`/api/nodes/${id}`);
    let library: LibrarySnapshot = await (await request.get('/api/card-library')).json();
    for (const deck of library.decks.filter(item => deckNames.includes(item.name))) {
      const response = await request.post('/api/card-library/actions', { data: {
        action: 'delete_deck', id: deck.id, expected_revision: library.revision,
      } });
      if (response.ok()) library = await response.json();
    }
  }
});
