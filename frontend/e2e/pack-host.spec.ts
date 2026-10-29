import { expect, test } from '@playwright/test';

test.skip(!process.env.OAW_PACK_ARTIFACT && process.env.OAW_PACK_SOURCE !== 'store-official',
  'Run scripts/run-pack-e2e.mjs with an externally built Greeter artifact or --store-official');

async function dismissOnboarding(page: import('@playwright/test').Page) {
  const profile = await (await page.request.get('/api/application')).json();
  const saved = profile.values['oaw-onboarding-v1'];
  const status = saved ? JSON.parse(saved).state?.status : 'new';
  await page.goto('/');
  const skip = page.getByRole('button', { name: 'Start blank', exact: true });
  if (status === 'new') {
    await expect(skip).toBeVisible();
    await skip.click();
    await expect(page.getByLabel('Welcome to Open Agent World', { exact: true })).toBeHidden();
  }
  await expect(page.getByRole('button', { name: 'Open Pack and Card Library' })).toBeVisible();
}

test('install local Pack into the production host without a frontend rebuild', async ({ page, request }) => {
  expect((await (await request.get('/api/application')).json()).mode).toBe('production');
  const before = await (await request.get('/api/catalog')).json();
  expect(before.plugins.some((p: { id: string }) => p.id === 'example.greeter')).toBe(false);
  await dismissOnboarding(page);
  await page.getByRole('button', { name: 'Open Pack and Card Library' }).click();
  const library = page.getByRole('dialog', { name: 'Pack & Card Library' });
  await library.getByLabel('Pack file', { exact: true }).setInputFiles(process.env.OAW_PACK_ARTIFACT!);
  await expect(library.getByRole('region', { name: 'Install Pack', exact: true })).toBeVisible();
  await library.getByRole('button', { name: 'Install Pack', exact: true }).click();
  const pendingPack = library.locator('[data-pack-id="installed:example.greeter"]');
  await expect(pendingPack.getByText('Restart required', { exact: true })).toBeVisible();
  await expect(library.getByRole('region', { name: 'Install Pack', exact: true })).toHaveCount(0);
  await pendingPack.getByRole('button', { name: 'View pack Greeter', exact: true }).click();
  await expect(library.getByRole('button', { name: 'Uninstall Pack', exact: true })).toBeVisible();
  await library.getByRole('button', { name: 'Back to packs', exact: true }).click();
  const installed = await (await request.get('/api/packs')).json();
  expect(installed.versions[0]).toMatchObject({ id: 'example.greeter', selected: true, loaded: false });
  expect((await (await request.get('/api/catalog')).json()).frontend_modules['example.greeter']).toBeUndefined();
  await page.screenshot({ path: `${process.env.OAW_PACK_E2E_DATA_ROOT}/installed.png` });
});

test('install remote Pack through Store and the existing installer', async ({ page, request }) => {
  const browserRequests: string[] = [];
  page.on('request', request => browserRequests.push(request.url()));
  expect((await (await request.get('/api/packs')).json()).versions).toEqual([]);
  await dismissOnboarding(page);
  await page.getByRole('button', { name: 'Open Pack and Card Library' }).click();
  const library = page.getByRole('dialog', { name: 'Pack & Card Library' });
  await library.getByRole('button', { name: 'Store', exact: true }).click();
  const pack = library.locator('[data-store-pack-id="example.greeter"]');
  await expect(pack).toBeVisible();
  if (process.env.OAW_PACK_SOURCE === 'store-fake') {
    await library.getByRole('button', { name: 'Load more', exact: true }).click();
    await expect(library.locator('[data-store-pack-id]')).toHaveCount(23);
  }
  await library.getByLabel('Search packs', { exact: true }).fill('does-not-exist');
  await expect(library.getByText('No matching packs', { exact: true })).toBeVisible();
  await library.getByLabel('Search packs', { exact: true }).fill('Greeter');
  await expect(pack).toBeVisible();
  await page.screenshot({ path: `${process.env.OAW_PACK_E2E_DATA_ROOT}/store-catalog.png` });
  await pack.getByRole('button', { name: 'View Greeter details', exact: true }).click();
  const detail = library.getByRole('article', { name: 'Pack details', exact: true });
  const metadata = await (await request.get(`/api/store/packs/example.greeter/versions/${process.env.OAW_PACK_VERSION}`)).json();
  expect(metadata.manifest).toMatchObject({ owner: 'example.greeter', packs: ['example.greeter'], node_types: ['example.greeter.card'] });
  await expect(detail.getByText('OAW compatibility', { exact: true })).toBeVisible();
  await page.screenshot({ path: `${process.env.OAW_PACK_E2E_DATA_ROOT}/store-detail.png` });
  await detail.getByRole('button', { name: 'Get', exact: true }).click();
  await expect(detail.getByText('Installed · Restart required', { exact: true })).toBeVisible({ timeout: 120000 });
  await expect(detail.getByRole('button', { name: 'Installed', exact: true })).toBeDisabled();
  const installed = await (await request.get('/api/packs')).json();
  expect(installed.versions[0]).toMatchObject({ id: 'example.greeter', version: process.env.OAW_PACK_VERSION, selected: true, loaded: false, digest: process.env.OAW_PACK_EXPECTED_SHA });
  expect((await (await request.get('/api/catalog')).json()).frontend_modules['example.greeter']).toBeUndefined();
  expect(browserRequests.every(url => !url.includes('/v1/packs'))).toBe(true);
  await page.screenshot({ path: `${process.env.OAW_PACK_E2E_DATA_ROOT}/installed.png` });
});

test('restart activates backend, runtime frontend, Library, Deck and a usable Card', async ({ page, request }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const catalog = await (await request.get('/api/catalog')).json();
  expect(catalog.plugins.some((p: { id: string }) => p.id === 'example.greeter')).toBe(true);
  expect(catalog.packs.some((p: { source: string }) => p.source === 'bundled')).toBe(true);
  expect(catalog.frontend_modules['example.greeter'].url).toContain(`/versions/${process.env.OAW_PACK_VERSION}/frontend/index.js`);
  const installed = await (await request.get('/api/packs')).json();
  expect(installed.versions.find((p: { id: string }) => p.id === 'example.greeter')).toMatchObject({
    selected: true, loaded: true, version: process.env.OAW_PACK_VERSION,
  });
  await dismissOnboarding(page);
  await page.getByRole('button', { name: 'Open Pack and Card Library' }).click();
  const library = page.getByRole('dialog', { name: 'Pack & Card Library' });
  const pack = library.locator('[data-pack-id="example.greeter"]');
  if (process.env.OAW_PACK_SOURCE !== 'local') {
    await library.getByRole('button', { name: 'Store', exact: true }).click();
    const remote = library.locator('[data-store-pack-id="example.greeter"]');
    await expect(remote.getByRole('button', { name: 'Installed', exact: true })).toBeDisabled();
    await expect(remote.getByText('Installed · Restart required', { exact: true })).toHaveCount(0);
  }
  await library.getByRole('button', { name: /^Packs/ }).click();
  await expect(library.locator('[data-pack-id="installed:example.greeter"]')).toHaveCount(0);
  await pack.getByRole('button', { name: 'Tear open Greeter', exact: true }).click();
  await expect(pack).toHaveClass(/is-opened/);
  await pack.getByRole('button', { name: 'View pack Greeter', exact: true }).click();
  await library.getByRole('button', { name: 'View cards', exact: true }).click();
  await library.getByRole('button', { name: 'Add Greeter to deck', exact: true }).click();
  await library.getByRole('button', { name: 'Close Library', exact: true }).click();
  const tray = page.getByRole('complementary', { name: 'Active card deck' });
  await tray.hover();
  const place = tray.getByRole('button', { name: 'Place Greeter', exact: true });
  await expect(place).toBeVisible();
  await place.dragTo(page.locator('.react-flow__pane').first(), { targetPosition: { x: 620, y: 320 } });
  await page.getByRole('article', { name: 'Greeter Greeter', exact: true }).click();
  await expect(page.getByLabel('Greeter name', { exact: true })).toBeVisible();
  await page.getByLabel('Greeter name', { exact: true }).fill('Pack Store');
  await page.getByRole('button', { name: 'Greet', exact: true }).click();
  await expect(page.locator('[data-greeter-output]')).toHaveText('Hello, Pack Store!');
  const nodes = await (await request.get('/api/nodes')).json();
  expect(nodes.find((n: { type: string }) => n.type === 'example.greeter.card').config.greeting).toBe('Hello, Pack Store!');
  await page.reload();
  await expect(page.locator('[data-greeter-output]')).toHaveText('Hello, Pack Store!');
  await page.screenshot({ path: `${process.env.OAW_PACK_E2E_DATA_ROOT}/greeter-in-world.png` });
  expect(errors).toEqual([]);
});

test('Store offline leaves Packs Cards Deck and World usable', async ({ page, request }) => {
  await dismissOnboarding(page);
  await expect(page.locator('[data-greeter-output]')).toHaveText('Hello, Pack Store!');
  await page.getByRole('button', { name: 'Open Pack and Card Library' }).click();
  const library = page.getByRole('dialog', { name: 'Pack & Card Library' });
  await library.getByRole('button', { name: 'Store', exact: true }).click();
  // Real failed connections include OS connect time and the shared GET retries.
  await expect(library.getByText('Store unavailable', { exact: true })).toBeVisible({ timeout: 45000 });
  await library.getByRole('button', { name: 'Retry', exact: true }).click();
  await expect(library.getByText('Loading…', { exact: true })).toBeVisible();
  await expect(library.getByText('Store unavailable', { exact: true })).toBeVisible({ timeout: 45000 });
  await page.screenshot({ path: `${process.env.OAW_PACK_E2E_DATA_ROOT}/store-offline.png` });
  await library.getByRole('button', { name: /^Packs/ }).click();
  await expect(library.locator('[data-pack-id="example.greeter"]')).toBeVisible();
  await expect(library.getByRole('button', { name: 'Install Pack from File...', exact: true })).toBeVisible();
  await library.getByRole('button', { name: /^Cards/ }).click();
  await expect(library.getByRole('textbox', { name: 'Search cards', exact: true })).toBeVisible();
  await library.getByRole('button', { name: 'Close Library', exact: true }).click();
  const tray = page.getByRole('complementary', { name: 'Active card deck' });
  await tray.hover();
  await expect(tray.getByRole('button', { name: 'Place Greeter', exact: true })).toBeVisible();
  await page.getByLabel('Greeter name', { exact: true }).fill('Offline World');
  await page.getByRole('button', { name: 'Greet', exact: true }).click();
  await expect(page.locator('[data-greeter-output]')).toHaveText('Hello, Offline World!');
  await page.reload();
  await expect(page.locator('[data-greeter-output]')).toHaveText('Hello, Offline World!');
  await expect(page.locator('[data-greeter-output]')).toBeVisible();
  await expect(page.locator('.node-inspector-content').filter({ has: page.locator('[data-greeter-output]') })).toHaveCSS('opacity', '1');
  await page.screenshot({ path: `${process.env.OAW_PACK_E2E_DATA_ROOT}/greeter-offline.png`, animations: 'disabled' });
  expect((await request.get('/api/health')).ok()).toBe(true);
});
