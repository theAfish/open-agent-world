import { expect, test } from '@playwright/test';
import type { LegionSummary } from '../src/types/world';
import type { LibrarySnapshot } from '../src/state/cardLibrary';

test('consent opens required packs, deploys a preset and exposes unavailable dependency details', async ({ page, request }) => {
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en',
    },
  } })).ok()).toBe(true);
  const snapshot = async (): Promise<LibrarySnapshot> => (await request.get('/api/card-library')).json();
  const presets: LegionSummary[] = await (await request.get('/api/legions/presets')).json();
  const preset = presets.find(item => !item.starter && item.compatible)!;
  const initial = await snapshot();
  expect(initial.collection.agent).toBeUndefined();
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  const tray = page.getByRole('complementary', { name: 'Active card deck' });
  await tray.hover();
  await tray.getByRole('tab', { name: /Legions/ }).click();
  const card = tray.getByRole('button', { name: `Place ${preset.name}`, exact: true });
  page.once('dialog', dialog => dialog.dismiss());
  await card.click();
  expect((await snapshot()).revision).toBe(initial.revision);
  expect(await (await request.get('/api/nodes')).json()).toEqual([]);
  let consent = '';
  page.once('dialog', async dialog => { consent = dialog.message(); await dialog.accept(); });
  await card.click();
  await expect.poll(async () => (await snapshot()).collection.agent?.unlocked).toBe(true);
  await expect.poll(async () => (await (await request.get('/api/nodes')).json()).length).toBeGreaterThanOrEqual(preset.node_count);
  expect(consent).toContain('Open these owned packs and continue?');
  for (const id of preset.required_card_ids!) expect((await snapshot()).collection[id]?.unlocked).toBe(true);

  // Inject only an incompatible summary to exercise the diagnostic UI. The
  // consent, collection edits and deployment above use the real backend.
  const issue = "node type 'example.research' requires missing plugin 'example.research-tools'";
  await page.route('**/api/legions/presets', async route => {
    const response = await route.fetch();
    const summaries: LegionSummary[] = await response.json();
    await route.fulfill({ response, json: summaries.map(item => item.id === preset.id
      ? { ...item, compatible: false, issues: [issue, 'blueprint format 9 is not supported'] } : item) });
  });
  await page.reload();
  await tray.hover();
  await tray.getByRole('tab', { name: /Legions/ }).click();
  await tray.getByRole('button', { name: `${preset.name} unavailable`, exact: true }).click();
  const details = page.getByRole('region', { name: 'Dependency details' });
  await expect(details.getByText(issue)).toBeVisible();
  await expect(details.getByText(issue)).toBeInViewport();
  await expect(details.getByText('blueprint format 9 is not supported')).toBeVisible();
  expect(await details.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.screenshot({ path: 'test-results/card-dependencies.png' });
  expect(errors).toEqual([]);
});
