import { expect, test } from '@playwright/test';

test.skip(!process.env.OAW_PACK_ARTIFACT, 'Run scripts/run-pack-e2e.mjs with an externally built Greeter artifact');

async function dismissOnboarding(page: import('@playwright/test').Page) {
  const profile = await (await page.request.get('/api/application')).json();
  const saved = profile.values['oaw-onboarding-v1'];
  const status = saved ? JSON.parse(saved).state?.status : 'new';
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Open Pack and Card Library' })).toBeVisible();
  const skip = page.getByRole('button', { name: 'Start Empty', exact: true });
  if (status === 'new') {
    await expect(skip).toBeVisible();
    await skip.click();
    await expect(page.getByLabel('Welcome to Open Agent World', { exact: true })).toBeHidden();
  }
}

test('install local Pack into the production host without a frontend rebuild', async ({ page, request }) => {
  expect((await (await request.get('/api/application')).json()).mode).toBe('production');
  const before = await (await request.get('/api/catalog')).json();
  expect(before.plugins.some((p: { id: string }) => p.id === 'example.greeter')).toBe(false);
  await dismissOnboarding(page);
  await page.getByRole('button', { name: 'Open Pack and Card Library' }).click();
  const library = page.getByRole('dialog', { name: 'Pack & Card Library' });
  await library.getByLabel('Pack file', { exact: true }).setInputFiles(process.env.OAW_PACK_ARTIFACT!);
  await expect(library.getByText('Ready to install', { exact: true })).toBeVisible();
  await library.getByRole('button', { name: 'Install Pack', exact: true }).click();
  await expect(library.getByText('Restart OAW to activate Pack changes.', { exact: true })).toBeVisible();
  const installed = await (await request.get('/api/packs')).json();
  expect(installed.versions[0]).toMatchObject({ id: 'example.greeter', selected: true, loaded: false });
  expect((await (await request.get('/api/catalog')).json()).frontend_modules['example.greeter']).toBeUndefined();
  await page.screenshot({ path: '../.outputs/pack-acceptance/installed.png' });
});

test('restart activates backend, runtime frontend, Python, Library, Deck and a usable Card', async ({ page, request }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const catalog = await (await request.get('/api/catalog')).json();
  expect(catalog.plugins.some((p: { id: string }) => p.id === 'example.greeter')).toBe(true);
  expect(catalog.packs.some((p: { source: string }) => p.source === 'bundled')).toBe(true);
  expect(catalog.frontend_modules['example.greeter'].url).toContain('/versions/0.1.0/frontend/index.js');
  await expect.poll(async () => {
    const state = await (await request.get('/api/packs')).json();
    const item = state.versions.find((p: { id: string }) => p.id === 'example.greeter');
    if (item?.environment?.state === 'environment_failed') throw new Error(item.environment.error);
    return item?.environment?.state;
  }, { timeout: 150000, intervals: [1000, 2000] }).toBe('environment_ready');
  await dismissOnboarding(page);
  await page.getByRole('button', { name: 'Open Pack and Card Library' }).click();
  const library = page.getByRole('dialog', { name: 'Pack & Card Library' });
  const pack = library.locator('[data-pack-id="example.greeter"]');
  await pack.getByRole('button', { name: 'Tear open Greeter', exact: true }).click();
  await expect(pack).toHaveClass(/is-opened/);
  await pack.getByRole('button', { name: 'View cards in Greeter', exact: true }).click();
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
  await page.screenshot({ path: '../.outputs/pack-acceptance/greeter-in-world.png' });
  expect(errors).toEqual([]);
});
