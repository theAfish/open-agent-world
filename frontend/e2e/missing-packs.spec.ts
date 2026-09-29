import { expect, test } from '@playwright/test';
import type { LibrarySnapshot } from '../src/state/cardLibrary';
import type { PackInstallations } from '../src/types/packs';

// Host UI contract: unavailable inventory, details and environment recovery.
test('missing and failed packs use the checker material and retain recovery controls', async ({ page, request }, testInfo) => {
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation,
    changes: { 'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en' },
  } });
  const snapshot: LibrarySnapshot = await (await request.get('/api/card-library')).json();
  snapshot.packs = {};
  snapshot.available_pack_ids = ['healthy', 'failed'];
  for (const id of ['healthy', 'missing', 'failed', 'pending']) {
    snapshot.packs[id] = { definition: { id, plugin_id: id, name: `${id} pack`, description: 'Pack status preview', cards: [] },
      owned: true, opened: false, opened_at: null };
    snapshot.plugins[id] = { descriptor: { id, name: `${id} pack`, description: '', version: '1.0.0', plugin_api_version: '1.0' },
      installed: id !== 'missing' && id !== 'pending', enabled: true };
  }
  const installations: PackInstallations = { restart_required: true, versions: [
    { id: 'failed', name: 'failed pack', version: '1.0.0', selected: true, loaded: true,
      environment: { state: 'environment_failed', error: 'Fixture environment preparation failed' } },
    { id: 'pending', name: 'pending pack', version: '1.0.0', selected: true, loaded: false, environment: null },
  ] };
  await page.route('**/api/card-library', route => route.fulfill({ json: snapshot }));
  await page.route('**/api/packs', route => route.fulfill({ json: installations }));
  await page.route('**/api/packs/environment/retry', route => {
    installations.versions[0].environment = { state: 'environment_ready', error: null };
    return route.fulfill({ json: installations });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Open Pack and Card Library' }).click();
  const library = page.getByRole('dialog', { name: 'Pack & Card Library' });
  const missing = library.locator('[data-pack-id="missing"]');
  const failed = library.locator('[data-pack-id="failed"]');
  await expect(missing).toHaveAttribute('data-pack-issue', 'missing');
  await expect(failed).toHaveAttribute('data-pack-issue', 'error');
  for (const pack of [missing, failed]) {
    await expect(pack.locator('.pack-print')).toHaveCSS('background-image', /conic-gradient/);
    await expect(pack.locator('.pack-foil')).toHaveCSS('display', 'none');
  }
  await expect(library.locator('[data-pack-id="healthy"]')).not.toHaveAttribute('data-pack-issue');
  await expect(library.locator('[data-pack-id="pending"]')).not.toHaveAttribute('data-pack-issue');
  await missing.getByRole('button').hover();
  await expect(missing.locator('.pack-touch-area')).toHaveAttribute('data-tilting');
  await page.screenshot({ path: testInfo.outputPath('missing-packs.png') });
  await failed.getByRole('button', { name: 'View pack failed pack' }).click();
  await expect(library.getByText('Fixture environment preparation failed')).toBeVisible();
  await expect(library.getByRole('button', { name: 'Uninstall Pack', exact: true })).toBeVisible();
  await library.getByRole('button', { name: 'Retry environment preparation' }).click();
  await expect(library.getByText('Fixture environment preparation failed')).toHaveCount(0);
  await library.getByRole('button', { name: 'Back to packs' }).click();
  await expect(failed).not.toHaveAttribute('data-pack-issue');
  await expect(failed.locator('.pack-print')).not.toHaveCSS('background-image', /conic-gradient/);
});
